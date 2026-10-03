# Contributing

This repository is the Soundtrack custom integration for Home Assistant 2026.4 and newer. It needs Python 3.14.2 or newer.

Agents working in this tree should follow [AGENTS.md](AGENTS.md).

## Setup

```bash
uv venv --python 3.14 .venv
uv pip install -r requirements-dev.txt
.venv/bin/pre-commit install
```

`dev/mock_soundtrack.py` is a local Soundtrack API for the tests. Run it with `.venv/bin/python dev/mock_soundtrack.py` and point Home Assistant at it with `SOUNDTRACK_API_URL=http://127.0.0.1:43124/`. Sign in as `ada@example.com` with password `soundtrack`.

## Checks

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest
.venv/bin/pre-commit run --all-files
```

Ruff is configured in `pyproject.toml` (line length 100, target Python 3.14). The same hooks run from `.pre-commit-config.yaml`. CI runs Ruff, pytest, [hassfest](https://developers.home-assistant.io/blog/2020/04/16/hassfest/), and HACS validation.

Pytest covers the GraphQL client and, inside Home Assistant, the config flow, reauth, zone setup, playback, favorites, browse, and search.

## Pull requests

`main` is protected. Open a pull request against it. Direct pushes, force-pushes, and deletion of `main` are rejected. The branch must be up to date, and `ruff and pytest`, `hassfest`, and `HACS` must pass. Resolve review threads before merging. An approving review is not required, and history on `main` stays linear.

Describe what changed and why. Leave passwords, tokens, and anything from `config/.storage` out of the diff, the description, and the logs.

## Dependency updates

[Renovate](renovate.json) opens update pull requests. A weekly GitHub Action (`.github/workflows/renovate.yml`) runs it every Monday at 06:00 UTC. You can also run that workflow by hand.

The default `GITHUB_TOKEN` can open those pull requests. GitHub does not start other workflows on pull requests that token opens, so CI stays quiet on them until a person retriggers it. To have CI run automatically, add a repository secret named `RENOVATE_TOKEN`: a fine-grained personal access token for this repo with read and write on contents, issues, and pull requests, plus the workflows permission.

Ruff's pip pin and its pre-commit hook are grouped into one pull request. The oldest Home Assistant this integration supports is declared in `hacs.json`. The test plugin pin is the Home Assistant version the suite runs against.

## Releases

HACS uses GitHub releases for the version it offers. Tag a commit `v1.2.3`, matching `version` in `custom_components/soundtrack/manifest.json`. Pushing that tag publishes the release.

## Reporting issues

Use the bug or feature form. Soundtrack billing, speaker hardware, and the Soundtrack app belong with Soundtrack, not here. Security reports go through [private vulnerability reporting](SECURITY.md).
