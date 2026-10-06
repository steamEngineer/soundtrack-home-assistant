# Soundtrack for Home Assistant

Custom integration that controls [Soundtrack](https://www.soundtrackyourbrand.com/) sound zones. Home Assistant 2026.4+ and Python 3.14.2+ only.

Human contribution steps live in [CONTRIBUTING.md](CONTRIBUTING.md). This file is the project-specific context.

## Layout

- `custom_components/soundtrack/` is the integration Home Assistant loads.
- `tests/` uses `pytest-homeassistant-custom-component` for Home Assistant's own `hass` fixture. `tests/conftest.py` only adds the Soundtrack stand-in.
- `dev/mock_soundtrack.py` is the local GraphQL stand-in.
- `config/` is a local Home Assistant config. It is gitignored and may hold a live session.

## Commands

```bash
uv venv --python 3.14 .venv
uv pip install -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest
.venv/bin/pre-commit run --all-files
```

## Easy to get wrong

- The Soundtrack password is used once in the config flow and is not stored. Tokens live in the config entry. A rejected refresh token starts reauth. It is not a retry loop.
- Do not log access tokens, refresh tokens, or passwords.
- The production API is `https://api.soundtrackyourbrand.com/v2`. `SOUNDTRACK_API_URL` overrides it for the lab and is read at call time through `api_url()`. Leave it unset when the lab should hit production.
- One `asyncio.Lock` serializes GraphQL so two refreshes cannot rotate the same token.
- Track position comes from `playback.progress`, including while paused. Home Assistant extrapolates that position while the zone is playing. If `playback.current` starts at least a second after `nowPlaying`, the newer track wins. A smaller gap is millisecond precision on `playback.current` while `nowPlaying` is the track that is actually playing.
- Discover children come from `editorialBrowse(id: "soundtrack:browse:<slug>")`. `browseCategory.playlists` is empty on the live API.
- Album art is a 960px square from `size(width: 960, height: 960)`. Substitute `%w` and `%h` in CDN URLs before requesting them. The hero image is a wide banner, not album art.
- Volume is an integer from 0 to 16. Home Assistant's 0–1 slider maps onto those steps.
- After a playback command, refresh immediately and once more about 4 seconds later.
- Assign and play each retry once. Pass a callable to `_once_more`. A coroutine object can only be awaited one time.
- The config entry is version 1.2. `async_migrate_entry` only bumps the minor version. 1.1 already stores the session fields 1.2 reads.
- Zone devices set `via_device_id` to the hub. `via_device` is deprecated and raises when the call stack is not attributed to the integration.
- There is no `MediaPlayerState.UNAVAILABLE`. An offline zone sets `available` to false.
- Do not pin aiohttp. Home Assistant selects its own version. Tests take Home Assistant from `pytest-homeassistant-custom-component`.
- Ruff's target is Python 3.14, so it formats `except (TypeError, ValueError)` to `except TypeError, ValueError` (PEP 758). Leave that form in place.
- Do not commit `config/.storage`, and do not print tokens from it.

## Branches

`main` is protected.

- Land work through a pull request. Do not push commits straight to `main`.
- Do not force-push `main`, and do not delete it.
- The pull request branch must be up to date with `main`.
- These checks must pass before merge: `ruff and pytest`, `hassfest`, and `HACS`.
- Resolve review threads before merging. An approving review is not required.
- History on `main` stays linear.
- Do not open or comment on GitHub issues or pull requests unless a person asked. When they ask for a change, the pull request is how that change reaches `main`.
