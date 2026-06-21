---
name: agent-kb
description: Initialize, upgrade, maintain, validate, and compile a lightweight `.agent-kb/` project knowledge base for coding agents. Use when Codex needs to create the KB scaffold, refresh the AGENTS.md runtime protocol, record durable project knowledge, check KB links/routes, or merge inbox notes into stable topic documents.
---

# Agent KB

Use this skill to manage a repository-local project knowledge base for coding agents.
The runtime knowledge lives in `.agent-kb/`; this skill only handles setup,
maintenance, validation, and inbox cleanup.

Treat `.agent-kb/` as an agent-facing index and distilled knowledge layer. It
should summarize durable facts from existing human docs when useful, not replace
those docs or move their full contents.

## Quick Start

Run the bundled script from the skill directory:

```bash
python3 scripts/agent_kb.py init --root /path/to/repo
python3 scripts/agent_kb.py upgrade --root /path/to/repo
python3 scripts/agent_kb.py validate --root /path/to/repo
python3 scripts/agent_kb.py note --root /path/to/repo --title "Auth session note" --target decisions/active/auth-storage.md --body "Durable fact."
python3 scripts/agent_kb.py compile --root /path/to/repo
python3 scripts/agent_kb.py trim --root /path/to/repo
python3 scripts/agent_kb.py trim --root /path/to/repo --write
```

Use `--root .` when working in the target repository.

## Workflow

1. Use `init` to create `.agent-kb/`, starter topic documents, `inbox/`,
   `plans/current.md`, `start.md`, `routes.yaml`, `map.md`, and the short
   `AGENTS.md` runtime protocol.
2. Use `upgrade` to refresh generated protocol text conservatively. It updates
   `AGENTS.md`, creates missing scaffold files, and leaves existing `start.md`,
   `routes.yaml`, `map.md`, or `plans/current.md` for manual review unless
   explicit write flags are passed.
3. Use `validate` after changes to check required files, map routes, Markdown
   links, inbox shape, placeholder text, and whether stable topic files are
   reachable.
4. Use `note` when the task produced durable project knowledge but the right
   stable topic file is not obvious or should be reviewed later.
5. Use `compile` to merge inbox notes that name an existing `Suggested target`.
   Notes with `unsure`, missing targets, or invalid targets remain in `inbox/`.
6. Use `trim` to diagnose KB bloat and get a compact prompt. Use `trim --write`
   only for deterministic cleanup: deleting empty scaffold topics, pruning
   route references, regenerating `map.md`, and validating the result.

## Knowledge Rules

Record durable knowledge only: architecture decisions, module boundaries,
debugging conclusions, workflows, conventions, integration constraints, and
pitfalls future agents should avoid.

Do not record ordinary progress logs, one-off chat summaries, secrets, local
credentials, or details that are obvious from reading the code. When existing
project docs contain the source material, keep those docs intact and add only a
short agent-oriented summary or link in `.agent-kb/`.

## Lightweight Plans

Use `.agent-kb/plans/current.md` for durable continuity: current focus, major
done milestones, next moves, and open questions that affect future work. Keep it
short. Do not use it as an issue tracker, commit log, or ordinary progress log.

Create module-specific plan files only when a module has its own durable
multi-step direction that will be reused across future tasks.

## Route Format

`.agent-kb/routes.yaml` is the canonical route source. The CLI supports a small
YAML subset directly, so projects do not need PyYAML or any external parser.

```yaml
routes:
  - id: planning
    task: Planning / current focus
    read_first:
      - plans/current.md
    also_consider:
      - decisions/active/project-decisions.md
  - id: local-dev
    task: Local dev / test / deploy
    read_first:
      - workflows/local-dev.md
    also_consider:
      - workflows/deploy.md
```

Paths are relative to `.agent-kb/`. Keep `read_first` to one file and
`also_consider` to at most two files. `.agent-kb/map.md` is a readable Markdown
view of the routes; regenerate or update it when routes change. Keep each stable
topic document reachable from `routes.yaml` or from another reachable document's
Markdown links.
