# Repository Guidelines

## Project Structure & Module Organization

This repository contains a Codex skill for lightweight agent knowledge bases.

Current layout:

- `skills/agent-context-kb/` - the Codex skill, including `SKILL.md`, UI metadata, and scripts.
- `skills/agent-context-kb/scripts/agent_kb.py` - the CLI for KB init, validation, notes, and compile.
- `evals/` - Release 2 eval bundles, runners, and summary result JSON files.
- `AGENTS.md` - contributor and agent guidance for this repository.

## Project Knowledge Base

Use `.agent-kb/` before broad code search when planning, building, debugging, or reviewing.
1. Read `.agent-kb/start.md`.
2. Read `.agent-kb/routes.yaml`; pick only relevant routes.
3. Read those KB docs, then search code for gaps.

After work, update a topic or `.agent-kb/inbox/` only for reusable project knowledge.
Do not store progress logs, chat summaries, secrets, or obvious code facts.

## Build, Test, and Development Commands

There is no package manager or build system. Use the bundled script and skill
validator for checks:

- `python3 -m py_compile skills/agent-context-kb/scripts/agent_kb.py skills/agent-context-kb/scripts/smoke_test.py` - check Python syntax.
- `python3 skills/agent-context-kb/scripts/smoke_test.py` - run temporary-directory CLI smoke, upgrade, and edge checks.
- `python3 skills/agent-context-kb/scripts/agent_kb.py validate --root .` - validate this repo's KB.
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
