# Repository Guidelines

## Project Structure & Module Organization

This repository contains a Codex skill for lightweight agent knowledge bases,
plus the design notes that define v0 behavior.

Current layout:

- `agent-kb/` - the Codex skill, including `SKILL.md`, UI metadata, and scripts.
- `agent-kb/scripts/agent_kb.py` - the CLI for KB init, validation, notes, and compile.
- `.agent-kb/` - this repository's own project knowledge base scaffold.
- `docs/superpowers/specs/` - dated design specifications and planning documents.
- `AGENTS.md` - contributor and agent guidance for this repository.

Use date-prefixed filenames for new specs, for example
`docs/superpowers/specs/2026-06-16-agent-kb-design.md`.

## Build, Test, and Development Commands

There is no package manager or build system. Use the bundled script and skill
validator for checks:

- `python3 -m py_compile agent-kb/scripts/agent_kb.py` - check Python syntax.
- `python3 agent-kb/scripts/agent_kb.py validate --root .` - validate this repo's KB.
- `python3 /Users/lsl/.codex/skills/.system/skill-creator/scripts/quick_validate.py agent-kb` - validate skill metadata.
- `git status --short` - review tracked and untracked changes.

Do not add new tooling unless generated artifacts or automated validation require it.

## Coding Style & Naming Conventions

Write Markdown in clear prose with short sections, descriptive headings, and
examples that use real repository paths. Use US spelling for new contributor-facing text.

For specification files, use lowercase, hyphen-separated names with a leading ISO date:
`YYYY-MM-DD-topic-name.md`.

## Testing Guidelines

No dedicated test suite is configured. For script changes, run `py_compile`,
`validate --root .`, and a temporary-directory smoke test for `init`, `note`, and `compile`.

Keep future test files close to the code they validate.

## Commit & Pull Request Guidelines

This repository has no established history yet. Prefer concise imperative commit
messages such as `Implement agent KB skill`.

Pull requests should include a summary, affected paths, and manual verification.

## Agent-Specific Instructions

Keep changes narrow. Do not turn this repository into a broad knowledge dump.


## Project Knowledge Base

Use `.agent-kb/` as the project knowledge base.

Before non-trivial coding:
1. Read `.agent-kb/start.md`.
2. Read `.agent-kb/map.md`.
3. Read only KB documents relevant to the current task.

After coding:
- Update `.agent-kb/` when the work creates or changes reusable project knowledge.
- Prefer the relevant topic file.
- Use `.agent-kb/inbox/` when the right location is unclear.
- Do not write ordinary progress logs or one-off chat summaries into KB.
