#!/usr/bin/env python3
"""Manage a lightweight repository-local agent knowledge base."""

from __future__ import annotations

import argparse
from collections import deque
import datetime as dt
import re
import sys
from pathlib import Path


RUNTIME_PROTOCOL = """## Project Knowledge Base

Use `.agent-kb/` as the project knowledge base.
Treat it as an agent-facing index and distilled knowledge layer; do not replace
human docs with KB entries.

Before non-trivial coding:
1. Read `.agent-kb/start.md`.
2. Read `.agent-kb/map.md`.
3. Read only KB documents relevant to the current task.

After coding:
- Update `.agent-kb/` only when the work creates or changes reusable project knowledge.
- Prefer the relevant topic file.
- Use `.agent-kb/inbox/` when the right location is unclear.
- Do not write ordinary progress logs or one-off chat summaries into KB.
"""

START_MD = """# Agent KB Start

This directory is the project knowledge base for coding agents.
It is an agent-facing index and distilled knowledge layer, not a replacement
for human docs. When docs already exist, summarize only the durable facts agents
need and link back to the source.

## How To Read

1. Read this file first.
2. Read `map.md` and choose only routes relevant to the current task.
3. Open candidate topic files and check `Summary` and `Read When`.
4. Continue into body text and related links only when they are clearly relevant.

## How To Write

Update this KB only when work creates or changes durable project knowledge:

- Architecture boundaries or module responsibilities
- Design decisions and reasons
- Project conventions, naming rules, or code style
- Debugging conclusions and known failures
- Local development, test, deployment, or integration workflows
- Constraints future agents should not rediscover

Prefer editing the relevant stable topic file. If the right location is unclear,
write a separate note in `inbox/`.

## Do Not Store

- Ordinary progress logs
- One-off chat summaries
- Secrets, tokens, cookies, credentials, or local-only state
- Unverified guesses unless clearly marked as pending verification
- Details that are already obvious from reading the code
"""

MAP_MD = """# KB Map

## Task Routing

| Task Pattern | Read First | Also Consider |
| --- | --- | --- |
| Architecture / module boundaries | architecture/overview.md | architecture/boundaries.md |
| Design decisions | decisions/active/project-decisions.md | architecture/overview.md |
| Debugging / known failures | debugging/known-failures.md | debugging/test-environment.md |
| Local dev / test / deploy | workflows/local-dev.md | workflows/deploy.md |
| Coding style / comments | conventions/code-style.md | conventions/comments.md |
"""

TOPIC_DOCS = {
    "architecture/overview.md": ("Architecture Overview", "Understanding the project structure or major components"),
    "architecture/boundaries.md": ("Architecture Boundaries", "Changing module responsibilities or cross-module behavior"),
    "decisions/active/project-decisions.md": ("Project Decisions", "Changing or relying on durable design decisions"),
    "debugging/known-failures.md": ("Known Failures", "Debugging recurring failures or surprising behavior"),
    "debugging/test-environment.md": ("Test Environment", "Running, fixing, or changing test setup"),
    "workflows/local-dev.md": ("Local Development", "Setting up or running the project locally"),
    "workflows/deploy.md": ("Deploy Workflow", "Changing release, deploy, or CI behavior"),
    "conventions/code-style.md": ("Code Style", "Changing code patterns, naming, or formatting conventions"),
    "conventions/comments.md": ("Comments", "Adding or changing project comment conventions"),
}

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
PLACEHOLDER_TEXTS = (
    "No durable knowledge recorded yet.",
    "Add durable project knowledge here.",
)


# Returns the repository root from an argparse namespace.
def repo_root(args: argparse.Namespace) -> Path:
    return Path(args.root).expanduser().resolve()


# Returns the `.agent-kb` directory for a repository root.
def kb_dir(root: Path) -> Path:
    return root / ".agent-kb"


# Writes content only when the target file does not already exist.
def write_if_missing(path: Path, content: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


# Builds a stable topic document with the required lightweight sections.
def render_topic(title: str, read_when: str) -> str:
    today = dt.date.today().isoformat()
    return f"""# {title}

## Summary

No entries yet.

## Read When

- {read_when}

## Current Knowledge

None yet.

## Related

None yet.

## Change Log

- {today} - Created initial KB topic.
"""


# Adds or replaces the Project Knowledge Base section in AGENTS.md.
def upsert_agents_protocol(root: Path) -> str:
    agents_path = root / "AGENTS.md"
    if not agents_path.exists():
        agents_path.write_text(RUNTIME_PROTOCOL, encoding="utf-8")
        return "created"

    text = agents_path.read_text(encoding="utf-8")
    pattern = re.compile(r"^## Project Knowledge Base\n.*?(?=^## |\Z)", re.M | re.S)
    if pattern.search(text):
        updated = pattern.sub(RUNTIME_PROTOCOL.rstrip() + "\n\n", text).rstrip() + "\n"
        agents_path.write_text(updated, encoding="utf-8")
        return "updated"

    separator = "" if text.endswith("\n\n") else "\n\n" if text.endswith("\n") else "\n\n"
    agents_path.write_text(text + separator + RUNTIME_PROTOCOL, encoding="utf-8")
    return "appended"


# Initializes `.agent-kb/` and the AGENTS.md runtime protocol.
def command_init(args: argparse.Namespace) -> int:
    root = repo_root(args)
    kb = kb_dir(root)
    created = []

    for directory in [
        kb / "inbox",
        kb / "architecture",
        kb / "decisions" / "active",
        kb / "decisions" / "superseded",
        kb / "debugging",
        kb / "workflows",
        kb / "conventions",
    ]:
        directory.mkdir(parents=True, exist_ok=True)

    for relative, content in {
        "start.md": START_MD,
        "map.md": MAP_MD,
    }.items():
        if write_if_missing(kb / relative, content):
            created.append(str(Path(".agent-kb") / relative))

    for relative, (title, read_when) in TOPIC_DOCS.items():
        if write_if_missing(kb / relative, render_topic(title, read_when)):
            created.append(str(Path(".agent-kb") / relative))

    protocol_action = upsert_agents_protocol(root)
    print(f"Initialized KB at {kb}")
    print(f"AGENTS.md protocol {protocol_action}.")
    if created:
        print("Created files:")
        for path in created:
            print(f"- {path}")
    return 0


# Extracts the Task Routing rows from `.agent-kb/map.md`.
def parse_map_rows(map_path: Path) -> tuple[list[tuple[str, str, str]], list[str]]:
    if not map_path.exists():
        return [], ["missing .agent-kb/map.md"]

    rows = []
    errors = []
    lines = map_path.read_text(encoding="utf-8").splitlines()
    in_table = False
    header_seen = False
    for line in lines:
        stripped = line.strip()
        if stripped == "## Task Routing":
            in_table = True
            continue
        if in_table and stripped.startswith("## "):
            break
        if not in_table or not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells == ["Task Pattern", "Read First", "Also Consider"]:
            header_seen = True
            continue
        if all(set(cell) <= {"-", " "} for cell in cells):
            continue
        if len(cells) != 3:
            errors.append(f"invalid map row: {line}")
            continue
        rows.append((cells[0], cells[1], cells[2]))

    if not header_seen:
        errors.append("map.md is missing the required Task Routing header")
    return rows, errors


# Splits a map cell into candidate KB-relative paths.
def split_path_cell(value: str) -> list[str]:
    if value.strip().lower() in {"", "-", "none", "n/a"}:
        return []
    normalized = value.replace("<br>", ",").replace("<br/>", ",").replace("<br />", ",")
    return [part.strip() for part in re.split(r"[,;]", normalized) if part.strip()]


# Normalizes a KB-relative path and rejects unsafe or external values.
def normalize_kb_path(raw: str) -> Path | None:
    if "://" in raw or raw.startswith("#"):
        return None
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path


# Returns Markdown links from text while ignoring external URLs and anchors.
def markdown_links(text: str) -> list[str]:
    links = []
    for match in LINK_RE.finditer(text):
        target = match.group(1).split("#", 1)[0].strip()
        if not target or "://" in target or target.startswith("mailto:"):
            continue
        links.append(target)
    return links


# Resolves a document-relative Markdown link to a KB-relative path when it stays inside the KB.
def resolve_internal_link(source: Path, kb: Path, raw: str) -> Path | None:
    if Path(raw).is_absolute():
        return None
    candidate = (source.parent / raw).resolve()
    try:
        return candidate.relative_to(kb.resolve())
    except ValueError:
        return None


# Lists stable topic documents while excluding routing and inbox files.
def stable_topic_docs(kb: Path) -> list[Path]:
    docs = []
    for path in kb.rglob("*.md"):
        relative = path.relative_to(kb)
        if relative.parts[0] == "inbox" or relative in {Path("start.md"), Path("map.md")}:
            continue
        if ".generated." in path.name:
            continue
        docs.append(path)
    return sorted(docs)


# Checks whether a topic document has the sections agents need for skimming.
def topic_section_warnings(kb: Path) -> list[str]:
    warnings = []
    required = ["## Summary", "## Read When", "## Current Knowledge"]
    for path in stable_topic_docs(kb):
        text = path.read_text(encoding="utf-8")
        for section in required:
            if section not in text:
                warnings.append(f"{path.relative_to(kb)} missing {section}")
    return warnings


# Checks whether a topic document still contains starter placeholder text.
def topic_placeholder_warnings(kb: Path) -> list[str]:
    warnings = []
    for path in stable_topic_docs(kb):
        text = path.read_text(encoding="utf-8")
        for placeholder in PLACEHOLDER_TEXTS:
            if placeholder in text:
                warnings.append(f"{path.relative_to(kb)} still contains placeholder text: {placeholder}")
    return warnings


# Resolves internal Markdown links from a document to KB-relative Markdown paths.
def linked_docs_from(path: Path, kb: Path) -> list[Path]:
    links = []
    text = path.read_text(encoding="utf-8")
    for raw in markdown_links(text):
        relative = resolve_internal_link(path, kb, raw)
        if relative is None:
            continue
        if relative.suffix == ".md":
            links.append(relative)
    return links


# Computes topic documents reachable from map routes and reachable Markdown links.
def reachable_docs(kb: Path, rows: list[tuple[str, str, str]]) -> set[Path]:
    reachable: set[Path] = set()
    queue: deque[Path] = deque()
    for _, read_first, also_consider in rows:
        for raw in split_path_cell(read_first) + split_path_cell(also_consider):
            normalized = normalize_kb_path(raw)
            if normalized and (kb / normalized).exists():
                queue.append(normalized)

    while queue:
        relative = queue.popleft()
        if relative in reachable:
            continue
        reachable.add(relative)
        path = kb / relative
        if path.exists() and path.suffix == ".md":
            queue.extend(linked_docs_from(path, kb))
    return reachable


# Validates the KB scaffold, routes, links, inbox notes, and topic reachability.
def command_validate(args: argparse.Namespace) -> int:
    root = repo_root(args)
    kb = kb_dir(root)
    errors = []
    warnings = []

    if not kb.exists():
        print("ERROR: missing .agent-kb/")
        return 1
    for relative in ["start.md", "map.md"]:
        if not (kb / relative).exists():
            errors.append(f"missing .agent-kb/{relative}")
    if not (kb / "inbox").is_dir():
        errors.append("missing .agent-kb/inbox/")

    rows, map_errors = parse_map_rows(kb / "map.md")
    errors.extend(map_errors)
    for _, read_first, also_consider in rows:
        for raw in split_path_cell(read_first) + split_path_cell(also_consider):
            normalized = normalize_kb_path(raw)
            if normalized is None:
                errors.append(f"invalid map path: {raw}")
            elif not (kb / normalized).exists():
                errors.append(f"map path does not exist: {raw}")

    for path in kb.rglob("*.md"):
        for raw in markdown_links(path.read_text(encoding="utf-8")):
            relative = resolve_internal_link(path, kb, raw)
            if relative is None:
                continue
            if not (kb / relative).exists():
                errors.append(f"broken link in {path.relative_to(kb)}: {raw}")

    reachable = reachable_docs(kb, rows)
    for path in stable_topic_docs(kb):
        relative = path.relative_to(kb)
        if relative not in reachable:
            warnings.append(f"stable topic is not reachable from map/links: {relative}")
    warnings.extend(topic_section_warnings(kb))
    warnings.extend(topic_placeholder_warnings(kb))

    for note in sorted((kb / "inbox").glob("*.md")) if (kb / "inbox").exists() else []:
        text = note.read_text(encoding="utf-8")
        if "Suggested target:" not in text or "## Note" not in text:
            warnings.append(f"inbox note does not match template: {note.relative_to(kb)}")

    for warning in warnings:
        print(f"WARN: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        return 1
    print(f"OK: validated {kb}")
    return 0


# Converts a note title into a short filesystem-safe slug.
def slugify(title: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")
    return slug[:48] or "note"


# Creates a separate inbox note for durable knowledge discovered during work.
def command_note(args: argparse.Namespace) -> int:
    root = repo_root(args)
    kb = kb_dir(root)
    inbox = kb / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    body = args.body if args.body is not None else sys.stdin.read().strip()
    if not body:
        print("ERROR: note body is empty", file=sys.stderr)
        return 1

    now = dt.datetime.now().strftime("%Y-%m-%dT%H%M%S")
    today = dt.date.today().isoformat()
    title = args.title.strip()
    target = args.target.strip() if args.target else "unsure"
    path = inbox / f"{now}-{slugify(title)}.md"
    content = f"""# {title}

Date: {today}
Suggested target: {target}

## Note

{body.rstrip()}
"""
    path.write_text(content, encoding="utf-8")
    print(f"Wrote {path}")
    return 0


# Parses an inbox note into title, date, suggested target, and body fields.
def parse_inbox_note(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    title_match = re.search(r"^#\s+(.+)$", text, re.M)
    date_match = re.search(r"^Date:\s*(.+)$", text, re.M)
    target_match = re.search(r"^Suggested target:\s*(.+)$", text, re.M)
    note_match = re.search(r"^## Note\s*\n(.*)\Z", text, re.M | re.S)
    return {
        "title": title_match.group(1).strip() if title_match else path.stem,
        "date": date_match.group(1).strip() if date_match else dt.date.today().isoformat(),
        "target": target_match.group(1).strip() if target_match else "unsure",
        "body": note_match.group(1).strip() if note_match else "",
    }


# Inserts text under a named Markdown heading, before the next same-level heading.
def insert_under_heading(text: str, heading: str, addition: str) -> tuple[str, bool]:
    pattern = re.compile(rf"(^## {re.escape(heading)}\s*$)(.*?)(?=^## |\Z)", re.M | re.S)
    match = pattern.search(text)
    if not match:
        return text.rstrip() + f"\n\n## {heading}\n\n{addition.rstrip()}\n", False
    existing = match.group(2).rstrip()
    replacement = f"{match.group(1)}{existing}\n\n{addition.rstrip()}\n\n"
    return text[: match.start()] + replacement + text[match.end() :], True


# Appends an inbox note to an existing stable target document.
def append_note_to_target(target: Path, note: dict[str, str], source_name: str) -> None:
    text = target.read_text(encoding="utf-8")
    addition = f"### {note['title']}\n\nDate: {note['date']}\nSource: `inbox/{source_name}`\n\n{note['body']}\n"
    text, _ = insert_under_heading(text, "Current Knowledge", addition)
    change = f"- {note['date']} - Merged inbox note `{source_name}`."
    text, _ = insert_under_heading(text, "Change Log", change)
    target.write_text(text.rstrip() + "\n", encoding="utf-8")


# Merges inbox notes with valid existing targets and leaves unresolved notes in place.
def command_compile(args: argparse.Namespace) -> int:
    root = repo_root(args)
    kb = kb_dir(root)
    inbox = kb / "inbox"
    merged = 0
    kept = []

    if not inbox.exists():
        print("ERROR: missing .agent-kb/inbox/")
        return 1

    for path in sorted(inbox.glob("*.md")):
        note = parse_inbox_note(path)
        target_raw = note["target"]
        target_rel = normalize_kb_path(target_raw)
        if target_raw.lower() == "unsure" or target_rel is None:
            kept.append((path.name, "target is unsure or invalid"))
            continue
        target = kb / target_rel
        if not target.exists():
            kept.append((path.name, f"target does not exist: {target_raw}"))
            continue
        append_note_to_target(target, note, path.name)
        path.unlink()
        merged += 1

    print(f"Merged: {merged}")
    print(f"Deleted: {merged}")
    print(f"Unresolved: {len(kept)}")
    for name, reason in kept:
        print(f"- {name}: {reason}")
    return 0


# Builds the command-line parser for agent KB maintenance actions.
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage a repository-local .agent-kb knowledge base.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize .agent-kb and AGENTS.md protocol.")
    init_parser.add_argument("--root", default=".", help="Repository root to manage.")
    init_parser.set_defaults(func=command_init)

    validate_parser = subparsers.add_parser("validate", help="Validate the .agent-kb scaffold.")
    validate_parser.add_argument("--root", default=".", help="Repository root to manage.")
    validate_parser.set_defaults(func=command_validate)

    note_parser = subparsers.add_parser("note", help="Write a durable knowledge note into inbox.")
    note_parser.add_argument("--root", default=".", help="Repository root to manage.")
    note_parser.add_argument("--title", required=True, help="Inbox note title.")
    note_parser.add_argument("--target", default="unsure", help="Suggested KB-relative target path.")
    note_parser.add_argument("--body", help="Note body. Reads stdin when omitted.")
    note_parser.set_defaults(func=command_note)

    compile_parser = subparsers.add_parser("compile", help="Merge inbox notes with valid targets.")
    compile_parser.add_argument("--root", default=".", help="Repository root to manage.")
    compile_parser.set_defaults(func=command_compile)
    return parser


# Parses arguments and dispatches the selected command.
def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
