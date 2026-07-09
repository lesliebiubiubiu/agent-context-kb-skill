# CLAUDE.md

See [AGENTS.md](AGENTS.md) for repository structure, commands, and conventions.

## Project Knowledge Base

`.agent-kb/` is this project's memory for coding agents: read it before working,
update it when your work changes what a future agent needs to know.

At task start, before broad code search:
1. Read `.agent-kb/start.md`.
2. Read `.agent-kb/routes.yaml`; pick the routes relevant to this task.
3. Read those KB docs, then search code for gaps.

Before finishing:
- Did this work change what a future agent needs to know? If yes, update the
  relevant topic file, or add a note in `.agent-kb/inbox/` when the right place
  is unclear. `start.md` defines what belongs in the KB.
