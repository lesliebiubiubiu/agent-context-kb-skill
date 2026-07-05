"""Extract KB read events from local agent transcript files."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shlex


KB_READ_COMMANDS = {"cat", "head", "tail", "sed", "nl", "less", "more"}


@dataclass(frozen=True)
class KbReadEvent:
    session: str
    harness: str
    timestamp: str
    file: str
    chars: int


@dataclass(frozen=True)
class TranscriptScan:
    sessions: set[str]
    reads: list[KbReadEvent]


# Returns a resolved Path while tolerating missing files and user-relative input.
def resolve_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


# Reads JSONL records from a transcript, skipping blank or malformed lines.
def read_jsonl(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


# Builds Claude's project-directory encoding for a repo path.
def claude_project_name(root: Path) -> str:
    return str(root).replace("/", "-")


# Returns whether a path is inside root and, if so, its relative path.
def root_relative(path_value: str, root: Path) -> Path | None:
    if not path_value:
        return None
    try:
        candidate = resolve_path(path_value)
        return candidate.relative_to(root)
    except (OSError, ValueError):
        return None


# Returns a current-size char estimate for a KB file read.
def file_chars(root: Path, relative: Path) -> int:
    path = root / relative
    try:
        return len(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return 0


# Returns the KB-relative path when a repo-relative path is inside `.agent-kb/`.
def kb_relative(relative: Path) -> str | None:
    if not relative.parts or relative.parts[0] != ".agent-kb" or len(relative.parts) == 1:
        return None
    return str(Path(*relative.parts[1:]))


# Walks nested Claude message content and yields tool_use dictionaries.
def claude_tool_uses(record: dict) -> list[dict]:
    message = record.get("message") if isinstance(record.get("message"), dict) else record
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, list):
        return []
    return [item for item in content if isinstance(item, dict) and item.get("type") == "tool_use"]


# Extracts KB read events and root-owned session membership from one Claude Code transcript.
def parse_claude_transcript(path: Path, root: Path) -> TranscriptScan:
    records = read_jsonl(path)
    session = f"claude:{path.stem}"
    sessions = {session} if path.parent.name == claude_project_name(root) else set()
    reads: list[KbReadEvent] = []
    for index, record in enumerate(records):
        timestamp = str(record.get("timestamp") or f"{path.name}:{index}")
        for tool_use in claude_tool_uses(record):
            if tool_use.get("name") != "Read":
                continue
            tool_input = tool_use.get("input") if isinstance(tool_use.get("input"), dict) else {}
            relative = root_relative(str(tool_input.get("file_path") or ""), root)
            if relative is None:
                continue
            sessions.add(session)
            kb_path = kb_relative(relative)
            if kb_path is not None:
                reads.append(KbReadEvent(session, "claude", timestamp, kb_path, file_chars(root, relative)))
    return TranscriptScan(sessions, reads)


# Parses a Codex function-call payload into a tool name and argument dictionary.
def codex_tool_call(payload: dict) -> tuple[str, dict]:
    name = str(payload.get("name") or payload.get("tool_name") or "")
    raw_args = payload.get("arguments") or payload.get("input") or payload.get("parameters") or {}
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
        except json.JSONDecodeError:
            parsed = {}
    elif isinstance(raw_args, dict):
        parsed = raw_args
    else:
        parsed = {}
    return name, parsed


# Returns the executable-like words from a shell command using shlex when possible.
def command_words(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


# Finds root-relative paths mentioned in a shell command.
def command_paths(command: str, root: Path, workdir: Path | None) -> list[Path]:
    relatives: list[Path] = []
    for word in command_words(command):
        cleaned = word.strip("'\"")
        relative = root_relative(cleaned, root)
        if relative is not None:
            relatives.append(relative)
            continue
        if workdir is not None and not cleaned.startswith("-"):
            relative = root_relative(str(workdir / cleaned), root)
            if relative is not None:
                relatives.append(relative)
    return relatives


# Extracts the first `.agent-kb/` path read by a Codex shell command.
def codex_kb_read_path(command: str, root: Path, workdir: Path | None) -> Path | None:
    words = command_words(command)
    if not words or Path(words[0]).name not in KB_READ_COMMANDS:
        return None
    for relative in command_paths(command, root, workdir):
        if kb_relative(relative) is not None:
            return relative
    return None


# Extracts KB read events and root-owned session membership from one Codex transcript.
def parse_codex_transcript(path: Path, root: Path) -> TranscriptScan:
    records = read_jsonl(path)
    session = f"codex:{path.stem}"
    sessions: set[str] = set()
    reads: list[KbReadEvent] = []
    current_workdir: Path | None = None
    for index, record in enumerate(records):
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        timestamp = str(record.get("timestamp") or f"{path.name}:{index}")
        if record.get("type") == "session_meta":
            cwd = str(payload.get("cwd") or payload.get("workdir") or "")
            cwd_path = resolve_path(cwd) if cwd else None
            if cwd_path == root:
                sessions.add(session)
                current_workdir = cwd_path
            continue
        if record.get("type") not in {"function_call", "tool_call"}:
            continue
        _name, args = codex_tool_call(payload)
        command = str(args.get("cmd") or args.get("command") or "")
        workdir_raw = str(args.get("workdir") or "")
        workdir = resolve_path(workdir_raw) if workdir_raw else current_workdir
        if workdir == root:
            sessions.add(session)
        relative = codex_kb_read_path(command, root, workdir)
        kb_path = kb_relative(relative) if relative is not None else None
        if kb_path is not None:
            sessions.add(session)
            reads.append(KbReadEvent(session, "codex", timestamp, kb_path, file_chars(root, relative)))
    return TranscriptScan(sessions, reads)


# Collects transcript paths under a directory using the harness' JSONL layout.
def transcript_paths(base: Path) -> list[Path]:
    if not base.exists():
        return []
    return sorted(path for path in base.rglob("*.jsonl") if path.is_file())


# Merges multiple transcript scan results into one result.
def merge_scans(scans: list[TranscriptScan]) -> TranscriptScan:
    sessions: set[str] = set()
    reads: list[KbReadEvent] = []
    for scan in scans:
        sessions.update(scan.sessions)
        reads.extend(scan.reads)
    return TranscriptScan(sessions, reads)


# Scans local Claude Code and Codex transcripts for KB reads belonging to root.
def scan_transcripts(root: Path, claude_dir: Path | None, codex_dir: Path | None) -> TranscriptScan:
    scans: list[TranscriptScan] = []
    if claude_dir is not None:
        scans.extend(parse_claude_transcript(path, root) for path in transcript_paths(claude_dir))
    if codex_dir is not None:
        scans.extend(parse_codex_transcript(path, root) for path in transcript_paths(codex_dir))
    return merge_scans(scans)
