# Agent Context KB

**English** | [中文](README.zh-CN.md)

A lightweight, agent-facing, routed knowledge base that gives coding agents a
durable, shared memory of your project. They read *before* working and
update *after*, so context survives across sessions and across
different agents.

## The Problem

A coding agent starts cold. Each new session — and each different agent — opens
your project knowing nothing about it: how the system is shaped and why, what's
been decided, what's been ruled out, what's already been painfully debugged.

The usual fixes all collapse as a project grows. Stuff everything into
`AGENTS.md`/`CLAUDE.md`, and the whole file gets injected into every session — it can only
grow until the signal drowns and agents start ignoring it. Write design docs,
and the agent doesn't know which of thirty files answers today's question — and
half of them are stale. Paste context by hand, and *you* are the memory,
repeating yourself to every new session and every new tool. And the knowledge
that hurts most to lose — the bug you chased for two hours, the approach that
looked right and wasn't — was never in the docs to begin with; it evaporated
with the session that just ended.

**Agent Context KB** organizes project memory as a small, navigable graph
instead of a flat pile. Each kind of task has an entry point that routes the
agent to just the few pieces relevant to it; agents write what they learn back
along the way; and the whole thing is actively kept compact, so it stays
trusted instead of rotting. It's plain files in your repo, so every agent —
Claude Code, Codex, whatever comes next — shares the same memory.

## Highlights

- **Reads little, reads the right thing.** A task pulls in just the knowledge
  relevant to it — not the whole pile.
- **Stays lean as it grows.** The knowledge base is actively kept compact, so it
  stays trustworthy instead of decaying into a dump nobody reads.
- **Leave footprints along the way.** As agents work, they write durable
  findings back into the KB, so hard-won project context stays available for
  future sessions and future agents.

## Does it actually work?

Project memory only pays off if a whole chain holds: the knowledge has to
**reach** the agent, get **read** before work starts, actually **help** the
work, and **not rot** as it's maintained. Most KB tools are never checked on
any of those links. This one is measured on each — full methodology and numbers
in [`evals/REPORT.md`](evals/REPORT.md).

- **It gets read.** In our instrumented dogfooding sessions, read compliance —
  did the agent open the KB before its first source exploration or edit — rose
  from 33% to 69.6% after we fixed how the protocol reaches the agent.
- **It helps.** Day to day, agents resume from recorded plans and cite
  recorded decisions instead of re-deriving them — the difference is felt most
  when a new session picks up exactly where the last one stopped. Beyond that
  lived experience, answers to project-knowledge questions pass an LLM judge
  calibrated against deliberately broken answers.
- **It doesn't rot.** Every trim is followed by a non-regression pass:
  questions answered correctly before the cleanup must still pass after it.

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

> [!TIP]
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

> [!TIP]
> By default the KB keeps itself out of your project's git history: it lives in
> its own nested repo (`.agent-kb/.git`) and `.agent-kb/` is gitignored, so your
> project stays clean and the memory stays yours. If you'd rather check the KB
> into the project so it travels with the repo, ask for a shared setup instead
> (`init --shared`) — the modes are detailed in
> [`SKILL.md`](skills/agent-context-kb/SKILL.md).

## Using the skill

![An agent routing through the KB to finish tasks](https://github.com/lesliebiubiubiu/agent-context-kb-skill/releases/download/readme-assets/demo.gif)

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

> [!TIP]
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
