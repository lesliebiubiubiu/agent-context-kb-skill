# Repository Guidelines

## Project Structure & Module Organization

This repository contains a Codex skill for lightweight agent knowledge bases.

Current layout:

- `skills/agent-context-kb/` - the Codex skill, including `SKILL.md`, UI metadata, and scripts.
- `skills/agent-context-kb/scripts/agent_kb.py` - the CLI for KB init, validation, notes, and compile.
- `AGENTS.md` - contributor and agent guidance for this repository.

## Project Knowledge Base

`.agent-kb/` is the project knowledge base for coding agents — a distilled index
of architecture, decisions, conventions, and pitfalls.

When you need to understand how this codebase works — to plan, build, debug,
review, or answer a question about it — start here, before opening a broad code
search:

1. Read `.agent-kb/start.md`.
2. Read `.agent-kb/routes.yaml` (the source of truth; `.agent-kb/map.md` is a
   readable view) and pick only the routes relevant to your task.
3. Read the KB documents those routes point to. Use open-ended code search for
   what the KB does not cover.

After the task, only when it created or changed reusable project knowledge:
- Update the relevant topic file (use `.agent-kb/inbox/` if the target is unclear).
- Do not write progress logs or one-off chat summaries into the KB.

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
