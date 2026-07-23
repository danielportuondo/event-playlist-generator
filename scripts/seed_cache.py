"""Pre-seed .resolution_cache.json to cut Spotify search quota usage.

Three steps, runnable independently:

  uv run python scripts/seed_cache.py harvest --rounds 1
      Generate candidates via Gemini across all presets x vibe variations and
      accumulate distinct title|artist keys into .harvest_keys.json.
      Spends zero Spotify calls.

  uv run python scripts/seed_cache.py seed --dataset dataset.csv
      Exact-match harvested keys against an offline Spotify tracks dataset
      (track_id/artists/track_name/popularity columns) and write matches into
      .resolution_cache.json. Spends zero Spotify calls.

  uv run python scripts/seed_cache.py resolve --budget 100
      Resolve still-unmatched harvested keys via the Spotify search API using
      the app token, stopping at --budget calls. Run only after quota unlock.
"""

import argparse
import ast
import csv
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.arc.presets import PRESETS
from app.config import load_config
from app.llm.candidates import generate_candidates
from app.llm.prompt import Brief
from app.spotify.auth import get_app_access_token
from app.spotify.client import (
    RESOLUTION_CACHE_PATH,
    TrackMatch,
    _cache_key,
    calls_today,
    search_track,
)

HARVEST_PATH = Path(".harvest_keys.json")

# Empty vibe covers the default-visitor case; the rest widen genre coverage.
VIBES = [
    "",
    "latin and reggaeton",
    "hip-hop and r&b",
    "indie rock",
    "throwback classics",
    "afrobeats and amapiano",
    "country",
    "k-pop",
    "jazz and soul",
    "electronic and dance",
    "classic rock",
    "current pop hits",
]


def _load(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def harvest(rounds: int) -> None:
    config = load_config()
    keys = _load(HARVEST_PATH)
    start = len(keys)
    for round_num in range(rounds):
        for template in PRESETS.values():
            for vibe in VIBES:
                brief = Brief(vibe=vibe)
                candidates = None
                for attempt in range(3):
                    try:
                        candidates = generate_candidates(
                            brief, template, template.default_duration_min, config
                        )
                        break
                    except Exception as exc:  # noqa: BLE001 — retry any genai failure
                        # Free tier allows 15 requests/min; one generation bursts
                        # up to ~5 parallel phase calls, so back off a full window.
                        print(
                            f"  {template.id} / '{vibe}' attempt {attempt + 1}: {exc}"
                        )
                        time.sleep(65)
                if candidates is None:
                    print(f"  {template.id} / '{vibe}': FAILED after retries")
                    continue
                time.sleep(20)
                new = 0
                for track in candidates:
                    key = _cache_key(track.title, track.artist)
                    if key not in keys:
                        keys[key] = {"title": track.title, "artist": track.artist}
                        new += 1
                HARVEST_PATH.write_text(json.dumps(keys, indent=0))
                print(
                    f"  round {round_num + 1} {template.id} / '{vibe}': "
                    f"{len(candidates)} candidates, {new} new keys"
                )
    print(f"harvest done: {len(keys)} keys total ({len(keys) - start} new)")


def _parse_row(row: dict) -> tuple[str, list[str], str, int] | None:
    """Normalize a dataset row to (title, artist_variants, track_id, popularity).

    Handles three known formats: HF 114k (track_name/artists ';'-joined),
    Kaggle 1M (track_name/artist_name), Kaggle 1.2M (name/artists list-string).
    """
    title = (row.get("track_name") or row.get("name") or "").strip()
    track_id = (row.get("track_id") or row.get("id") or "").strip()
    if not title or not track_id:
        return None
    popularity = int(row.get("popularity") or 0)

    if row.get("artist_name"):
        return title, [row["artist_name"].strip()], track_id, popularity
    artists = (row.get("artists") or "").strip()
    if not artists:
        return None
    if artists.startswith("["):
        try:
            names = [str(n).strip() for n in ast.literal_eval(artists)]
        except (ValueError, SyntaxError):
            return None
        variants = [n for n in names if n]
        if len(variants) > 1:
            variants.append(", ".join(variants))
    else:
        variants = [artists.split(";")[0].strip(), artists]
    return title, variants, track_id, popularity


def seed(dataset_paths: list[Path]) -> None:
    keys = _load(HARVEST_PATH)
    cache = _load(RESOLUTION_CACHE_PATH)
    pending = {k: v for k, v in keys.items() if k not in cache}
    print(f"{len(keys)} harvested keys, {len(pending)} not yet cached")

    for dataset_path in dataset_paths:
        if not pending:
            break
        # Index only pending keys; on duplicates keep highest popularity.
        index: dict[str, dict] = {}
        with dataset_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                parsed = _parse_row(row)
                if parsed is None:
                    continue
                title, variants, track_id, popularity = parsed
                for artist_variant in dict.fromkeys(variants):
                    key = _cache_key(title, artist_variant)
                    if key not in pending:
                        continue
                    if key not in index or index[key]["popularity"] < popularity:
                        index[key] = {
                            "track_id": track_id,
                            "title": title,
                            "artist": variants[0],
                            "popularity": popularity,
                        }

        for key, row in index.items():
            match = TrackMatch(
                spotify_id=row["track_id"],
                spotify_uri=f"spotify:track:{row['track_id']}",
                resolved_title=row["title"],
                resolved_artist=row["artist"],
                popularity=row["popularity"],
            )
            cache[key] = asdict(match)
            del pending[key]
        print(f"{dataset_path.name}: seeded {len(index)}; {len(pending)} keys left")

    RESOLUTION_CACHE_PATH.write_text(json.dumps(cache))
    print(f"done; {len(pending)} keys still unresolved")


def resolve(budget: int) -> None:
    config = load_config()
    keys = _load(HARVEST_PATH)
    cache = _load(RESOLUTION_CACHE_PATH)
    pending = [(k, v) for k, v in keys.items() if k not in cache]
    print(f"{len(pending)} unresolved keys, {calls_today()} Spotify calls used today")

    token = get_app_access_token(config)
    if token is None:
        print("no app token available (SPOTIFY_CLIENT_SECRET missing?)")
        return

    spent = misses = resolved = 0
    for key, entry in pending:
        if spent >= budget:
            break
        try:
            match = search_track(entry["title"], entry["artist"], token)
        except Exception as exc:  # noqa: BLE001 — abort cleanly on any API failure
            print(f"aborting on API error (likely quota): {exc}")
            break
        spent += 1
        if match is None:
            misses += 1
            continue
        cache[key] = asdict(match)
        resolved += 1
        if resolved % 20 == 0:
            RESOLUTION_CACHE_PATH.write_text(json.dumps(cache))

    RESOLUTION_CACHE_PATH.write_text(json.dumps(cache))
    print(
        f"resolved {resolved}, no-hit {misses}, calls spent {spent}; "
        f"{len(pending) - resolved - misses} keys remaining"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_harvest = sub.add_parser("harvest")
    p_harvest.add_argument("--rounds", type=int, default=1)
    p_seed = sub.add_parser("seed")
    p_seed.add_argument("--dataset", type=Path, required=True, nargs="+")
    p_resolve = sub.add_parser("resolve")
    p_resolve.add_argument("--budget", type=int, default=100)
    args = parser.parse_args()

    if args.command == "harvest":
        harvest(args.rounds)
    elif args.command == "seed":
        seed(args.dataset)
    else:
        resolve(args.budget)


if __name__ == "__main__":
    main()
