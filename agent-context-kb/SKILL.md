---
name: agent-kb
description: Initialize, maintain, validate, and compile a lightweight `.agent-kb/` project knowledge base for coding agents. Use when Codex needs to create the KB scaffold, add the AGENTS.md runtime protocol, record durable project knowledge, check KB links/routes, or merge inbox notes into stable topic documents.
---

# Agent KB

Use this skill to manage a repository-local project knowledge base for coding agents.
The runtime knowledge lives in `.agent-kb/`; this skill only handles setup,
maintenance, validation, and inbox cleanup.

## Quick Start

Run the bundled script from the skill directory:

```bash
python3 scripts/agent_kb.py init --root /path/to/repo
python3 scripts/agent_kb.py validate --root /path/to/repo
python3 scripts/agent_kb.py note --root /path/to/repo --title "Auth session note" --target decisions/active/auth-storage.md --body "Durable fact."
python3 scripts/agent_kb.py compile --root /path/to/repo
```

Use `--root .` when working in the target repository.

## Workflow

1. Use `init` to create `.agent-kb/`, starter topic documents, `inbox/`,
   `start.md`, `map.md`, and the short `AGENTS.md` runtime protocol.
2. Use `validate` after changes to check required files, map routes, Markdown
   links, inbox shape, and whether stable topic files are reachable.
3. Use `note` when the task produced durable project knowledge but the right
   stable topic file is not obvious or should be reviewed later.
4. Use `compile` to merge inbox notes that name an existing `Suggested target`.
   Notes with `unsure`, missing targets, or invalid targets remain in `inbox/`.

## Knowledge Rules

Record durable knowledge only: architecture decisions, module boundaries,
debugging conclusions, workflows, conventions, integration constraints, and
pitfalls future agents should avoid.

Do not record ordinary progress logs, one-off chat summaries, secrets, local
credentials, or details that are obvious from reading the code.

## Map Format

`.agent-kb/map.md` must keep this table under `## Task Routing`:

```md
| Task Pattern | Read First | Also Consider |
| --- | --- | --- |
| Local dev / test / deploy | workflows/local-dev.md | workflows/deploy.md |
```

Paths are relative to `.agent-kb/`. Keep each stable topic document reachable
from `map.md` or from another reachable document's Markdown links.
