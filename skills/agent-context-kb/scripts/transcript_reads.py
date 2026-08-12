"""Extract KB read events from local agent transcript files."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shlex
import sys


KB_READ_COMMANDS = {"cat", "head", "tail", "sed", "nl", "less", "more"}
KB_ENTRY_FILES = {"start.md", "routes.yaml"}
AGENTS_INSTRUCTION_FILE = "AGENTS.md"
SOURCE_SEARCH_TOOLS = {"Grep", "Glob", "LS"}
SOURCE_EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
SHELL_SEARCH_COMMANDS = {"rg", "grep", "find", "ls"}
# Shell punctuation shlex leaves glued to an unquoted operand, e.g. `cat start.md; echo done`.
SHELL_TOKEN_TRIM = "'\"`;&|<>()"
# KB docs only ever carry these suffixes. Codex reads are scraped from free-form command text,
# so this rejects junk tokens that happen to sit after a `.agent-kb/` prefix.
KB_DOC_SUFFIXES = {".md", ".yaml", ".yml", ".json", ".txt"}
SHELL_EDIT_COMMANDS = {"apply_patch", "perl", "ruby"}
# Shell operators that chain several commands into one recorded string.
SHELL_SEGMENT_RE = re.compile(r"&&|\|\||;|\n")
# Codex records `exec` calls as JavaScript source, e.g.
# `tools.exec_command({cmd:"sed -n '1,40p' .agent-kb/start.md","workdir":"/repo"})`.
# The keys appear both bare and quoted, so match either form.
JS_CMD_RE = re.compile(r'"?\bcmd"?\s*:\s*("(?:[^"\\]|\\.)*")')
JS_WORKDIR_RE = re.compile(r'"?\bworkdir"?\s*:\s*("(?:[^"\\]|\\.)*")')
JS_EXEC_MARKER = "exec_command"
# Codex sends edits as a patch body, never as a read, whatever text the patch happens to quote.
PATCH_PREFIX = "*** Begin Patch"


@dataclass(frozen=True)
class KbReadEvent:
    session: str
    harness: str
    timestamp: str
    file: str
    chars: int


@dataclass(frozen=True)
class TranscriptScan:
    # Logical sessions (one per session id), not transcript files: a resumed session and its
    # sub-agents share one id. `user_sessions` holds those seen through a user thread, which is
    # what makes a session a "session a human started in this repo".
    sessions: set[str]
    reads: list[KbReadEvent]
    user_sessions: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class ToolEvent:
    session: str
    harness: str
    timestamp: str
    order: int
    kind: str
    path: str = ""
    # False for a sub-agent (Claude sidechain / Codex non-user thread) file. Such a file reports
    # its parent's session id, so a consumer can merge it and still tell whose actions these were.
    user_thread: bool = True
    # Orders the transcript files of one logical session against each other (a resume/fork chain);
    # `file_records` is that file's record count, so the files can be laid end to end and `order`
    # keeps meaning "records into the session".
    file_key: str = ""
    file_records: int = 0


# Returns a resolved Path while tolerating missing files and user-relative input.
# Junk tokens scraped from recorded commands (e.g. `~3`) make expanduser raise, so fall back to no expansion.
def resolve_path(path: str | Path) -> Path:
    try:
        expanded = Path(path).expanduser()
    except RuntimeError:
        expanded = Path(path)
    return expanded.resolve()


# Reads JSONL records from a transcript one line at a time, skipping blank or malformed lines.
def read_jsonl(path: Path) -> list[dict]:
    records = []
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return records
    with handle:
        for line in handle:
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


# Yields JSONL records from a transcript one line at a time, skipping malformed lines.
def iter_jsonl(path: Path):
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


# Builds Claude's project-directory encoding for a repo path using non-alphanumeric separators.
def claude_project_name(root: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(root))


# Builds the older slash-only Claude project-directory encoding used by early fixtures.
def legacy_claude_project_name(root: Path) -> str:
    return str(root).replace("/", "-")


# Returns likely Claude project directories for root without making them authoritative.
def claude_project_dirs(base: Path, root: Path) -> list[Path]:
    names = {claude_project_name(root), legacy_claude_project_name(root)}
    return sorted(path for path in (base / name for name in names) if path.exists() and path.is_dir())


# Returns whether an absolute path is inside root and, if so, its relative path.
# Relative input is rejected on purpose: resolving it here would use the process cwd, which
# attributes another project's transcript paths to this repo. Callers join it with the session workdir.
def root_relative(path_value: str, root: Path) -> Path | None:
    if not path_value:
        return None
    try:
        expanded = Path(path_value).expanduser()
    except RuntimeError:
        return None
    if not expanded.is_absolute():
        return None
    try:
        return expanded.resolve().relative_to(root)
    except (OSError, ValueError):
        return None


# Returns whether candidate points at root or any path below it.
def path_is_inside_root(candidate: Path | None, root: Path) -> bool:
    if candidate is None:
        return False
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


# Extracts a cwd/workdir value from common transcript record shapes.
def record_cwd(record: dict) -> Path | None:
    values = [
        record.get("cwd"),
        record.get("workdir"),
        record.get("currentWorkingDirectory"),
        record.get("project_dir"),
    ]
    message = record.get("message") if isinstance(record.get("message"), dict) else {}
    values.extend([message.get("cwd"), message.get("workdir")])
    for value in values:
        if value:
            try:
                return resolve_path(str(value))
            except OSError:
                return None
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


# Classifies a root-relative read path as KB, AGENTS.md, or source exploration.
def read_kind_for_relative(relative: Path) -> str:
    kb_path = kb_relative(relative)
    if kb_path is not None:
        return "kb_entry_read" if kb_path in KB_ENTRY_FILES else "kb_read"
    if str(relative) == AGENTS_INSTRUCTION_FILE:
        return "agents_read"
    return "source_explore"


# Builds one transcript file's scan result, stamping its collected reads with the logical session id.
# A file that never touched root owns nothing; a sub-agent file still reports its parent's session,
# but only a user thread puts that session into `user_sessions` (the denominator).
def file_scan(session: str, harness: str, owned: bool, user_thread: bool, found: list[tuple[str, str, int]]) -> TranscriptScan:
    if not owned:
        return TranscriptScan(set(), [], set())
    reads = [KbReadEvent(session, harness, timestamp, file, chars) for timestamp, file, chars in found]
    return TranscriptScan({session}, reads, {session} if user_thread else set())


# Builds the key that orders the transcript files of one logical session against each other.
# A file is ordered by when it starts, with its name only breaking ties. Start time is the one
# cross-file fact that always holds (a resumed or forked file starts after the file it came from),
# even though two files can then run at the same time. A file with no timestamp at all sorts last,
# because sorting it first would let its actions jump ahead of another file's KB read.
def transcript_file_key(path: Path, first_timestamp: str) -> str:
    return f"{first_timestamp or '~'}|{path.name}"


# Builds one transcript file's compliance events, stamping them with the logical session id.
# Same identity rule as the read parsers: a sub-agent or resumed file reports the session that
# started it, and `user_thread` records which of the two this file is.
def file_tool_events(
    session: str,
    harness: str,
    owned: bool,
    user_thread: bool,
    file_key: str,
    file_records: int,
    found: list[tuple[str, int, str, str]],
) -> list[ToolEvent]:
    if not owned:
        return []
    return [
        ToolEvent(session, harness, timestamp, order, kind, path, user_thread, file_key, file_records)
        for timestamp, order, kind, path in found
    ]


# Keeps only sessions a human started: a logical session counts when at least one of its transcript
# files is a user thread, so sub-agent reads stay (they carry the parent id) but sub-agent-only
# sessions leave both the numerator and the denominator.
def user_thread_scan(scan: TranscriptScan) -> TranscriptScan:
    sessions = scan.sessions & scan.user_sessions
    reads = [read for read in scan.reads if read.session in sessions]
    return TranscriptScan(sessions, reads, sessions)


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
# Reads are held as plain tuples until the file is read out, so they can be stamped with the
# logical session id (`sessionId`, which sidechain files share with their parent) rather than
# with the file name, which would split one session into several.
def parse_claude_transcript(path: Path, root: Path) -> TranscriptScan:
    # No `sessionId` anywhere means a single-file session, and a record without `isSidechain`
    # is a main thread: both fall back to "one user session named after the file".
    session = f"claude:{path.stem}"
    user_thread = False
    owned = False
    found: list[tuple[str, str, int]] = []
    current_cwd: Path | None = None
    for index, record in enumerate(iter_jsonl(path)):
        session_id = str(record.get("sessionId") or "")
        if session_id:
            session = f"claude:{session_id}"
        if not record.get("isSidechain"):
            user_thread = True
        cwd = record_cwd(record)
        # Keep the last cwd seen, including another project's, so bare Bash paths resolve there.
        if cwd is not None:
            current_cwd = cwd
        if path_is_inside_root(cwd, root):
            owned = True
        timestamp = str(record.get("timestamp") or f"{path.name}:{index}")
        for tool_use in claude_tool_uses(record):
            name = str(tool_use.get("name", ""))
            tool_input = tool_use.get("input") if isinstance(tool_use.get("input"), dict) else {}
            if name == "Read":
                relative = root_relative(str(tool_input.get("file_path") or ""), root)
                if relative is None:
                    continue
                owned = True
                kb_path = kb_relative(relative)
                if kb_path is not None:
                    found.append((timestamp, kb_path, file_chars(root, relative)))
            elif name == "Bash":
                # A `cat`/`sed`/... on a KB doc is a read too, under the same rules as Codex shell calls.
                for relative in shell_kb_read_paths(str(tool_input.get("command") or ""), root, current_cwd):
                    kb_path = kb_relative(relative)
                    if kb_path is None:
                        continue
                    owned = True
                    found.append((timestamp, kb_path, file_chars(root, relative)))
    return file_scan(session, "claude", owned, user_thread, found)


# Extracts normalized compliance events from one Claude Code transcript.
# Identity matches parse_claude_transcript: events are held as plain tuples until the file is read
# out, then stamped with the logical `sessionId` (which sidechain and resumed files share with the
# session that started them) rather than with the file name, which would split one session into several.
def parse_claude_tool_events(path: Path, root: Path) -> list[ToolEvent]:
    found: list[tuple[str, int, str, str]] = []
    belongs_to_root = False
    # No `sessionId` anywhere means a single-file session, and a record without `isSidechain`
    # is a main thread: both fall back to "one user session named after the file".
    session = f"claude:{path.stem}"
    user_thread = False
    first_timestamp = ""
    records = 0
    current_cwd: Path | None = None
    for index, record in enumerate(iter_jsonl(path)):
        records = index + 1
        session_id = str(record.get("sessionId") or "")
        if session_id:
            session = f"claude:{session_id}"
        if not record.get("isSidechain"):
            user_thread = True
        cwd = record_cwd(record)
        if cwd is not None:
            current_cwd = cwd
        if path_is_inside_root(cwd, root):
            belongs_to_root = True
        timestamp = str(record.get("timestamp", ""))
        if timestamp and not first_timestamp:
            first_timestamp = timestamp
        for tool_use in claude_tool_uses(record):
            name = str(tool_use.get("name", ""))
            tool_input = tool_use.get("input") if isinstance(tool_use.get("input"), dict) else {}
            file_path = str(tool_input.get("file_path") or tool_input.get("path") or "")
            relative = root_relative(file_path, root)
            if relative is not None:
                belongs_to_root = True
            if name == "Read" and relative is not None:
                found.append((timestamp, index, read_kind_for_relative(relative), str(relative)))
            elif name == "Bash":
                # Classify Bash with the shared shell rules so a KB read done in the shell still counts.
                kind, event_path = classify_codex_command(str(tool_input.get("command") or ""), root, current_cwd)
                if kind and (path_is_inside_root(current_cwd, root) or event_path):
                    found.append((timestamp, index, kind, event_path))
                    belongs_to_root = True
            elif name in SOURCE_SEARCH_TOOLS:
                if relative is not None and kb_relative(relative) is not None:
                    continue
                if belongs_to_root or relative is not None:
                    found.append((timestamp, index, "source_explore", str(relative or "")))
            elif name in SOURCE_EDIT_TOOLS and relative is not None and kb_relative(relative) is None:
                found.append((timestamp, index, "source_edit", str(relative)))
    file_key = transcript_file_key(path, first_timestamp)
    return file_tool_events(session, "claude", belongs_to_root, user_thread, file_key, records, found)


# Returns the raw argument payload of a Codex tool call before any decoding.
def codex_raw_args(payload: dict):
    return payload.get("arguments") or payload.get("input") or payload.get("parameters") or {}


# Parses a Codex function-call payload into a tool name and argument dictionary.
def codex_tool_call(payload: dict) -> tuple[str, dict]:
    name = str(payload.get("name") or payload.get("tool_name") or "")
    raw_args = codex_raw_args(payload)
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


# Returns a normalized command string, unwrapping common shell -c wrappers when present.
def codex_command_arg(args: dict) -> str:
    raw = args.get("cmd") or args.get("command") or ""
    if isinstance(raw, list):
        parts = [str(part) for part in raw]
        if len(parts) >= 3 and Path(parts[0]).name in {"bash", "sh", "zsh"} and parts[1] in {"-c", "-lc"}:
            return parts[2]
        return " ".join(shlex.quote(part) for part in parts)
    command = str(raw)
    words = command_words(command)
    if len(words) >= 3 and Path(words[0]).name in {"bash", "sh", "zsh"} and words[1] in {"-c", "-lc"}:
        return words[2]
    return command


# Decodes one JSON string literal lifted out of a JS payload, returning "" when it is malformed.
def js_string(literal: str) -> str:
    try:
        value = json.loads(literal)
    except json.JSONDecodeError:
        return ""
    return value if isinstance(value, str) else ""


# Pulls the (command, workdir) pairs out of a Codex `exec` JavaScript payload.
# The payload is JS source, not JSON, so it is split at each `exec_command` call site and each
# call's `cmd`/`workdir` fields are read with regexes; patch bodies are edits and yield nothing.
def js_exec_commands(source: str) -> list[tuple[str, str]]:
    if source.lstrip().startswith(PATCH_PREFIX):
        return []
    segments = source.split(JS_EXEC_MARKER)
    if len(segments) > 1:
        segments = segments[1:]
    pairs: list[tuple[str, str]] = []
    for segment in segments:
        workdir_match = JS_WORKDIR_RE.search(segment)
        workdir = js_string(workdir_match.group(1)) if workdir_match else ""
        for match in JS_CMD_RE.finditer(segment):
            command = js_string(match.group(1))
            if command:
                pairs.append((command, workdir))
    return pairs


# Returns the (command, workdir) pairs one Codex tool call ran.
# JSON arguments hold a single command; an `exec` call arrives as a JS wrapper string that can
# hold several, so the JS decoder is the fallback whenever the arguments are not JSON.
def codex_tool_commands(payload: dict) -> list[tuple[str, str]]:
    _name, args = codex_tool_call(payload)
    if args:
        return [(codex_command_arg(args), str(args.get("workdir") or ""))]
    raw_args = codex_raw_args(payload)
    return js_exec_commands(raw_args) if isinstance(raw_args, str) else []


# Returns the tool payload for the known Codex transcript record shapes.
def codex_record_tool_payload(record: dict) -> dict | None:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    # A transcript can nest JSON schemas whose `type` is a dict (`{"type": {"type": "string"}}`).
    # Testing an unhashable value against a set raises TypeError, so require a string first.
    record_type = record.get("type") if isinstance(record.get("type"), str) else ""
    payload_type = payload.get("type") if isinstance(payload.get("type"), str) else ""
    if record_type in {"function_call", "tool_call"}:
        return payload
    if record_type == "response_item" and payload_type in {"function_call", "custom_tool_call"}:
        return payload
    return None


# Returns the executable-like words from a shell command using shlex when possible.
def command_words(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


# Finds root-relative paths mentioned in a shell command.
# A bare path means "relative to the session's workdir", so it is only resolved when that workdir is known.
def command_paths(command: str, root: Path, workdir: Path | None) -> list[Path]:
    relatives: list[Path] = []
    for word in command_words(command):
        cleaned = word.strip(SHELL_TOKEN_TRIM)
        if not cleaned or cleaned.startswith("-"):
            continue
        relative = root_relative(cleaned, root)
        if relative is None and workdir is not None:
            relative = root_relative(str(workdir / cleaned), root)
        if relative is not None:
            relatives.append(relative)
    return relatives


# Splits a compound shell command into the individual commands it chains together.
# An operator can also sit inside a quoted argument (`sed -n '1,5p;9p' f`), which leaves a segment
# with unbalanced quotes, so any such split is discarded and the command is kept whole.
def command_segments(command: str) -> list[str]:
    segments = [segment.strip() for segment in SHELL_SEGMENT_RE.split(command) if segment.strip()]
    for segment in segments:
        try:
            shlex.split(segment)
        except ValueError:
            return [command.strip()] if command.strip() else []
    return segments


# Finds every `.agent-kb/` doc read by a shell command, one chained segment at a time.
# Both harnesses call this, so "a shell command that reads a KB doc" has a single definition.
def shell_kb_read_paths(command: str, root: Path, workdir: Path | None) -> list[Path]:
    found: list[Path] = []
    for segment in command_segments(command):
        words = command_words(segment)
        if not words or Path(words[0]).name not in KB_READ_COMMANDS:
            continue
        for relative in command_paths(segment, root, workdir):
            if kb_relative(relative) is None or relative.suffix.lower() not in KB_DOC_SUFFIXES:
                continue
            if relative not in found:
                found.append(relative)
    return found


# Extracts the first `.agent-kb/` doc path read by a Codex shell command.
def codex_kb_read_path(command: str, root: Path, workdir: Path | None) -> Path | None:
    paths = shell_kb_read_paths(command, root, workdir)
    return paths[0] if paths else None


# Classifies a Codex shell command as KB read, AGENTS.md read, source action, or irrelevant.
# A KB read wins over any other segment; otherwise the first classifiable segment decides.
def classify_codex_command(command: str, root: Path, workdir: Path | None) -> tuple[str | None, str]:
    kb_read_path = codex_kb_read_path(command, root, workdir)
    if kb_read_path is not None:
        rel = kb_relative(kb_read_path) or ""
        return ("kb_entry_read" if rel in KB_ENTRY_FILES else "kb_read"), str(kb_read_path)
    for segment in command_segments(command):
        kind, event_path = classify_codex_segment(segment, root, workdir)
        if kind:
            return kind, event_path
    return None, ""


# Classifies one non-compound shell command by its executable and the root paths it names.
def classify_codex_segment(command: str, root: Path, workdir: Path | None) -> tuple[str | None, str]:
    words = command_words(command)
    if not words:
        return None, ""
    executable = Path(words[0]).name
    paths = command_paths(command, root, workdir)
    if executable in KB_READ_COMMANDS:
        for path in paths:
            if str(path) == AGENTS_INSTRUCTION_FILE:
                return "agents_read", str(path)
    source_paths = [path for path in paths if kb_relative(path) is None]
    if executable in SHELL_SEARCH_COMMANDS:
        if source_paths:
            return "source_explore", str(source_paths[0])
        if path_is_inside_root(workdir, root):
            return "source_explore", ""
    if executable in KB_READ_COMMANDS and source_paths:
        return "source_explore", str(source_paths[0])
    if executable in SHELL_EDIT_COMMANDS and source_paths:
        return "source_edit", str(source_paths[0])
    return None, ""


# Extracts KB read events and root-owned session membership from one Codex transcript.
# A sub-agent or resumed rollout already records the root thread's id in `session_meta.session_id`,
# so keying on it (instead of on the file) merges those rollouts into the session that started them.
# A rollout can carry several `session_meta` records when it is resumed or forked; the last one
# wins, which folds every file of a resume chain onto the thread the chain ended up in.
def parse_codex_transcript(path: Path, root: Path) -> TranscriptScan:
    # An old rollout without `thread_source` has no sub-agent concept at all, so it is a user
    # session; only an explicit non-user thread_source marks a rollout as a sub-agent.
    session = f"codex:{path.stem}"
    user_thread = True
    owned = False
    found: list[tuple[str, str, int]] = []
    current_workdir: Path | None = None
    for index, record in enumerate(iter_jsonl(path)):
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        timestamp = str(record.get("timestamp") or f"{path.name}:{index}")
        if record.get("type") == "session_meta":
            session_id = str(payload.get("session_id") or "")
            if session_id:
                session = f"codex:{session_id}"
            thread_source = str(payload.get("thread_source") or "")
            if thread_source:
                user_thread = thread_source == "user"
            cwd = str(payload.get("cwd") or payload.get("workdir") or "")
            cwd_path = resolve_path(cwd) if cwd else None
            # Track the session's own cwd even when it is another project, so its bare
            # paths resolve there and fail the root check instead of landing under root.
            if cwd_path is not None:
                current_workdir = cwd_path
            if path_is_inside_root(cwd_path, root):
                owned = True
            continue
        tool_payload = codex_record_tool_payload(record)
        if tool_payload is None:
            continue
        _name, args = codex_tool_call(tool_payload)
        workdir_raw = str(args.get("workdir") or "")
        default_workdir = resolve_path(workdir_raw) if workdir_raw else current_workdir
        if path_is_inside_root(default_workdir, root):
            owned = True
        for command, command_workdir_raw in codex_tool_commands(tool_payload):
            workdir = resolve_path(command_workdir_raw) if command_workdir_raw else default_workdir
            if path_is_inside_root(workdir, root):
                owned = True
            for relative in shell_kb_read_paths(command, root, workdir):
                kb_path = kb_relative(relative)
                if kb_path is None:
                    continue
                owned = True
                found.append((timestamp, kb_path, file_chars(root, relative)))
    return file_scan(session, "codex", owned, user_thread, found)


# Extracts normalized compliance events from one Codex transcript.
# Identity matches parse_codex_transcript: the logical `session_meta.session_id` (last one wins,
# because a resumed or forked rollout records several) merges sub-agent and resumed rollouts into
# the session that started them, and only an explicit non-user `thread_source` marks a sub-agent.
def parse_codex_tool_events(path: Path, root: Path) -> list[ToolEvent]:
    found: list[tuple[str, int, str, str]] = []
    session = f"codex:{path.stem}"
    user_thread = True
    first_timestamp = ""
    records = 0
    belongs_to_root = False
    current_workdir: Path | None = None
    for index, record in enumerate(iter_jsonl(path)):
        records = index + 1
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        timestamp = str(record.get("timestamp", ""))
        if timestamp and not first_timestamp:
            first_timestamp = timestamp
        if record.get("type") == "session_meta":
            session_id = str(payload.get("session_id") or "")
            if session_id:
                session = f"codex:{session_id}"
            thread_source = str(payload.get("thread_source") or "")
            if thread_source:
                user_thread = thread_source == "user"
            cwd = str(payload.get("cwd") or payload.get("workdir") or "")
            cwd_path = resolve_path(cwd) if cwd else None
            if cwd_path is not None:
                current_workdir = cwd_path
            if path_is_inside_root(cwd_path, root):
                belongs_to_root = True
            continue
        tool_payload = codex_record_tool_payload(record)
        if tool_payload is None:
            continue
        name, args = codex_tool_call(tool_payload)
        workdir_raw = str(args.get("workdir") or "")
        default_workdir = resolve_path(workdir_raw) if workdir_raw else current_workdir
        if path_is_inside_root(default_workdir, root):
            belongs_to_root = True
        if "apply_patch" in name:
            if path_is_inside_root(default_workdir, root):
                found.append((timestamp, index, "source_edit", ""))
            continue
        for command, command_workdir_raw in codex_tool_commands(tool_payload):
            workdir = resolve_path(command_workdir_raw) if command_workdir_raw else default_workdir
            in_root_workdir = path_is_inside_root(workdir, root)
            if in_root_workdir:
                belongs_to_root = True
            kind, event_path = classify_codex_command(command, root, workdir)
            if kind and (in_root_workdir or event_path):
                found.append((timestamp, index, kind, event_path))
                belongs_to_root = True
    file_key = transcript_file_key(path, first_timestamp)
    return file_tool_events(session, "codex", belongs_to_root, user_thread, file_key, records, found)


# Collects transcript paths under a directory using the harness' JSONL layout.
def transcript_paths(base: Path) -> list[Path]:
    if not base.exists():
        return []
    return sorted(path for path in base.rglob("*.jsonl") if path.is_file())


# Returns whether a transcript's raw text mentions any needle before JSON parsing.
def transcript_mentions(path: Path, needles: set[str]) -> bool:
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return False
    with handle:
        for line in handle:
            if any(needle in line for needle in needles):
                return True
    return False


# Collects Claude transcript paths from likely project directories for this root.
def claude_transcript_paths(base: Path, root: Path) -> list[Path]:
    paths: list[Path] = []
    for project_dir in claude_project_dirs(base, root):
        paths.extend(transcript_paths(project_dir))
    return sorted(paths)


# Collects Codex transcript paths that cheaply mention this root.
# Codex sessions are date-partitioned, not per-project, so this is only a prefilter for which
# files to open; the parsers decide ownership from cwd/workdir. A `.agent-kb` needle is not used
# here because it matches every project's KB sessions.
def codex_transcript_paths(base: Path, root: Path) -> list[Path]:
    needles = {str(root), root.name}
    return [path for path in transcript_paths(base) if transcript_mentions(path, needles)]


# Merges per-file transcript scan results into one result, keeping user-thread membership separate
# so the user-thread rule can be applied once the whole scan (all files of a session) is known.
def merge_scans(scans: list[TranscriptScan]) -> TranscriptScan:
    sessions: set[str] = set()
    user_sessions: set[str] = set()
    reads: list[KbReadEvent] = []
    for scan in scans:
        sessions.update(scan.sessions)
        user_sessions.update(scan.user_sessions)
        reads.extend(scan.reads)
    return TranscriptScan(sessions, reads, user_sessions)


# Scans local Claude Code and Codex transcripts for KB reads belonging to root.
def scan_transcripts(root: Path, claude_dir: Path | None, codex_dir: Path | None) -> TranscriptScan:
    scans: list[TranscriptScan] = []
    if claude_dir is not None:
        scans.extend(parse_claude_transcript(path, root) for path in claude_transcript_paths(claude_dir, root))
    if codex_dir is not None:
        scans.extend(parse_codex_transcript(path, root) for path in codex_transcript_paths(codex_dir, root))
    return user_thread_scan(merge_scans(scans))


# Parses one transcript and returns no events when it fails, so one bad file cannot end the scan.
def safe_tool_events(path: Path, root: Path, parse: Callable[[Path, Path], list[ToolEvent]]) -> list[ToolEvent]:
    try:
        return parse(path, root)
    except Exception as err:
        print(f"WARN: skipped unparseable transcript {path.name}: {err}", file=sys.stderr)
        return []


# Collects normalized compliance events from local Claude Code and Codex transcripts.
def collect_tool_events(root: Path, claude_dir: Path | None, codex_dir: Path | None) -> list[ToolEvent]:
    events: list[ToolEvent] = []
    if claude_dir is not None:
        for path in claude_transcript_paths(claude_dir, root):
            events.extend(safe_tool_events(path, root, parse_claude_tool_events))
    if codex_dir is not None:
        for path in codex_transcript_paths(codex_dir, root):
            events.extend(safe_tool_events(path, root, parse_codex_tool_events))
    return events
