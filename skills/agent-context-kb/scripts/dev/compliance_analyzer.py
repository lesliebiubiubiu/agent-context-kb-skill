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
    first_kb_order: int | None
    first_source_order: int | None


# Groups events by session while preserving their observed order.
def group_events_by_session(events: list[ToolEvent]) -> dict[str, list[ToolEvent]]:
    grouped: dict[str, list[ToolEvent]] = {}
    for event in events:
        grouped.setdefault(event.session, []).append(event)
    for session_events in grouped.values():
        session_events.sort(key=lambda event: (event.order, event.timestamp))
    return grouped


# Summarizes whether each session read the KB entry before first source exploration or edit.
def summarize_sessions(events: list[ToolEvent]) -> list[SessionResult]:
    results = []
    for session, session_events in sorted(group_events_by_session(events).items()):
        first_kb = next((event for event in session_events if event.kind == "kb_entry_read"), None)
        first_source = next((event for event in session_events if event.kind in {"source_explore", "source_edit"}), None)
        compliant = bool(first_kb and (not first_source or first_kb.order < first_source.order))
        harness = session_events[0].harness if session_events else "unknown"
        results.append(
            SessionResult(
                session=session,
                harness=harness,
                compliant=compliant,
                first_kb_order=first_kb.order if first_kb else None,
                first_source_order=first_source.order if first_source else None,
            )
        )
    return results


# Formats a numerator and denominator as both count and percentage.
def format_rate(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0/0 (n/a)"
    return f"{numerator}/{denominator} ({numerator / denominator * 100:.1f}%)"


# Prints the compliance summary and optional per-session details.
def print_report(results: list[SessionResult], details: bool) -> None:
    total = len(results)
    compliant = sum(1 for result in results if result.compliant)
    hit = sum(1 for result in results if result.first_kb_order is not None)
    first_source_before_kb = sum(
        1
        for result in results
        if result.first_source_order is not None
        and (result.first_kb_order is None or result.first_source_order < result.first_kb_order)
    )
    print("Compliance summary")
    print(f"Sessions analyzed: {total}")
    print(f"KB entry hit rate: {format_rate(hit, total)}")
    print(f"Read compliance: {format_rate(compliant, total)}")
    print(f"First source action before KB: {first_source_before_kb}")
    print("Write-back compliance: deferred (needs heuristic or manual labels)")
    if details:
        print()
        print("Details:")
        for result in results:
            status = "compliant" if result.compliant else "miss"
            print(
                f"- {result.session} [{result.harness}] {status} "
                f"first_kb={result.first_kb_order} first_source={result.first_source_order}"
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
