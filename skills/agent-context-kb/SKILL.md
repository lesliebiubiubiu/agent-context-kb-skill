---
name: agent-context-kb
description: Initialize, upgrade, maintain, validate, and compile a lightweight `.agent-kb/` project knowledge base for coding agents. Use when Codex needs to create the KB scaffold, refresh the runtime protocol in the main agent instruction file, record durable project knowledge, check KB links/routes, or merge inbox notes into stable topic documents.
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
python3 scripts/agent_kb.py trim --root /path/to/repo --recheck
python3 scripts/agent_kb.py trim --root /path/to/repo --write
python3 scripts/agent_kb.py trim --root /path/to/repo --max-file-chars 12000
python3 scripts/agent_kb.py stats --root /path/to/repo
```

Use `--root .` when working in the target repository.

## Workflow

1. Use `init` to create `.agent-kb/`, starter topic documents, `inbox/`,
   `plans/current.md`, `start.md`, `routes.yaml`, `map.md`, and the short
   runtime protocol in the main agent instruction file (`AGENTS.md` by
   default, or `CLAUDE.md` when it is the existing primary file). By default
   `init` also sets up the personal nested-repo versioning mode (see Versioning
   & privacy); use `--shared` or `--local` to choose another mode.
2. Use `upgrade` to refresh generated protocol text conservatively. It updates
   the main agent instruction file, creates missing scaffold files, and leaves
   existing `start.md`, `routes.yaml`, `map.md`, or `plans/current.md` for
   manual review unless explicit write flags are passed.
3. Use `validate` after changes to check required files, map routes, Markdown
   links, inbox shape, placeholder text, and whether stable topic files are
   reachable.
4. Use `note` when the task produced durable project knowledge but the right
   stable topic file is not obvious or should be reviewed later.
5. Use `compile` to merge inbox notes that name an existing `Suggested target`.
   Notes with `unsure`, missing targets, or invalid targets remain in `inbox/`.
6. Use `trim` to diagnose KB bloat. It first prints the per-file char/line
   breakdown of every stable doc counted toward the budget (plus the total and
   soft budget), so you never need to re-measure with `wc`. It then names the
   concrete cleanup candidates and structural signals (with actual counts), so
   the output localizes the problem; tune the budgets with `--max-file-lines`,
   `--max-file-chars`, `--max-total-chars`, and `--max-inbox-notes`. A file is
   flagged oversize when it exceeds **either** the per-file char budget **or**
   the line budget, but only **char** overage (real bulk that can't be gamed by
   joining short lines) makes a file **major**; line-only overage stays
   **minor**, an advisory — so the agent compacts content instead of shaving
   lines to clear the signal. Severity is graded: char overage under ~10% (and
   any line-only overage) is **minor — optional** (`Safe to stop here`); ~10%+
   char overage is **major** and worth a compaction pass. Size is a *proxy, not
   a target*: it flags files that may be repeating themselves or holding dead
   detail. Compact by cutting redundant or stale **information** (duplicate
   facts, superseded detail, routine Change Log churn) — shrinking bytes without
   removing information is not progress, and the reader is a future agent that
   must still locate and trust each fact. **Stop once what remains is genuine,
   non-redundant durable content — even if the file is still over the signal** (a
   large file of real, distinct facts is a correct end state, like `lean (above
   soft budget)`). The total-char budget is likewise a **soft** signal, never on
   its own a reason to compact. So `trim` distinguishes clean states (`KB is already
   lean`, `lean (above soft budget)`) from `minor — optional` and from `compact
   recommended` (major char oversize or inbox backlog).
   To keep the compaction loop quiet, the full agent compact prompt and the
   soft-budget note print only on first detection within a loop (or with
   `--verbose`); later rounds show a one-line pointer instead. Use `--recheck`
   to fold the loop's `validate` step into the same command, so each round is
   one command (`trim --recheck`) instead of two. Use `trim --write` only for
   deterministic cleanup: deleting pristine empty scaffold topics, pruning route
   references, regenerating `map.md`, and validating. Semantic compacting stays
   with the agent: `trim` emits a self-converging prompt (rewrite within
   headings, then `upgrade --write-map` -> `validate` -> rerun `trim` until lean
   — stopping once structure is clean, even if still above the soft budget). An
   emptied husk (content gone but `Change Log` grown) is only flagged for manual
   deletion, never removed automatically.
7. Use `stats` to observe usage. Every CLI run appends one JSONL line to
   `.agent-kb/.log/events.jsonl`; `init` and `upgrade` write a KB-local
   `.agent-kb/.gitignore` so the log stays out of git. `stats` draws ASCII bar
   charts of command frequency (with failure counts), per-file change churn
   from git history (read from the nested `.agent-kb/` repo when the KB is
   versioned on its own — see Versioning & privacy — otherwise from the parent
   repo), and the largest current KB files by character count, so you
   can see at a glance which commands run most, which KB documents change most,
   and which carry the most weight right now (not just historically). After
   running `stats`, paste its full output verbatim inside a fenced code block in
   your reply to the user. Do not paraphrase or summarize the bar charts into
   prose — the ASCII charts are the deliverable; reproduce them exactly. You may
   add commentary after the block. Each event
   also records the run's parameters (`args`, with free text like note bodies
   redacted) and optional per-command `metrics` (e.g. validate error/warning
   counts, trim deleted/husk counts, compile merged/unresolved); `stats`
   surfaces the latest outcome per command.
   Logging is best-effort and never blocks a command. Reads of KB documents are
   done by the agent's own tools, so the CLI cannot observe them; only command
   runs and git-tracked writes are recorded.

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

## Versioning & privacy

The KB is just files under `.agent-kb/`, so you choose how it is versioned. The
KB-local `.gitignore` always keeps `.log/` out of version control; the rest is up
to the mode:

- **Personal nested repo (default):** `init` sets this up automatically — it adds
  `.agent-kb/` to the project repo's `.gitignore`, runs `git init` inside
  `.agent-kb/`, and makes an initial commit, so the KB is its own repository
  (optionally add a private remote for backup). This keeps a full, restorable
  history while the KB never enters the project's branches, `main`, or PRs — a
  per-developer knowledge base, since habits differ and a shared KB tends to grow
  large and hard to manage. Commit later changes with `git -C .agent-kb ...`. Git
  has no per-file privacy and merges carry tracked files, so this nested-repo
  split is what makes "versioned but never merged" actually work. `stats` churn
  reads this nested repo automatically when `.agent-kb/.git` exists. A remote is
  optional: everything (history, rollback) works locally; the remote only adds
  backup and cross-machine sync. (If git is missing or unconfigured, `init`
  prints the manual commands instead of failing.)
- **Shared:** run `init --shared`; it records the mode and leaves the KB tracked
  by the project repo, so it travels with branches and PRs as team memory. Use
  this when the whole team deliberately wants one shared knowledge base.
- **Local-only:** run `init --local`; it gitignores `.agent-kb/` but does not
  create a nested repo, so the KB is never versioned. Simplest, but there is no
  history to roll back to.

The chosen mode is recorded in `.agent-kb/.kb-meta.yaml` alongside the KB schema
version, so future tooling can tell how a KB was set up.
