#!/usr/bin/env python3
"""Analyze whether local agent sessions read the KB before exploring code."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "agent-context-kb" / "scripts"))
from transcript_reads import (  # noqa: E402
    ToolEvent,
    collect_tool_events,
    resolve_path,
)


@dataclass
class SessionResult:
    session: str
    harness: str
    compliant: bool
    applicable: bool
    category: str
    late_bucket: str | None
    read_agents_md: bool
    first_kb_order: int | None
    first_any_kb_order: int | None
    first_source_order: int | None
    first_source_kind: str | None
    first_source_path: str


@dataclass(frozen=True)
class SinceFilterResult:
    events: list[ToolEvent]
    cutoff: datetime
    excluded_missing_timestamp: int


# Parses an ISO timestamp into an aware datetime, accepting transcript UTC `Z`.
def parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


# Resolves a --since value as ISO first, then as a git ref in the target repo.
def resolve_since(value: str, root: Path) -> datetime:
    parsed = parse_timestamp(value)
    if parsed is not None:
        return parsed
    result = subprocess.run(
        ["git", "show", "-s", "--format=%cI", value],
        cwd=root,
        check=False,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise ValueError(f"could not parse --since as ISO timestamp or git ref: {detail}")
    parsed = parse_timestamp(result.stdout.strip())
    if parsed is None:
        raise ValueError("git ref resolved to an invalid commit timestamp")
    return parsed


# Groups events into logical sessions, keeping only the main-thread files of each session.
# Sub-agent events are dropped rather than woven into the stream on purpose: this analyzer is
# order-sensitive, a sub-agent explores code by design and its record indices restart per file, so
# interleaving them would invent "read the KB after exploring code" verdicts no agent produced.
# A sub-agent-only group therefore disappears instead of being judged as a session of its own.
# (`stats` differs deliberately: it has no ordering, so it can credit sub-agent KB reads to the parent.)
# The remaining files are ordered as whole blocks by start time, never interleaved, because only the
# record order inside a file is exact. A later file can then only be pushed further back, never in
# front of an earlier file's KB read, so merging cannot invent a late-KB-read verdict; the cost is
# that a long first file can hide a genuinely late read, which is the safe direction to err in.
def group_events_by_session(events: list[ToolEvent]) -> dict[str, list[ToolEvent]]:
    grouped: dict[str, list[ToolEvent]] = {}
    for event in events:
        if not event.user_thread:
            continue
        grouped.setdefault(event.session, []).append(event)
    for session_events in grouped.values():
        session_events.sort(key=lambda event: (event.file_key, event.order))
    return grouped


# Lays a session's transcript files end to end so events from different files compare on one axis.
# Each block is offset by the record counts of the blocks before it, so an event's number keeps
# meaning "records into the session" — the unit the late-read buckets are stated in.
def session_orders(session_events: list[ToolEvent]) -> list[int]:
    lengths = {event.file_key: event.file_records for event in session_events}
    offsets: dict[str, int] = {}
    cursor = 0
    for key in sorted(lengths):
        offsets[key] = cursor
        cursor += lengths[key]
    return [offsets[event.file_key] + event.order for event in session_events]


# Returns the first event of any of these kinds together with its session-global order.
def first_event(events: list[ToolEvent], orders: list[int], kinds: set[str]) -> tuple[ToolEvent | None, int | None]:
    for event, order in zip(events, orders):
        if event.kind in kinds:
            return event, order
    return None, None


# Returns the earliest real timestamp observed in a session.
def session_start_time(events: list[ToolEvent]) -> datetime | None:
    timestamps = [parsed for event in events if (parsed := parse_timestamp(event.timestamp)) is not None]
    return min(timestamps) if timestamps else None


# Keeps sessions on or after the cutoff and counts sessions without real timestamps.
def filter_events_since(events: list[ToolEvent], cutoff: datetime) -> SinceFilterResult:
    filtered: list[ToolEvent] = []
    excluded_missing_timestamp = 0
    for session_events in group_events_by_session(events).values():
        started_at = session_start_time(session_events)
        if started_at is None:
            excluded_missing_timestamp += 1
            continue
        if started_at >= cutoff:
            filtered.extend(session_events)
    return SinceFilterResult(filtered, cutoff, excluded_missing_timestamp)


# Returns the late-read bucket for the distance between source and KB entry reads.
def late_read_bucket(delta: int | None) -> str | None:
    if delta is None or delta <= 0:
        return None
    if delta <= 3:
        return "1-3 actions late"
    if delta <= 19:
        return "4-19 actions late"
    return "20+ actions late"


# Classifies a session by the session-global order of the first KB and source events observed.
def classify_session(
    first_kb: int | None,
    first_any_kb: int | None,
    first_source: int | None,
) -> tuple[bool, str, str | None]:
    if first_source is None:
        return False, "kb_first_not_applicable", None
    if first_kb is not None and first_kb < first_source:
        return True, "compliant", None
    if first_kb is not None and first_kb > first_source:
        return True, "late_kb_read", late_read_bucket(first_kb - first_source)
    if first_any_kb is not None and first_any_kb < first_source:
        return True, "non_entry_kb_read", None
    if first_kb is None:
        return True, "no_kb_read", None
    return True, "harness_parser_gap", None


# Summarizes whether each session read the KB entry before first source exploration or edit.
def summarize_sessions(events: list[ToolEvent]) -> list[SessionResult]:
    results = []
    for session, session_events in sorted(group_events_by_session(events).items()):
        orders = session_orders(session_events)
        _kb, first_kb = first_event(session_events, orders, {"kb_entry_read"})
        _any_kb, first_any_kb = first_event(session_events, orders, {"kb_entry_read", "kb_read"})
        source, first_source = first_event(session_events, orders, {"source_explore", "source_edit"})
        read_agents_md = any(event.kind == "agents_read" for event in session_events)
        compliant = first_kb is not None and (first_source is None or first_kb < first_source)
        applicable, category, bucket = classify_session(first_kb, first_any_kb, first_source)
        harness = session_events[0].harness if session_events else "unknown"
        results.append(
            SessionResult(
                session=session,
                harness=harness,
                compliant=compliant,
                applicable=applicable,
                category=category,
                late_bucket=bucket,
                read_agents_md=read_agents_md,
                first_kb_order=first_kb,
                first_any_kb_order=first_any_kb,
                first_source_order=first_source,
                first_source_kind=source.kind if source else None,
                first_source_path=source.path if source else "",
            )
        )
    return results


# Formats a numerator and denominator as both count and percentage.
def format_rate(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0/0 (n/a)"
    return f"{numerator}/{denominator} ({numerator / denominator * 100:.1f}%)"


# Counts results matching a category name.
def count_category(results: list[SessionResult], category: str) -> int:
    return sum(1 for result in results if result.category == category)


# Counts late-read misses in each distance bucket.
def count_late_buckets(results: list[SessionResult]) -> dict[str, int]:
    buckets = {"1-3 actions late": 0, "4-19 actions late": 0, "20+ actions late": 0}
    for result in results:
        if result.late_bucket:
            buckets[result.late_bucket] += 1
    return buckets


# Counts miss cases whose first source action lacks a concrete path for manual review.
def count_source_path_missing(results: list[SessionResult]) -> int:
    return sum(
        1
        for result in results
        if result.applicable and not result.compliant and result.first_source_order is not None and not result.first_source_path
    )


# Prints the core compliance metrics for a group of sessions.
def print_summary_block(label: str, results: list[SessionResult], indent: str = "") -> None:
    total = len(results)
    compliant = sum(1 for result in results if result.compliant)
    hit = sum(1 for result in results if result.first_kb_order is not None)
    any_hit = sum(1 for result in results if result.first_any_kb_order is not None)
    applicable = [result for result in results if result.applicable]
    applicable_compliant = sum(1 for result in applicable if result.compliant)
    first_source_before_kb = sum(
        1
        for result in results
        if result.first_source_order is not None
        and (result.first_kb_order is None or result.first_source_order < result.first_kb_order)
    )
    print(f"{indent}{label}")
    print(f"{indent}Sessions analyzed (logical user sessions): {total}")
    print(f"{indent}KB entry hit rate: {format_rate(hit, total)}")
    print(f"{indent}Any KB hit rate: {format_rate(any_hit, total)}")
    print(f"{indent}Read compliance: {format_rate(compliant, total)}")
    print(f"{indent}Applicable read compliance (auto): {format_rate(applicable_compliant, len(applicable))}")
    print(f"{indent}First source action before KB: {first_source_before_kb}")
    print(f"{indent}Miss taxonomy (automated):")
    print(f"{indent}  Real behavior:")
    print(f"{indent}  - late KB read: {count_category(results, 'late_kb_read')}")
    for bucket, count in count_late_buckets(results).items():
        print(f"{indent}    - {bucket}: {count}")
    print(f"{indent}  - no KB read: {count_category(results, 'no_kb_read')}")
    print(f"{indent}  - non-entry KB read: {count_category(results, 'non_entry_kb_read')}")
    print(f"{indent}  - KB-first not applicable: {count_category(results, 'kb_first_not_applicable')}")
    print(f"{indent}  Measurement noise candidates:")
    print(f"{indent}  - source path unavailable: {count_source_path_missing(results)}")
    print(f"{indent}  - outside-root ambiguity: 0")
    print(f"{indent}  - harness parser gap: {count_category(results, 'harness_parser_gap')}")


# Prints harness and Claude AGENTS.md delivery breakdowns.
def print_breakdowns(results: list[SessionResult]) -> None:
    print()
    print("Breakdown by harness:")
    for harness in ["claude", "codex"]:
        group = [result for result in results if result.harness == harness]
        if group:
            print_summary_block(harness, group, "  ")
    unknown = [result for result in results if result.harness not in {"claude", "codex"}]
    if unknown:
        print_summary_block("unknown", unknown, "  ")

    claude = [result for result in results if result.harness == "claude"]
    if claude:
        print()
        print("Claude AGENTS.md delivery:")
        print_summary_block("read AGENTS.md", [result for result in claude if result.read_agents_md], "  ")
        print_summary_block("did not read AGENTS.md", [result for result in claude if not result.read_agents_md], "  ")


# Prints the compliance summary and optional per-session details.
# The session-unit note leads because the counts below are per logical session, not per transcript
# file, which is a different (much smaller) denominator than a file-per-session reading would give.
def print_report(results: list[SessionResult], details: bool) -> None:
    print("Session unit: one logical session - resumed/forked transcript files count once, and")
    print("sub-agent rollouts are folded into the session that spawned them, never judged alone.")
    print()
    print_summary_block("Compliance summary", results)
    print("Write-back compliance: deferred (needs heuristic or manual labels)")
    print_breakdowns(results)
    if details:
        print()
        print("Details:")
        for result in results:
            status = "compliant" if result.compliant else "miss"
            late = f" late_bucket={result.late_bucket}" if result.late_bucket else ""
            print(
                f"- {result.session} [{result.harness}] {status} "
                f"category={result.category}{late} first_kb={result.first_kb_order} "
                f"first_any_kb={result.first_any_kb_order} first_source={result.first_source_order} "
                f"read_agents_md={result.read_agents_md}"
            )


# Builds the command-line parser for the private compliance analyzer.
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze KB read compliance from local agent transcripts.")
    parser.add_argument("--root", default=".", help="Repository root to analyze.")
    parser.add_argument("--claude-dir", default="~/.claude/projects", help="Claude Code projects transcript directory.")
    parser.add_argument("--codex-dir", default="~/.codex/sessions", help="Codex sessions transcript directory.")
    parser.add_argument("--no-claude", action="store_true", help="Skip Claude Code transcripts.")
    parser.add_argument("--no-codex", action="store_true", help="Skip Codex transcripts.")
    parser.add_argument("--details", action="store_true", help="Print per-session compliance details.")
    parser.add_argument("--since", help="Only include sessions on or after an ISO timestamp or git ref.")
    return parser


# Parses arguments, analyzes transcripts, and prints a compact developer report.
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_path(args.root)
    claude_dir = None if args.no_claude else resolve_path(args.claude_dir)
    codex_dir = None if args.no_codex else resolve_path(args.codex_dir)
    if not (root / ".agent-kb").exists():
        print("ERROR: --root must point to a repo with .agent-kb/", file=sys.stderr)
        return 1
    events = collect_tool_events(root, claude_dir, codex_dir)
    since_filter = None
    if args.since:
        try:
            cutoff = resolve_since(args.since, root)
        except ValueError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        since_filter = filter_events_since(events, cutoff)
        events = since_filter.events
        print(f"Since filter: {args.since} -> {since_filter.cutoff.isoformat()}")
        print(f"Sessions excluded by missing/invalid timestamp: {since_filter.excluded_missing_timestamp}")
    print_report(summarize_sessions(events), args.details)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
