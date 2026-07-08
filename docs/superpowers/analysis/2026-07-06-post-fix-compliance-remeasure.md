# Post-Fix Compliance Remeasure

Date: 2026-07-06

## Summary

This remeasure is valid Release 2 evidence for the Codex track, but not causal
evidence for the Claude placement fix. After `--since cb91b38`, read compliance
is higher than the first baseline, especially for Codex. The changed population
and tiny Claude sample mean the result should be worded as an observed
post-fix association, not as proof that the placement fix caused the increase.

## Command

```bash
python3 skills/agent-context-kb/scripts/dev/compliance_analyzer.py --root . --since cb91b38
```

`cb91b38` resolved to `2026-07-05T17:14:27+08:00`.

## Results

- Sessions analyzed: 46
- KB entry hit rate: 42/46 (91.3%)
- Any KB hit rate: 44/46 (95.7%)
- Read compliance: 32/46 (69.6%)
- Applicable read compliance: 28/41 (68.3%)
- First source action before KB: 13

Harness split:

- Claude: 2 sessions, 0/2 read compliance. This is too small for a placement
  fix conclusion.
- Codex: 44 sessions, 32/44 read compliance (72.7%); applicable read
  compliance 28/39 (71.8%).

Miss taxonomy:

- Late KB read: 10 total; 7 were only 1-3 actions late.
- No KB read: 2.
- Non-entry KB read: 1.
- KB-first not applicable: 5.
- Measurement-noise buckets were all zero.

## Interpretation

The first baseline recorded overall read compliance around one third, with
Claude at 0/5 and Codex at 38/100. This remeasure shows a stronger Codex-track
result, but the intervention under review primarily affected Claude delivery
through `CLAUDE.md` / `AGENTS.md`. Because the Claude post-fix sample is only
two sessions, Release 2 should not claim that the placement fix caused the
overall improvement.

Use this as:

- Evidence that the Codex track now has substantially better observed
  compliance in the post-fix session population.
- A reason to keep Claude placement validation as a post-release wait-state
  until roughly 15-20 Claude sessions exist.

Do not use this as:

- Cross-harness proof.
- A causal placement-fix result.
- An absolute-utility claim.

## Release Wording Boundary

Release 2 can honestly claim a Codex-track validation story: compliance
improved in the observed post-fix Codex population, C1 regression and
trim/compact non-regression ran, and the eval-evidence -> KB-repair -> retest
loop was exercised. It should not claim cross-harness validation or causal KB
utility.
