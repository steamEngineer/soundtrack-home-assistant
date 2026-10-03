# Contributing

This repository is the Soundtrack custom integration for Home Assistant 2026.4 and newer. It needs Python 3.14.2 or newer.

Agents working in this tree should follow [AGENTS.md](AGENTS.md).

## Setup

```bash
uv venv --python 3.14 .venv
uv pip install -r requirements-dev.txt
.venv/bin/pre-commit install
```

The local player UI is described in the Development section of [README.md](README.md).

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

Open pull requests against `main`. Describe what changed and why. Leave passwords, tokens, and anything from `config/.storage` out of the diff, the description, and the logs.

## Dependency updates

[Renovate](renovate.json) opens update pull requests. A weekly GitHub Action (`.github/workflows/renovate.yml`) runs it every Monday at 06:00 UTC. You can also run that workflow by hand.

The default `GITHUB_TOKEN` can open those pull requests. GitHub does not start other workflows on pull requests that token opens, so CI stays quiet on them until a person retriggers it. To have CI run automatically, add a repository secret named `RENOVATE_TOKEN`: a fine-grained personal access token for this repo with read and write on contents, issues, and pull requests, plus the workflows permission.

Ruff's pip pin and its pre-commit hook are grouped into one pull request. A major Home Assistant bump waits on the dependency dashboard until someone approves it, because that moves the oldest supported release.

## Reporting issues

Use the bug or feature form. Soundtrack billing, speaker hardware, and the Soundtrack app belong with Soundtrack, not here. Security reports go through [private vulnerability reporting](SECURITY.md).
