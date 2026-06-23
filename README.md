# Agent Context KB

A lightweight, agent-facing knowledge base that gives coding agents a durable,
shared memory of your project. They read *before* working and
update *after*, so context survives across sessions and across
different agents.

## The Problem

A coding agent starts cold. Each new session — and each different agent — opens
your project knowing nothing about it: how the system is built and why it's
shaped that way, how the pieces fit together, the decisions already settled, the
approaches already ruled out, and the bugs already chased down.

You can write some of that down, but as a project grows the durable knowledge
piles up faster than any single document can hold. An agent is then stuck between
two bad options: read everything (slow, noisy, and mostly irrelevant to the task
at hand), or read nothing and re-derive — re-asking what you've already answered
and re-breaking what you already fixed. Often the hardest part is simply knowing
*where to start*.

**Agent Context KB** organizes that knowledge as a small, navigable graph rather
than a flat pile. Each kind of task has an entry point that routes the agent to
just the few pieces relevant to it, and each piece links to related ones. So
every agent, in every session, lands in the right place, reads only what matters,
and writes what it learns back.

## Highlights

- **Reads little, reads the right thing.** A task pulls in just the knowledge
  relevant to it — not the whole pile.
- **Stays lean as it grows.** The knowledge base is actively kept compact, so it
  stays trustworthy instead of decaying into a dump nobody reads.
- **Improves as the work happens.** Agents write durable findings back, so the
  project's memory compounds over time instead of evaporating when a session
  ends.

## Quickstart

Install the skill into your agent with the [`skills`](https://github.com/vercel-labs/skills)
CLI:

```bash
npx skills add lesliebiubiubiu/agent-context-kb-skill
```

Then, in any repo, ask your agent to set it up — for example:

> "Initialize an agent-kb knowledge base in this repo."

That triggers the skill, which scaffolds `.agent-kb/` with `start.md`,
`routes.yaml`, starter topic files, and a short runtime protocol in the main
agent instruction file telling agents how to use it.

You can also run the bundled CLI directly from the installed skill directory:

```bash
python3 scripts/agent_kb.py init --root /path/to/repo
```

## Using the skill

You mostly drive this in plain language — your agent picks the right command. A few typical prompts:

- *"Set up an agent-kb knowledge base in this repo."*
- *"Update the agent-kb with what we just figured out."*
- *"Record why we picked Postgres over Mongo here."*
- *"Trim kb"*
- *"Show me the kb stats."*

Those map onto the commands you'll touch directly:

| Command | When to use it |
|---|---|
| `init` | Set up the knowledge base in a repo |
| `note` | Record a durable fact worth keeping |
| `trim` | Tidy the knowledge base when it's grown bloated |
| `stats` | See how the knowledge base is being used |

The rest run automatically as the agent maintains the knowledge base — `validate`
(check structure after edits), `compile` (file captured notes into the right
place), and `upgrade` (refresh the protocol) — so you rarely call them yourself.

### What goes in the KB

Durable knowledge only: architecture decisions, module boundaries, debugging
conclusions, conventions, integration constraints, and pitfalls future agents
should avoid. **Not** progress logs, chat summaries, secrets, or anything obvious
from reading the code.

For the full protocol — route format, the compaction loop, and the versioning &
privacy modes — see
[`skills/agent-context-kb/SKILL.md`](skills/agent-context-kb/SKILL.md).

## License

[MIT](LICENSE)
