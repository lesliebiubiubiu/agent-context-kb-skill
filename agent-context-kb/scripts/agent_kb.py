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
2. Read `.agent-kb/routes.yaml` as the source of truth; use `.agent-kb/map.md`
   only as a readable view if helpful.
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
2. Read `routes.yaml` and choose only routes relevant to the current task.
   Use `map.md` only as the readable summary if that is faster.
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

DEFAULT_ROUTES = [
    {
        "id": "architecture",
        "task": "Architecture / module boundaries",
        "read_first": ["architecture/overview.md"],
        "also_consider": ["architecture/boundaries.md"],
    },
    {
        "id": "decisions",
        "task": "Design decisions",
        "read_first": ["decisions/active/project-decisions.md"],
        "also_consider": ["architecture/overview.md"],
    },
    {
        "id": "debugging",
        "task": "Debugging / known failures",
        "read_first": ["debugging/known-failures.md"],
        "also_consider": ["debugging/test-environment.md"],
    },
    {
        "id": "local-dev",
        "task": "Local dev / test / deploy",
        "read_first": ["workflows/local-dev.md"],
        "also_consider": ["workflows/deploy.md"],
    },
    {
        "id": "code-style",
        "task": "Coding style / comments",
        "read_first": ["conventions/code-style.md"],
        "also_consider": ["conventions/comments.md"],
    },
]

ROUTES_YAML = """# Agent KB routes. Keep read_first narrow; use also_consider sparingly.
routes:
  - id: architecture
    task: Architecture / module boundaries
    read_first:
      - architecture/overview.md
    also_consider:
      - architecture/boundaries.md
  - id: decisions
    task: Design decisions
    read_first:
      - decisions/active/project-decisions.md
    also_consider:
      - architecture/overview.md
  - id: debugging
    task: Debugging / known failures
    read_first:
      - debugging/known-failures.md
    also_consider:
      - debugging/test-environment.md
  - id: local-dev
    task: Local dev / test / deploy
    read_first:
      - workflows/local-dev.md
    also_consider:
      - workflows/deploy.md
  - id: code-style
    task: Coding style / comments
    read_first:
      - conventions/code-style.md
    also_consider:
      - conventions/comments.md
"""

MAP_MD = """# KB Map

This is a readable view of `routes.yaml`. Keep routing narrow: one `Read First`
target and no more than two `Also Consider` targets per route.

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


# Formats a list of route paths for the Markdown routing table.
def format_route_paths(paths: list[str]) -> str:
    return ", ".join(paths) if paths else "-"


# Renders a Markdown routing table from route dictionaries.
def render_map(routes: list[dict[str, object]]) -> str:
    lines = [
        "# KB Map",
        "",
        "This is a readable view of `routes.yaml`. Keep routing narrow: one `Read First`",
        "target and no more than two `Also Consider` targets per route.",
        "",
        "## Task Routing",
        "",
        "| Task Pattern | Read First | Also Consider |",
        "| --- | --- | --- |",
    ]
    for route in routes:
        read_first = [str(path) for path in route.get("read_first", [])]
        also_consider = [str(path) for path in route.get("also_consider", [])]
        lines.append(f"| {route.get('task', '')} | {format_route_paths(read_first)} | {format_route_paths(also_consider)} |")
    return "\n".join(lines) + "\n"


# Parses the supported routes.yaml subset into route dictionaries.
def parse_routes_yaml(routes_path: Path) -> tuple[list[dict[str, object]], list[str]]:
    if not routes_path.exists():
        return [], ["missing .agent-kb/routes.yaml"]

    routes: list[dict[str, object]] = []
    errors = []
    current: dict[str, object] | None = None
    current_list: str | None = None
    saw_routes = False

    for line_number, line in enumerate(routes_path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "routes:":
            saw_routes = True
            continue
        if not saw_routes:
            errors.append(f"routes.yaml line {line_number}: expected routes:")
            continue
        if line.startswith("  - "):
            if current is not None:
                routes.append(current)
            current = {"read_first": [], "also_consider": []}
            current_list = None
            key_value = line[4:].strip()
            if not key_value:
                continue
            if ":" not in key_value:
                errors.append(f"routes.yaml line {line_number}: invalid route entry")
                continue
            key, value = [part.strip() for part in key_value.split(":", 1)]
            current[key] = value
            continue
        if current is None:
            errors.append(f"routes.yaml line {line_number}: route field before route entry")
            continue
        if line.startswith("    ") and not line.startswith("      - "):
            field = line[4:].strip()
            if ":" not in field:
                errors.append(f"routes.yaml line {line_number}: invalid field")
                continue
            key, value = [part.strip() for part in field.split(":", 1)]
            if key in {"read_first", "also_consider"}:
                current[key] = []
                current_list = key
            else:
                current[key] = value
                current_list = None
            continue
        if line.startswith("      - "):
            if current_list not in {"read_first", "also_consider"}:
                errors.append(f"routes.yaml line {line_number}: list item without list field")
                continue
            value = line[8:].strip()
            if not value:
                errors.append(f"routes.yaml line {line_number}: empty path")
                continue
            current[current_list].append(value)  # type: ignore[index, union-attr]
            continue
        errors.append(f"routes.yaml line {line_number}: unsupported YAML shape")

    if current is not None:
        routes.append(current)
    if not saw_routes:
        errors.append("routes.yaml is missing routes:")
    for index, route in enumerate(routes, 1):
        for key in ["id", "task", "read_first", "also_consider"]:
            if key not in route:
                errors.append(f"routes.yaml route {index} missing {key}")
    return routes, errors


# Converts route dictionaries into map-compatible routing rows.
def rows_from_routes(routes: list[dict[str, object]]) -> list[tuple[str, str, str]]:
    rows = []
    for route in routes:
        read_first = [str(path) for path in route.get("read_first", [])]
        also_consider = [str(path) for path in route.get("also_consider", [])]
        rows.append((str(route.get("task", "")), format_route_paths(read_first), format_route_paths(also_consider)))
    return rows


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
        "routes.yaml": ROUTES_YAML,
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


# Updates a scaffold file when missing or when explicit overwrite is allowed.
def upgrade_scaffold_file(path: Path, content: str, write_existing: bool) -> str:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return "created"
    if path.read_text(encoding="utf-8") == content:
        return "current"
    if write_existing:
        path.write_text(content, encoding="utf-8")
        return "updated"
    return "needs review"


# Upgrades generated protocol files while leaving project-specific KB content intact.
def command_upgrade(args: argparse.Namespace) -> int:
    root = repo_root(args)
    kb = kb_dir(root)
    if not kb.exists():
        print("ERROR: missing .agent-kb/; run init first")
        return 1

    if not (kb / "inbox").exists():
        (kb / "inbox").mkdir(parents=True)

    protocol_action = upsert_agents_protocol(root)
    start_action = upgrade_scaffold_file(kb / "start.md", START_MD, args.write_start)
    routes_action = upgrade_scaffold_file(kb / "routes.yaml", ROUTES_YAML, args.write_routes)
    routes, route_errors = parse_routes_yaml(kb / "routes.yaml")
    map_content = render_map(routes) if not route_errors else MAP_MD
    map_action = upgrade_scaffold_file(kb / "map.md", map_content, args.write_map)
    custom_routes_preserved = routes_action == "needs review" and args.write_map and not args.write_routes and not route_errors

    print(f"Upgraded KB at {kb}")
    print(f"AGENTS.md protocol {protocol_action}.")
    print(f".agent-kb/start.md {start_action}.")
    if custom_routes_preserved:
        print(".agent-kb/routes.yaml custom routes preserved.")
    else:
        print(f".agent-kb/routes.yaml {routes_action}.")
    print(f".agent-kb/map.md {map_action}.")
    if start_action == "needs review":
        print("Review .agent-kb/start.md manually or rerun with --write-start to replace it.")
    if custom_routes_preserved:
        print("custom routes preserved; map generated from routes.yaml")
    elif routes_action == "needs review":
        print("Review .agent-kb/routes.yaml manually; use --write-routes only if you want the default routes.")
    if map_action == "needs review":
        print("Review .agent-kb/map.md manually; use --write-map only if you want the default routing table.")
    return 0


# Extracts the Task Routing rows from `.agent-kb/map.md` for legacy compatibility.
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


# Checks route shape rules that keep routing narrow and cheap to read.
def route_quality_warnings(routes: list[dict[str, object]]) -> list[str]:
    warnings = []
    for route in routes:
        route_id = str(route.get("id", "unknown"))
        read_first = route.get("read_first", [])
        also_consider = route.get("also_consider", [])
        if isinstance(read_first, list) and len(read_first) > 1:
            warnings.append(f"route {route_id} has more than one read_first path")
        if isinstance(also_consider, list) and len(also_consider) > 2:
            warnings.append(f"route {route_id} has more than two also_consider paths")
    return warnings


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
    for relative in ["start.md", "routes.yaml", "map.md"]:
        if not (kb / relative).exists():
            errors.append(f"missing .agent-kb/{relative}")
    if not (kb / "inbox").is_dir():
        errors.append("missing .agent-kb/inbox/")

    routes, route_errors = parse_routes_yaml(kb / "routes.yaml")
    errors.extend(route_errors)
    rows = rows_from_routes(routes)
    if routes and (kb / "map.md").exists() and (kb / "map.md").read_text(encoding="utf-8") != render_map(routes):
        warnings.append("map.md differs from routes.yaml; update the readable view or rerun upgrade --write-map")
    warnings.extend(route_quality_warnings(routes))

    for _, read_first, also_consider in rows:
        for raw in split_path_cell(read_first) + split_path_cell(also_consider):
            normalized = normalize_kb_path(raw)
            if normalized is None:
                errors.append(f"invalid route path: {raw}")
            elif not (kb / normalized).exists():
                errors.append(f"route path does not exist: {raw}")

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
            warnings.append(f"stable topic is not reachable from routes/map/links: {relative}")
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

    upgrade_parser = subparsers.add_parser("upgrade", help="Upgrade generated KB protocol files conservatively.")
    upgrade_parser.add_argument("--root", default=".", help="Repository root to manage.")
    upgrade_parser.add_argument("--write-start", action="store_true", help="Replace .agent-kb/start.md with the current template.")
    upgrade_parser.add_argument("--write-routes", action="store_true", help="Replace .agent-kb/routes.yaml with the default routes.")
    upgrade_parser.add_argument("--write-map", action="store_true", help="Replace .agent-kb/map.md with the default routing table.")
    upgrade_parser.set_defaults(func=command_upgrade)

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
