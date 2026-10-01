# Torque: instructions for coding agents

This file is the single source of instructions for any coding agent working on this
repository (Claude Code, Codex, Gemini CLI, Cursor or others). Tool-specific files only
point here.

## What this repo is

Torque is a Python command-line tool plus packaged workflows that give a coding assistant a
private, local workspace per Salesforce client or internal initiative. This repository is the
public, generic engine. It must never contain a firm's, client's or person's material.

## Map

| Path | Contents |
|---|---|
| `src/torque/` | The engine: workspace and records, change records, gate and connected mode, approvals, upgrades, demo |
| `src/torque/data/` | Generated bundle that `torque workspace init` and `upgrade` install into a workspace (do not edit by hand) |
| `packages/` | Bundled Salesforce tools reached through `torque` (revert, qa, browser, logs, advisory, lesson, meeting); provenance in `packages/provenance.json` |
| `workflows/` | Workflow catalogue and the adapter check |
| `.claude/`, `.agents/skills/`, `workflows/catalogue.json` | Source of the recipes, rules, worker roles, skills and catalogue; bundled into `src/torque/data/` |
| `tests/`, `scripts/` | Offline tests and release checks |
| `docs/`, `examples/` | User guides, one validation record per release, design records |

## Agent assets, by tool

- Workflow recipes: `torque workflows list` and `torque workflows show NAME` work for any agent. Claude Code also reads them as `.claude/commands/`.
- Skills: `.agents/skills/` (read by agents that support the shared skills folder); workspaces get the same skills in `.claude/skills/`. List: `docs/skills.md`.
- Standing rules: `.claude/rules/*.md` are plain Markdown; any agent should read them before client work.
- Worker roles: `.claude/agents/*.md` are plain role descriptions any agent can follow.
- Guardrails: the build-only and connected-mode gate runs as a Claude Code hook today; other agents get the same records and workflows but not the hook.

## Commands

```sh
python -m pip install -e '.[dev]'
python scripts/test-offline.py -q          # full offline suite and package self-tests
python workflows/sync_adapters.py --check  # catalogue matches the recipe files
python scripts/sync-workflows.py --check   # src/torque/data matches the sources
python scripts/check-provenance.py         # carried-over package files match their records
python -m build && python scripts/check-distribution.py dist/*.whl --sdist dist/*.tar.gz
python scripts/smoke-installed.py --require-wheel   # run from a clean venv with the wheel installed
```

## Done means

All of the commands above pass locally, the change has tests, CI is green on Linux, macOS and
Windows, and user-facing behavior changes are reflected in `README.md`, the relevant `docs/`
page and `CHANGELOG.md`. Report observed, asserted, unknown and untested outcomes separately.

## Rules

- No firm, client or person names, credentials, org mappings or record data in tracked files; `tests/test_public_hygiene.py` enforces part of this.
- Use the user's existing Salesforce access and authorization; never select another client or org silently. Live org and browser tests run only when explicitly in scope.
- Edit recipes, rules and roles in `.claude/`, skills in `.agents/skills/`, and the catalogue in `workflows/catalogue.json`; then run `python scripts/sync-workflows.py` to rebuild `src/torque/data/`.
- Prefer integrating a mature existing tool over building one; no feature-count targets or extra user ceremonies.
- Retain material adoption decisions in `docs/research-adoption.md`.
- No commit, push or release is implied by a local change; the maintainer decides.
