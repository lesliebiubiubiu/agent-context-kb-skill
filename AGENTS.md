# Repository Guidelines

## Project Structure & Module Organization

This repository contains a Codex skill for lightweight agent knowledge bases.

Current layout:

- `agent-context-kb/` - the Codex skill, including `SKILL.md`, UI metadata, and scripts.
- `agent-context-kb/scripts/agent_kb.py` - the CLI for KB init, validation, notes, and compile.
- `AGENTS.md` - contributor and agent guidance for this repository.

## Build, Test, and Development Commands

There is no package manager or build system. Use the bundled script and skill
validator for checks:

- `python3 -m py_compile agent-context-kb/scripts/agent_kb.py agent-context-kb/scripts/smoke_test.py` - check Python syntax.
- `python3 agent-context-kb/scripts/smoke_test.py` - run temporary-directory CLI smoke, upgrade, and edge checks.
- `python3 agent-context-kb/scripts/agent_kb.py validate --root .` - validate this repo's KB.
- `git status --short` - review tracked and untracked changes.

Do not add new tooling unless generated artifacts or automated validation require it.

## Coding Style & Naming Conventions

Write Markdown in clear prose with short sections, descriptive headings, and
examples that use real repository paths. Use US spelling for new contributor-facing text.

## Testing Guidelines

No dedicated test suite is configured. For script changes, run `py_compile`,
`validate --root .`, and the temporary-directory smoke test.

Keep future test files close to the code they validate.

## Commit & Pull Request Guidelines

Use concise type-prefixed commit messages such as `fix: repair KB validation`
or `test: add KB smoke checks`.

Pull requests should include a summary, affected paths, and manual verification.

## Agent-Specific Instructions

Keep changes narrow. Do not turn this repository into a broad knowledge dump.


## Project Knowledge Base

Use `.agent-kb/` as the project knowledge base.
Treat it as an agent-facing index and distilled knowledge layer; do not replace
human docs with KB entries.

Before non-trivial coding:
1. Read `.agent-kb/start.md`.
2. Read `.agent-kb/routes.yaml` as the source of truth; use `.agent-kb/map.md`
   only as a readable view if helpful.
3. Read only KB documents relevant to the current task.

After coding:
- Update `.agent-kb/` only when the work creates or changes reusable project knowledge.
- Prefer the relevant topic file.
- Use `.agent-kb/inbox/` when the right location is unclear.
- Do not write ordinary progress logs or one-off chat summaries into KB.
