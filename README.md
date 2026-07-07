# Agent Context KB

A lightweight, agent-facing knowledge base that gives coding agents a durable,
shared memory of your project. They read *before* working and
update *after*, so context survives across sessions and across
different agents.

<!-- demo: record with asciinema + agg, save as .github/demo.gif, then:
![An agent routing through the KB to answer a project question](.github/demo.gif)
-->

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

## Does it actually work?

Most KB tools are designed to help and never checked. This one is evaluated —
the full methodology and numbers are in [`evals/REPORT.md`](evals/REPORT.md).

- **Agents really read it.** In our own dogfooding, agents opened the KB
  before starting work in about 70% of recent sessions — up from under 40%
  when we first started measuring.
- **Cleanups don't lose knowledge.** Every time the KB is compacted, we
  re-check that answers which were correct before are still correct after —
  so tidying never silently drops something.
- **The checks catch real problems.** Our own eval once flagged a project
  question the KB was answering wrong; we fixed the KB and the same check
  went green. The safety net isn't decorative.

## How we measure it

Each eval pins an exact repo commit *and* KB commit, so a run is
reproducible rather than "it worked when I tried it." Tasks are read-only
project-knowledge questions with two kinds of checks:

- **Deterministic behavior checks**, read straight from the agent's
  tool-call trace — did it open the right KB file, did it avoid editing
  anything.
- **Semantic checks** for the answer itself, scored by an LLM judge that's
  calibrated against deliberately broken answers, so a rubber-stamp judge
  fails the negative control.

Runs are tracked per harness rather than pooled, and the whole bundle —
tasks, pins, and runner — lives in [`evals/`](evals/).

## Quickstart

Install the skill into your agent with the [`skills`](https://github.com/vercel-labs/skills)
CLI:

```bash
npx skills add lesliebiubiubiu/agent-context-kb-skill
```

Then, in any repo, ask your agent to set it up — for example:

> "Initialize an agent-kb knowledge base in this repo."

That triggers the skill, which scaffolds `.agent-kb/` and adds a short runtime
protocol to the main agent instruction file telling agents how to use it:

```
.agent-kb/
├── start.md         # entry point — agents read this first
├── routes.yaml      # task type → which docs to read
├── map.md           # one-page overview of every topic
├── plans/           # current focus — new sessions resume, not restart
├── inbox/           # quick notes, later compiled into topics
├── architecture/    # how the system is shaped, and why
├── decisions/       # what's settled, what was ruled out
├── debugging/       # bugs already chased down
├── workflows/       # how to build, test, deploy
└── conventions/     # code style and project idioms
```

The agent then offers a one-time distillation pass — mining your README, docs,
and git history for the durable facts that seed the topic files.

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
should avoid.

> [!NOTE]
> Not progress logs, chat summaries, secrets, or anything obvious from reading
> the code — the KB stays useful precisely because it stays small.

For the full protocol — route format, the compaction loop, and the versioning &
privacy modes — see
[`skills/agent-context-kb/SKILL.md`](skills/agent-context-kb/SKILL.md).

## Changelog

Versioned via git tags — see
[GitHub Releases](https://github.com/lesliebiubiubiu/agent-context-kb-skill/releases)
for what changed in each release.

## License

[MIT](LICENSE)
