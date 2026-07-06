# Bundle: `pressure-distillation`

A private cross-repo bundle that measures the **navigation / compression value** of a
cold-start distilled KB on the external `pressure` fixture — the "pre/post distillation
utility slice" from `.agent-kb/decisions/active/c2-eval-paradigm.md`. It sidesteps the
retired KB-only tautology because every task's ground truth is derivable from the repo's own
`docs/requirements.md` (which the judge sees), not from hidden KB-only knowledge.

## Target

- **Repo:** `~/Desktop/pressure-fixture/repo` (its own git; tag `cold-start`). `repo_path` is
  a machine-local relative path, so this bundle is not portable — adjust it on another
  machine. `bundle.yaml`/`tasks.yaml` are JSON (the runner uses strict `json.loads`; no
  comments).
- **Shared KB mode:** `kb_commit == repo_commit`. Distill with `init --shared` so the KB is
  part of `repo/`'s history and can be pinned.

## Tasks

Three read-only questions answerable from `docs/requirements.md`, each asserting `kb_access`
(did the agent consult the KB — the navigation signal), `no_edit`, and one `judge` check
against a real project decision (photo-mandatory capture, fully-local/offline, data-only
reports). `kb_access` is used instead of a specific `tool_read` path because the distilled
topic layout is agent-produced and varies; consulting the KB at all is the robust signal.

## Pre/post protocol

**Runner constraint (found via `--dry-run`):** the C1 runner requires a KB to exist at the
pinned commit — it errors with `KB path is not a git repository` when `.agent-kb/` is absent.
So the raw `cold-start` commit (no KB) is **not runnable** as a true "no-KB" baseline; that
would need a runner enhancement. `bundle.yaml` therefore ships pinned at `cold-start` as a
**template only** — repin it before running.

Use the runnable interpretation of the slice: **empty scaffold (PRE) vs distilled (POST)**,
both committed shared so both pin cleanly. This isolates the distillation's *content* value.

```bash
cd /Users/lsl/Desktop/agent-context-kb-skill/evals
R=~/Desktop/pressure-fixture/repo

# PRE — commit an empty scaffold, tag it, run:
python3 ../skills/agent-context-kb/scripts/agent_kb.py init --root "$R" --shared
git -C "$R" add -A && git -C "$R" commit -m "empty KB scaffold" && git -C "$R" tag kb-empty
# set bundle.yaml repo_commit == kb_commit == $(git -C "$R" rev-parse kb-empty), then:
python3 run_bundle.py --bundle bundles/pressure-distillation

# POST — distill (fill topics), commit, tag, repin, rerun:
git -C "$R" add -A && git -C "$R" commit -m "distilled KB" && git -C "$R" tag kb-distilled
# set bundle.yaml repo_commit == kb_commit == $(git -C "$R" rev-parse kb-distilled), then:
python3 run_bundle.py --bundle bundles/pressure-distillation
```

Compare paired, per the C1 discipline (`decisions/active/project-decisions.md`: "no assertion
passes pre and fails post"). The `judge` contrast (empty vs filled KB) measures how much the
distilled content improved answers; `kb_access` passing in both just confirms the scaffold was
consulted. Reset the fixture between experiments with
`git -C "$R" reset --hard cold-start && git -C "$R" clean -fdx`.
