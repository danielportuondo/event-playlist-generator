"""M3 acceptance script: log in to Spotify once (browser opens on first run,
cached/refreshed silently after), then resolve a sample list of title/artist
pairs to Spotify track URIs and report the match rate. Needs SPOTIFY_CLIENT_ID
and SPOTIFY_REDIRECT_URI set in .env (see .env.example)."""

from app.arc.models import CandidateTrack
from app.config import load_config
from app.spotify import auth, client

SAMPLE_TRACKS = [
    ("Landslide", "Fleetwood Mac"),
    ("Dreams", "Fleetwood Mac"),
    ("Landslide", "Fleetwood Mac"),  # deliberate duplicate: exercises dedup-on-id
    ("Blinding Lights", "The Weeknd"),
    ("Bohemian Rhapsody", "Queen"),
    ("Redbone", "Childish Gambino"),
    ("Feels Like Summer", "Childish Gambino"),
    ("Holocene", "Bon Iver"),
    ("Skinny Love", "Bon Iver"),
    ("Nuvole Bianche", "Ludovico Einaudi"),
    ("Xyzzy Nonexistent Track 42", "Totally Fake Artist"),  # deliberate miss
    ("A Cover That Doesnt Exist Xk29", "Nobody Real Band"),  # deliberate miss
]


def _placeholder_candidate(title: str, artist: str) -> CandidateTrack:
    return CandidateTrack(
        title=title, artist=artist, energy=50, tempo=100, valence=50, rationale="sample"
    )


def main() -> None:
    config = load_config()
    access_token = auth.get_valid_access_token(config)

    me = client.get_me(access_token)
    print(f"Logged in as {me.get('display_name', me.get('id'))}\n")

    tracks = [_placeholder_candidate(title, artist) for title, artist in SAMPLE_TRACKS]
    stats = client.resolve_candidates(tracks, access_token)

    for track in tracks:
        if track.spotify_uri:
            print(f"  [OK]      {track.title} — {track.artist}")
            print(f"            -> {track.resolved_title} — {track.resolved_artist} ({track.spotify_uri})")
        else:
            print(f"  [MISSING] {track.title} — {track.artist}")

    print(f"\nResolved {stats.resolved}/{stats.total} ({stats.rate:.0%})")


if __name__ == "__main__":
    main()
