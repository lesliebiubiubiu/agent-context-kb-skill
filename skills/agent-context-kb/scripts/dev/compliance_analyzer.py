#!/usr/bin/env python3
"""Analyze whether local agent sessions read the KB before exploring code."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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


# Groups events by session while preserving their observed order.
def group_events_by_session(events: list[ToolEvent]) -> dict[str, list[ToolEvent]]:
    grouped: dict[str, list[ToolEvent]] = {}
    for event in events:
        grouped.setdefault(event.session, []).append(event)
    for session_events in grouped.values():
        session_events.sort(key=lambda event: (event.order, event.timestamp))
    return grouped


# Returns the late-read bucket for the distance between source and KB entry reads.
def late_read_bucket(delta: int | None) -> str | None:
    if delta is None or delta <= 0:
        return None
    if delta <= 3:
        return "1-3 actions late"
    if delta <= 19:
        return "4-19 actions late"
    return "20+ actions late"


# Classifies a session by the first KB and source events the analyzer can observe.
def classify_session(
    first_kb: ToolEvent | None,
    first_any_kb: ToolEvent | None,
    first_source: ToolEvent | None,
) -> tuple[bool, str, str | None]:
    if first_source is None:
        return False, "kb_first_not_applicable", None
    if first_kb and first_kb.order < first_source.order:
        return True, "compliant", None
    if first_kb and first_kb.order > first_source.order:
        delta = first_kb.order - first_source.order
        return True, "late_kb_read", late_read_bucket(delta)
    if first_any_kb and first_any_kb.order < first_source.order:
        return True, "non_entry_kb_read", None
    if first_kb is None:
        return True, "no_kb_read", None
    return True, "harness_parser_gap", None


# Summarizes whether each session read the KB entry before first source exploration or edit.
def summarize_sessions(events: list[ToolEvent]) -> list[SessionResult]:
    results = []
    for session, session_events in sorted(group_events_by_session(events).items()):
        first_kb = next((event for event in session_events if event.kind == "kb_entry_read"), None)
        first_any_kb = next((event for event in session_events if event.kind in {"kb_entry_read", "kb_read"}), None)
        first_source = next((event for event in session_events if event.kind in {"source_explore", "source_edit"}), None)
        read_agents_md = any(event.kind == "agents_read" for event in session_events)
        compliant = bool(first_kb and (not first_source or first_kb.order < first_source.order))
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
                first_kb_order=first_kb.order if first_kb else None,
                first_any_kb_order=first_any_kb.order if first_any_kb else None,
                first_source_order=first_source.order if first_source else None,
                first_source_kind=first_source.kind if first_source else None,
                first_source_path=first_source.path if first_source else "",
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
    print(f"{indent}Sessions analyzed: {total}")
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
def print_report(results: list[SessionResult], details: bool) -> None:
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
    print_report(summarize_sessions(events), args.details)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
