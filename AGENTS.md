# Torque: instructions for coding agents

This file is the single source of instructions for any coding agent working on this
repository (Claude Code, Codex, Gemini CLI, Cursor or others). Tool-specific files only
point here.

## What this repo is

Torque is a Python command-line tool plus packaged workflows that give a coding assistant a
private, local workspace per Salesforce client or internal initiative. This repository is the
public, generic engine. Private firm, client and personal material belongs outside it.
Necessary public attribution, license notices and dependency provenance are retained.

## Map

| Path | Contents |
|---|---|
| `src/torque/` | The engine: workspace and records, change records, gate and connected mode, approvals, upgrades, demo |
| `src/torque/data/` | Generated bundle that `torque workspace init` and `upgrade` install into a workspace (do not edit by hand) |
| `packages/` | Bundled Salesforce tools reached through `torque` (revert, qa, browser, logs, advisory, lesson, meeting); provenance in `packages/provenance.json` |
| `workflows/` | Source recipes (`*.md`) and the catalogue (`catalogue.json`); `scripts/sync-workflows.py` generates every copy |
| `.claude/commands/` | Generated workflow adapters; edit `workflows/*.md`, then regenerate |
| `.claude/rules/`, `.claude/agents/`, `.agents/skills/` | Source standing rules, worker roles and skills; bundled with the adapters into `src/torque/data/` |
| `lessons/` | Source platform lessons, one Markdown file each (format: `docs/lessons.md`); bundled into `src/torque/data/lessons/` |
| `floor/AGENTS-floor.md` | Source of the Torque block every host loads from a workspace's `AGENTS.md` (at most 60 lines; `docs/workspace-upgrades.md`); bundled into `src/torque/data/floor/` |
| `tests/`, `scripts/` | Offline tests and release checks |
| `docs/`, `examples/` | User guides, one validation record per release, design records |

## Agent assets, by tool

- Workflow recipes: `torque workflows list` and `torque workflows show NAME` work for any agent. Claude Code also reads them as `.claude/commands/`.
- Skills: `.agents/skills/` (read by agents that support the shared skills folder); workspaces get the same skills in `.claude/skills/`. List: `docs/skills.md`.
- Standing rules: `.claude/rules/*.md` are plain Markdown; any agent should read them before client work.
- Worker roles: `.claude/agents/*.md` are plain role descriptions any agent can follow.
- Antigravity (`agy`) reads `.agents/` only. A workspace gets the rules, recipes (as skills and slash commands) and worker roles there in its format, derived at `init` and `upgrade` by `src/torque/antigravity.py`; nothing for it is generated in this repository. See `docs/workspace-upgrades.md`.
- Guardrails: the gate (`src/torque/gate.py`) runs as a Claude Code hook; `src/torque/gate_antigravity.py` runs the same gate as an Antigravity hook. `src/torque/hosts.py` is the registry of hosts: `torque launch` starts the host a workspace names, or `--host claude|antigravity`. Connected mode under Antigravity is tested offline only. Other agents get the same records and workflows but not the hook. See `docs/ai-access.md` and `docs/hosts.md`.

## Commands

```sh
python -m pip install -e '.[dev]'
python scripts/test-offline.py -q          # full offline suite and package self-tests
python scripts/sync-workflows.py --check   # .claude/commands and src/torque/data match their sources
python scripts/check-provenance.py         # carried-over package files match their records
python -m build && python scripts/check-distribution.py dist/*.whl --sdist dist/*.tar.gz
python scripts/smoke-installed.py --require-wheel   # run from a clean venv with the wheel installed
```

## Done means

All of the commands above pass locally, the change has tests, CI is green on Linux, macOS and
Windows, and user-facing behavior changes are reflected in `README.md`, the relevant `docs/`
page and `CHANGELOG.md`. Report observed, asserted, unknown and untested outcomes separately.

## Rules

- No private firm, client or personal material, credentials, org mappings or real record data in tracked files. Necessary public authorship, LICENSE/NOTICE text, dependency attribution and provenance are allowed; retain required notices. Fictional fixtures use reserved example domains. See `CONTRIBUTING.md`; `tests/test_public_hygiene.py` enforces part of this policy.
- Use the user's existing Salesforce access and authorization; never select another client or org silently. Live org and browser tests run only when explicitly in scope.
- Edit recipes in `workflows/*.md` and the catalogue in `workflows/catalogue.json`; run `python scripts/sync-workflows.py`, which regenerates `.claude/commands/` and rebuilds `src/torque/data/`; `--check` fails on a hand-edited, missing or stray copy. Edit standing rules in `.claude/rules/`, worker roles in `.claude/agents/`, skills in `.agents/skills/`, platform lessons in `lessons/` and the workspace block in `floor/AGENTS-floor.md` (raise the version in its first line when its text changes), then rebuild the bundle. Do not edit generated adapters or bundled files by hand.
- Prefer integrating a mature existing tool over building one; no feature-count targets or extra user ceremonies.
- Retain material adoption decisions in `docs/research-adoption.md`.
- No commit, push or release is implied by a local change; the maintainer decides.
