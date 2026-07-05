# Late-Read Analysis - 2026-07-05

## Summary

The first Release 2 follow-up analysis adds two breakdowns to the compliance
baseline: harness-level rates and Claude sessions split by whether they read
`AGENTS.md`. The main finding is that Claude delivery is structurally weaker in
this repo because `CLAUDE.md` is only a pointer to `AGENTS.md`, so the KB
protocol is not in Claude's pinned instruction context.

## Measurements

Command:

```bash
python3 skills/agent-context-kb/scripts/dev/compliance_analyzer.py --root .
```

Overall:

- Sessions analyzed: 105
- KB entry hit rate: 91/105 (86.7%)
- Any KB hit rate: 95/105 (90.5%)
- Read compliance: 38/105 (36.2%)
- First source action before KB: 67
- Late KB read: 53
  - 1-3 actions late: 26
  - 4-19 actions late: 17
  - 20+ actions late: 10
- No KB read: 13
- Non-entry KB read: 1

By harness:

- Claude: 0/5 read compliance, 3 late KB reads, 1 no KB read, 1 non-entry KB read.
- Codex: 38/100 read compliance, 50 late KB reads, 12 no KB reads.

Claude `AGENTS.md` delivery split:

- Read `AGENTS.md`: 0/4 read compliance; 3 late KB reads and 1 no KB read.
- Did not read `AGENTS.md`: 0/1 read compliance; 1 non-entry KB read.

## Interpretation

The cross-harness difference is directionally useful but not the main proof:
Claude has only five sessions here, Codex read detection is inferred from shell
commands, and the two harnesses likely handled different tasks.

The stronger signal is mechanistic. This repo's `CLAUDE.md` points to
`AGENTS.md`, while the runtime protocol body lives in `AGENTS.md`. Claude
sessions that eventually read `AGENTS.md` still did so after source actions, so
the protocol was delivered too late to govern the initial work. That matches the
placement hypothesis better than a wording or reminder hypothesis.

## P0 Decision

Fix protocol placement before changing wording or adding hooks:

- When `CLAUDE.md` is a short pointer to `AGENTS.md`, make `CLAUDE.md` the
  runtime protocol owner.
- If `AGENTS.md` already contains the KB protocol in that migration case, keep
  that Codex-visible copy and sync it to the current generated protocol to avoid
  drift.
- Keep `init --hooks` deferred until a post-placement baseline shows remaining
  decay that placement cannot explain.

## Follow-Up

After the placement fix, rerun the analyzer and compare:

- Claude read compliance.
- Claude late-read count.
- Overall late-read buckets, especially the short `1-3 actions late` bucket.

The remaining late-read cases should then be sampled separately for benign
"looked at scene first" behavior versus real work before KB read.
