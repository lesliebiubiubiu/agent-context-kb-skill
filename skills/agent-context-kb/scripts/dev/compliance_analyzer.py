#!/usr/bin/env python3
"""Analyze whether local agent sessions read the KB before exploring code."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from transcript_reads import (  # noqa: E402
    KB_READ_COMMANDS,
    claude_transcript_paths,
    claude_tool_uses,
    codex_kb_read_path,
    codex_transcript_paths,
    codex_tool_call,
    command_paths,
    command_words,
    iter_jsonl,
    path_is_inside_root,
    record_cwd,
    resolve_path,
    root_relative,
)


KB_ENTRY_FILES = {"start.md", "routes.yaml"}
SOURCE_SEARCH_TOOLS = {"Grep", "Glob", "LS"}
SOURCE_EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
SHELL_SEARCH_COMMANDS = {"rg", "grep", "find", "ls"}
SHELL_EDIT_COMMANDS = {"apply_patch", "perl", "ruby"}


@dataclass
class ToolEvent:
    session: str
    harness: str
    timestamp: str
    order: int
    kind: str
    path: str = ""


@dataclass
class SessionResult:
    session: str
    harness: str
    compliant: bool
    first_kb_order: int | None
    first_source_order: int | None


# Classifies a root-relative path as a KB entry read or source exploration.
def read_kind_for_relative(relative: Path) -> str:
    if relative.parts and relative.parts[0] == ".agent-kb":
        kb_relative = Path(*relative.parts[1:]) if len(relative.parts) > 1 else Path("")
        return "kb_entry_read" if str(kb_relative) in KB_ENTRY_FILES else "kb_read"
    return "source_explore"


# Extracts normalized tool events from one Claude Code transcript.
def parse_claude_transcript(path: Path, root: Path) -> list[ToolEvent]:
    events: list[ToolEvent] = []
    belongs_to_root = False
    session = f"claude:{path.stem}"
    for index, record in enumerate(iter_jsonl(path)):
        if path_is_inside_root(record_cwd(record), root):
            belongs_to_root = True
        timestamp = str(record.get("timestamp", ""))
        for tool_use in claude_tool_uses(record):
            name = str(tool_use.get("name", ""))
            tool_input = tool_use.get("input") if isinstance(tool_use.get("input"), dict) else {}
            file_path = str(tool_input.get("file_path") or tool_input.get("path") or "")
            relative = root_relative(file_path, root)
            if relative is not None:
                belongs_to_root = True
            if name == "Read" and relative is not None:
                events.append(ToolEvent(session, "claude", timestamp, index, read_kind_for_relative(relative), str(relative)))
            elif name in SOURCE_SEARCH_TOOLS:
                if relative is not None and relative.parts[:1] == (".agent-kb",):
                    continue
                if belongs_to_root or relative is not None:
                    events.append(ToolEvent(session, "claude", timestamp, index, "source_explore", str(relative or "")))
            elif name in SOURCE_EDIT_TOOLS and relative is not None:
                if relative.parts[:1] != (".agent-kb",):
                    events.append(ToolEvent(session, "claude", timestamp, index, "source_edit", str(relative)))
    return events if belongs_to_root else []


# Classifies a Codex shell command as KB read, source exploration, source edit, or irrelevant.
def classify_codex_command(command: str, root: Path, workdir: Path | None) -> tuple[str | None, str]:
    words = command_words(command)
    if not words:
        return None, ""
    executable = Path(words[0]).name
    paths = command_paths(command, root, workdir)
    kb_read_path = codex_kb_read_path(command, root, workdir)
    if kb_read_path is not None:
        rel = str(Path(*kb_read_path.parts[1:])) if len(kb_read_path.parts) > 1 else ""
        return ("kb_entry_read" if rel in KB_ENTRY_FILES else "kb_read"), str(kb_read_path)
    if executable in SHELL_SEARCH_COMMANDS:
        return "source_explore", str(paths[0]) if paths else ""
    if executable in KB_READ_COMMANDS and paths:
        source_paths = [path for path in paths if path.parts[:1] != (".agent-kb",)]
        if source_paths:
            return "source_explore", str(source_paths[0])
    if executable in SHELL_EDIT_COMMANDS and paths:
        source_paths = [path for path in paths if path.parts[:1] != (".agent-kb",)]
        if source_paths:
            return "source_edit", str(source_paths[0])
    return None, ""


# Extracts normalized tool events from one Codex transcript.
def parse_codex_transcript(path: Path, root: Path) -> list[ToolEvent]:
    events: list[ToolEvent] = []
    session = f"codex:{path.stem}"
    belongs_to_root = False
    current_workdir: Path | None = None
    for index, record in enumerate(iter_jsonl(path)):
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        timestamp = str(record.get("timestamp", ""))
        if record.get("type") == "session_meta":
            cwd = str(payload.get("cwd") or payload.get("workdir") or "")
            cwd_path = resolve_path(cwd) if cwd else None
            if path_is_inside_root(cwd_path, root):
                belongs_to_root = True
                current_workdir = cwd_path
            continue
        if record.get("type") not in {"function_call", "tool_call"}:
            continue
        name, args = codex_tool_call(payload)
        if "apply_patch" in name:
            events.append(ToolEvent(session, "codex", timestamp, index, "source_edit"))
            belongs_to_root = True
            continue
        command = str(args.get("cmd") or args.get("command") or "")
        workdir_raw = str(args.get("workdir") or "")
        workdir = resolve_path(workdir_raw) if workdir_raw else current_workdir
        if path_is_inside_root(workdir, root):
            belongs_to_root = True
        kind, event_path = classify_codex_command(command, root, workdir)
        if kind:
            events.append(ToolEvent(session, "codex", timestamp, index, kind, event_path))
    return events if belongs_to_root else []


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


# Collects transcript paths under a directory using the harness' JSONL layout.
def transcript_paths(base: Path) -> list[Path]:
    if not base.exists():
        return []
    return sorted(path for path in base.rglob("*.jsonl") if path.is_file())


# Parses all configured transcript roots into normalized events.
def collect_events(root: Path, claude_dir: Path | None, codex_dir: Path | None) -> list[ToolEvent]:
    events: list[ToolEvent] = []
    if claude_dir is not None:
        for path in claude_transcript_paths(claude_dir, root):
            events.extend(parse_claude_transcript(path, root))
    if codex_dir is not None:
        for path in codex_transcript_paths(codex_dir, root):
            events.extend(parse_codex_transcript(path, root))
    return events


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
    events = collect_events(root, claude_dir, codex_dir)
    print_report(summarize_sessions(events), args.details)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
