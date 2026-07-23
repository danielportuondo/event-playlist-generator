# References

Sources, APIs, and tooling used to build this project.

## Project spec

- [`CLAUDE_CODE_HANDOFF.MD`](CLAUDE_CODE_HANDOFF.MD) — the driving spec: scope, energy-arc model, and milestones M1 (arc core) through M5 (save-to-Spotify). The arc sequencer is the differentiator; everything else is plumbing.

## External APIs

- [Spotify Web API](https://developer.spotify.com/documentation/web-api) — track search (candidate resolution), playlist creation, adding tracks, `/me` profile. Adapted to the Feb 2026 API migration (`/playlists/{id}/tracks` → `/playlists/{id}/items`). The `audio-features` and `recommendations` endpoints are deliberately **not** used — they are unavailable to newly created apps, and the design routes around them (LLM estimates instead).
- [Spotify OAuth](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow) — Authorization Code + PKCE for the owner login; Client Credentials app token as the visitor-mode fallback.
- [Google Gemini](https://ai.google.dev/gemini-api/docs) via the `google-genai` SDK — LLM candidate generation with per-track energy/tempo/valence estimates and rationales. (The spec originally called for the Anthropic API; Gemini is what shipped.)

## Python dependencies (`pyproject.toml`)

Runtime:

- [FastAPI](https://fastapi.tiangolo.com/) — web framework (pydantic comes transitively for models/validation)
- [httpx](https://www.python-httpx.org/) — async HTTP client for Spotify calls
- [uvicorn](https://www.uvicorn.org/) — ASGI dev server
- [google-genai](https://pypi.org/project/google-genai/) — Gemini SDK
- [python-dotenv](https://pypi.org/project/python-dotenv/) — local secrets via `.env`

Dev:

- [pytest](https://docs.pytest.org/) — test suite

## Tooling

- [uv](https://docs.astral.sh/uv/) — environment and dependency management
- [ruff](https://docs.astral.sh/ruff/) — lint and format
- [Claude Code](https://claude.com/claude-code) — build agent for the whole project
- [Playwright MCP](https://github.com/microsoft/playwright-mcp) — browser-driven UI verification during development

## Operational notes (Spotify dev mode)

- Dev-mode app: 5-user cap; observed search quota ≈230 calls/day (single measurement, unpublished by Spotify).
- Daily call-budget guard lives at `app/main.py:29-33` (visitor ceiling 130, owner ceiling 150).
- Resolution cache cuts a generation from ~25 Spotify calls (cold) to ~8 (repeat).
