# Release 2 Compliance Baseline

## Summary

This is the first Release 2 read-compliance baseline after parser hardening.
The baseline measures whether local agent sessions read the KB entry files before
their first source exploration or edit.

## Result

- Sessions analyzed: 103
- KB entry hit rate: 89/103 (86.4%)
- Read compliance: 34/103 (33.0%)
- First source action before KB: 69
- Write-back compliance: deferred; it needs heuristics or manual labels.

## Commands

```bash
python3 -m py_compile skills/agent-context-kb/scripts/agent_kb.py skills/agent-context-kb/scripts/smoke_test.py skills/agent-context-kb/scripts/transcript_reads.py skills/agent-context-kb/scripts/dev/compliance_analyzer.py
python3 skills/agent-context-kb/scripts/smoke_test.py
python3 skills/agent-context-kb/scripts/agent_kb.py validate --root .
python3 skills/agent-context-kb/scripts/dev/compliance_analyzer.py --root .
```

Read-usage stats were also checked:

```bash
python3 skills/agent-context-kb/scripts/agent_kb.py stats --root . --top 10
/usr/bin/time -p python3 skills/agent-context-kb/scripts/agent_kb.py stats --root . --top 10
```

The first post-cache-bump stats scan backfilled 838 KB read events across 98
scanned sessions. The next unchanged run completed in 0.09 seconds wall time.

## Transcript Scale

- Full local transcript corpus: 1,231 JSONL files, 1,204.2 MB.
- Candidate scan corpus for this repo run: 871 JSONL files, 1,066.7 MB.

## Parser Hardening Before Baseline

- Shared transcript event extraction between `stats` and the private compliance
  analyzer so root ownership and KB-read classification are fixed in one place.
- Kept `python3 ... agent_kb.py ...` from being classified as source edit.
- Kept `.agent-kb/` write/git commands from being classified as KB reads.
- Ignored source actions whose `cwd` / `workdir` and tool paths belong outside
  this repo, even when the same transcript later touches this repo.
- Added Codex `response_item` tool-call support and shell command-array
  normalization.
- Added an mtime/size cache for stats transcript backfill; unchanged transcripts
  reuse cached session ownership and are not reparsed.
- Made backfill failure visible as a distinct stats warning instead of looking
  like intentional backfill disablement.

## Taxonomy Pass - 2026-07-05

This pass appended automated miss taxonomy and late-read buckets to the private
compliance analyzer. It did not apply a parser correction, so the original
baseline counts above are left unchanged. The local transcript corpus had grown
by one session when this was rerun.

- Sessions analyzed: 104
- KB entry hit rate: 90/104 (86.5%)
- Any KB hit rate: 94/104 (90.4%)
- Read compliance: 34/104 (32.7%)
- Applicable read compliance (auto): 34/104 (32.7%)
- First source action before KB: 70

Automated miss taxonomy:

- Late KB read: 56
  - 1-3 actions late: 28
  - 4-19 actions late: 18
  - 20+ actions late: 10
- No KB read: 13
- Non-entry KB read: 1
- KB-first not applicable: 0
- Measurement-noise candidates:
  - Source path unavailable: 0
  - Outside-root ambiguity: 0
  - Harness parser gap: 0

The main signal is the gap between high eventual entry reads and low read
compliance: most misses are late reads, not total misses. Half of the late-read
misses are only 1-3 actions late, which should be analyzed separately from the
20+ action late reads before changing the runtime protocol.

## Caveats

- This is a single-repo dogfood baseline, not an absolute utility result.
- Compliance denominator is sessions with KB-entry or source-action events after
  repo-ownership filtering.
- Claude coverage relies on `Read` tool records. Codex coverage is lossy because
  reads happen through shell commands and are inferred from recognized commands.
- Per-session details were inspected locally with `--details` but are not stored
  here because they include local session ids and paths.
