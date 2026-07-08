# Evaluation Report

This is the public summary of how Agent Context KB is evaluated and what the
results show. The bundles and result files in this directory are **worked
examples** of the harness, pinned to this repository so anyone can inspect or
re-run them. The same harness also runs against real production repositories we
maintain; those results are summarized here rather than published, because the
artifacts reference private code.

## What we measure

Three questions, in decreasing order of how much they matter:

1. **Do agents actually read the KB before working?** A KB nobody opens is
   dead weight, however well-organized.
2. **Does maintenance lose knowledge?** Trimming and compaction must never
   silently drop an answer that used to be correct.
3. **Do the checks themselves catch real problems?** A judge that passes
   everything proves nothing.

## Method

Each eval bundle pins an exact repo commit *and* KB commit, so a run is
reproducible rather than anecdotal. Tasks are read-only project-knowledge
questions with two kinds of assertions:

- **Deterministic behavior checks**, read straight from the agent's tool-call
  trace — did it open the expected KB file, did it avoid editing anything.
- **Semantic checks** on the answer itself, scored by an LLM judge. Judges are
  calibrated against deliberately broken answers (negative controls) and
  cross-checked against a second judge harness, so a rubber-stamp judge fails
  calibration instead of inflating results.

Runs are tracked per harness (Claude Code, Codex CLI) rather than pooled.
Result files record per-assertion outcomes, judge provenance (model, prompt
template hash), and cost accounting.

## Results

### Do agents read it? (dogfooding compliance)

We instrument our own local sessions on this repository and measure *read
compliance*: did the session read a KB entry file before its first source
exploration or edit.

- **Baseline (2026-07-05, 103 sessions):** 33% read compliance. Most misses
  were *late* reads — the agent got to the KB eventually, just not first.
- **Post-fix population (2026-07-06, 46 sessions):** 69.6% read compliance
  (Codex track: 72.7%), after moving the KB protocol so it is reachable from
  both `AGENTS.md` and `CLAUDE.md`.

Honest caveats: the two measurements cover different session populations, so
this is an observed post-fix association, not a proven causal effect; and the
post-fix Claude sample (2 sessions) is too small to say anything about the
Claude track yet.

### Does maintenance lose knowledge?

Every trim/compact of the KB is followed by a non-regression pass: questions
that were answered correctly before the cleanup are re-asked after it, and no
assertion may flip from pass to fail. This gate has run on every compaction to
date.

### Do the checks catch real problems?

Two concrete cases from Release 2 validation:

- The eval loop flagged a project question the KB was answering wrong; the KB
  was corrected and the same check went green on rerun — the
  evidence → repair → retest loop works end to end.
- Early judge-calibration runs failed outright (unparseable judge output,
  unsupported model); after fixes, the final calibration shows the second
  judge agreeing with the primary on all compared verdicts. The iteration is
  visible in the result files rather than hidden.

### Worked example: the `agent-kb-release-2` bundle

The pinned bundle in [`bundles/agent-kb-release-2/`](bundles/agent-kb-release-2/)
asks two read-only questions about this repository's own project knowledge.
On the final Claude run, 5 of 6 assertions passed: both judge checks and both
no-edit checks passed, while one behavior check failed — on that task the agent
answered correctly by reading source code instead of opening the expected KB
decisions file. We keep that failure in the record: it is exactly the kind of
compliance miss the deterministic checks exist to expose, and it feeds the
read-compliance work above.

Result files also include runs where the harness itself errored (0 tool
calls). Those are kept as records of runner behavior, and are excluded from
any claim about agent performance.

## What we do not claim

- No cross-harness proof: compliance evidence is strongest for the Codex
  track; the Claude track needs more post-fix sessions.
- No causal claims from population-shift measurements.
- No absolute-utility claim: dogfooding on the KB project's own repository is
  an existence proof, not a generalization result. Generalization evidence
  comes from the private real-project runs, which currently show the same
  qualitative pattern (KB consulted first, answers grounded in recorded
  decisions).

## Reproducing

```bash
cd evals
python3 run_bundle.py --bundle bundles/agent-kb-release-2
```

`bundle.yaml` pins the repo and KB commits; see
[`bundles/pressure-distillation/README.md`](bundles/pressure-distillation/README.md)
for the pre/post distillation protocol used in cross-repo experiments.
