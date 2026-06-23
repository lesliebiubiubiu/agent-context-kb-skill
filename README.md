# Agent Context KB

A lightweight, agent-facing knowledge base for your repository. It gives coding
agents a small, durable memory (`.agent-kb/`) they read *before* working and
update *after* — so context survives across sessions and across different agents.

## The Problem

Coding agents start cold. Every new session — and every different agent or tool —
begins with no memory of:

- the architecture decisions you already made,
- the conventions the project follows,
- the bugs you already chased down, and why,
- or even where to start reading.

That knowledge ends up scattered across chat logs and human docs that agents
don't reliably read. So they re-ask, re-derive, and re-break things you already
solved.

**Agent Context KB** fixes this with a tiny, structured knowledge base that lives
in your repo. `start.md` points the agent in; `routes.yaml` sends it to the few
docs that matter for the task at hand; topic files hold the durable facts. Any
agent, any session, reads the same memory first — and writes durable findings
back.

## Highlights

(Not the full list — just the parts that matter most.)

- **Routing, not a dump.** `routes.yaml` maps a task to the few docs worth
  reading, so agents read *little* and read the *right* thing.
- **Frictionless capture.** `note` drops a durable fact into an inbox;
  `compile` merges it into the correct topic file later.
- **Stays lean.** `trim` diagnoses bloat against soft budgets and drives an
  agent-led compaction loop — it flags redundancy, never auto-truncates real
  knowledge.
- **Observability.** `stats` shows which commands run, which docs change most,
  and which are heaviest right now.
- **Versioning your way.** Run it as shared team memory, or as a *personal
  nested repo* that is versioned and restorable yet never enters your project's
  branches or PRs.
- **Zero dependencies.** Pure Python standard library; the CLI even parses its
  own small YAML subset.

## Quickstart

Install the skill into your agent with the [`skills`](https://github.com/vercel-labs/skills)
CLI:

```bash
npx skills add lesliebiubiubiu/agent-context-kb-skill
```

Then, in any repo, ask your agent to set it up — for example:

> "Initialize an agent-kb knowledge base in this repo."

That triggers the skill, which scaffolds `.agent-kb/` with `start.md`,
`routes.yaml`, starter topic files, and a short `AGENTS.md` protocol telling
agents how to use it.

You can also run the bundled CLI directly from the installed skill directory:

```bash
python3 scripts/agent_kb.py init --root /path/to/repo
```

## Using the skill

Once installed, your agent invokes the skill whenever it needs to set up or
maintain the KB. The core commands:

| Command | When to use it |
|---|---|
| `init` | Create the `.agent-kb/` scaffold in a repo |
| `upgrade` | Refresh the generated protocol / add missing scaffold files |
| `validate` | Check links, routes, and structure after changes |
| `note` / `compile` | Capture a durable fact, then merge it into a topic file |
| `trim` | Diagnose bloat and run the compaction loop |
| `stats` | See command usage and per-file churn |

Run any command with `--root .` from the target repo.

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
