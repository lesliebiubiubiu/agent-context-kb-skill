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
        "id": "planning",
        "task": "Planning / current focus",
        "read_first": ["plans/current.md"],
        "also_consider": ["decisions/active/project-decisions.md"],
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
  - id: planning
    task: Planning / current focus
    read_first:
      - plans/current.md
    also_consider:
      - decisions/active/project-decisions.md
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
| Planning / current focus | plans/current.md | decisions/active/project-decisions.md |
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

PLAN_CURRENT_MD = """# Current Plan

## Summary

No active plan recorded yet.

## Read When

- Continuing prior work or deciding the next project step

## Current Focus

None yet.

## Done

None yet.

## Next

None yet.

## Open Questions

None yet.

## Related

None yet.

## Change Log

- {today} - Created initial lightweight plan.
"""

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
PLACEHOLDER_TEXTS = (
    "No durable knowledge recorded yet.",
    "Add durable project knowledge here.",
)
TRIM_PLACEHOLDER_TEXTS = PLACEHOLDER_TEXTS + (
    "No entries yet.",
    "None yet.",
)
TRIM_MAX_FILE_LINES = 120
TRIM_MAX_TOTAL_CHARS = 20000
TRIM_MAX_INBOX_NOTES = 5


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


# Builds the lightweight current plan document with today's creation date.
def render_current_plan() -> str:
    return PLAN_CURRENT_MD.format(today=dt.date.today().isoformat())


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


# Renders route dictionaries into the supported routes.yaml subset.
def render_routes_yaml(routes: list[dict[str, object]]) -> str:
    lines = ["# Agent KB routes. Keep read_first narrow; use also_consider sparingly.", "routes:"]
    for route in routes:
        lines.append(f"  - id: {route.get('id', '')}")
        lines.append(f"    task: {route.get('task', '')}")
        for key in ["read_first", "also_consider"]:
            lines.append(f"    {key}:")
            for path in [str(path) for path in route.get(key, [])]:
                lines.append(f"      - {path}")
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
        kb / "plans",
    ]:
        directory.mkdir(parents=True, exist_ok=True)

    for relative, content in {
        "start.md": START_MD,
        "routes.yaml": ROUTES_YAML,
        "map.md": MAP_MD,
        "plans/current.md": render_current_plan(),
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
    plan_action = upgrade_scaffold_file(kb / "plans" / "current.md", render_current_plan(), args.write_plan)
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
    print(f".agent-kb/plans/current.md {plan_action}.")
    print(f".agent-kb/map.md {map_action}.")
    if start_action == "needs review":
        print("Review .agent-kb/start.md manually or rerun with --write-start to replace it.")
    if custom_routes_preserved:
        print("custom routes preserved; map generated from routes.yaml")
    elif routes_action == "needs review":
        print("Review .agent-kb/routes.yaml manually; use --write-routes only if you want the default routes.")
    if plan_action == "needs review":
        print("Review .agent-kb/plans/current.md manually; use --write-plan only if you want the default empty plan.")
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


# Checks whether a stable document has the sections agents need for skimming.
def topic_section_warnings(kb: Path) -> list[str]:
    warnings = []
    for path in stable_topic_docs(kb):
        relative = path.relative_to(kb)
        if relative.parts[0] == "plans":
            required = ["## Summary", "## Read When", "## Current Focus", "## Next"]
        else:
            required = ["## Summary", "## Read When", "## Current Knowledge"]
        text = path.read_text(encoding="utf-8")
        for section in required:
            if section not in text:
                warnings.append(f"{relative} missing {section}")
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


# Extracts the body of a second-level Markdown section by heading text.
def markdown_section_body(text: str, heading: str) -> str:
    pattern = re.compile(rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", re.M | re.S)
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


# Checks whether a document contains a named second-level Markdown section.
def has_markdown_section(text: str, heading: str) -> bool:
    return bool(re.search(rf"^## {re.escape(heading)}\s*$", text, re.M))


# Checks whether a section body contains only starter placeholder text.
def is_placeholder_body(body: str) -> bool:
    normalized = body.strip()
    if not normalized:
        return True
    normalized_lines = [line.strip("- ").strip() for line in normalized.splitlines() if line.strip()]
    return bool(normalized_lines) and all(line in TRIM_PLACEHOLDER_TEXTS for line in normalized_lines)


# Checks whether a changelog only records initial scaffold creation.
def is_initial_changelog_only(body: str) -> bool:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    return len(lines) == 1 and "Created initial KB topic." in lines[0]


# Checks whether a stable topic has empty content sections and no links, ignoring its Change Log.
def is_content_empty_topic(path: Path, kb: Path) -> bool:
    relative = path.relative_to(kb)
    if relative in {Path("start.md"), Path("map.md"), Path("plans/current.md")}:
        return False
    if relative.parts[0] in {"inbox", "plans"}:
        return False
    text = path.read_text(encoding="utf-8")
    for heading in ["Summary", "Current Knowledge", "Related", "Change Log"]:
        if not has_markdown_section(text, heading):
            return False
    return (
        is_placeholder_body(markdown_section_body(text, "Summary"))
        and is_placeholder_body(markdown_section_body(text, "Current Knowledge"))
        and is_placeholder_body(markdown_section_body(text, "Related"))
        and not markdown_links(text)
    )


# Checks whether a stable topic is a pristine empty scaffold that can be safely deleted.
def is_empty_scaffold_topic(path: Path, kb: Path) -> bool:
    if not is_content_empty_topic(path, kb):
        return False
    return is_initial_changelog_only(markdown_section_body(path.read_text(encoding="utf-8"), "Change Log"))


# Checks whether a stable topic is an emptied husk: content gone, but its Change Log shows it once held knowledge.
def is_husk_after_merge_topic(path: Path, kb: Path) -> bool:
    if not is_content_empty_topic(path, kb):
        return False
    return not is_initial_changelog_only(markdown_section_body(path.read_text(encoding="utf-8"), "Change Log"))


# Maps internal Markdown link targets to the KB documents that reference them.
def incoming_link_sources(kb: Path) -> dict[Path, list[Path]]:
    incoming: dict[Path, list[Path]] = {}
    for source in kb.rglob("*.md"):
        source_relative = source.relative_to(kb)
        for raw in markdown_links(source.read_text(encoding="utf-8")):
            target = resolve_internal_link(source, kb, raw)
            if target is not None:
                incoming.setdefault(target, []).append(source_relative)
    return incoming


# Finds empty scaffold topic files that trim can remove without breaking links.
def empty_scaffold_topics(kb: Path) -> list[Path]:
    candidates = [path for path in stable_topic_docs(kb) if is_empty_scaffold_topic(path, kb)]
    candidate_relatives = {path.relative_to(kb) for path in candidates}
    incoming = incoming_link_sources(kb)
    safe = []
    for path in candidates:
        relative = path.relative_to(kb)
        linked_from_kept_docs = [source for source in incoming.get(relative, []) if source not in candidate_relatives]
        if not linked_from_kept_docs:
            safe.append(path)
    return safe


# Finds emptied husks left after a merge; trim only warns about them because deleting a file with history is a judged act.
def husk_after_merge_topics(kb: Path) -> list[Path]:
    return [path for path in stable_topic_docs(kb) if is_husk_after_merge_topic(path, kb)]


# Returns the path used to invoke this script so suggested commands copy-paste cleanly under symlinks.
def script_invocation() -> str:
    return sys.argv[0] or "agent_kb.py"


# Builds the agent handoff: the semantic step (agent judgment) plus the deterministic finisher commands to run after.
def trim_compact_prompt(args: argparse.Namespace) -> str:
    script = script_invocation()
    return (
        "  Semantic step (agent judgment): within the existing headings, merge\n"
        "  overlapping sections and keep each Summary / Read When dense (what the\n"
        "  file is, the current conclusion, the traps). Preserve durable decisions,\n"
        "  constraints, failures, and links. Change Log is history: collapse routine\n"
        "  entries (e.g. inbox merges) but keep ones that record a real decision or\n"
        "  change. Do not change the schema or add headings.\n"
        "  Then close the loop with the deterministic tools:\n"
        f"    python3 {script} upgrade --root {args.root} --write-map   # regenerate map.md if you changed routes.yaml\n"
        f"    python3 {script} validate --root {args.root}              # confirm links, schema, reachability, map==routes\n"
        f"    python3 {script} trim --root {args.root}                  # re-diagnose; repeat the loop until it reports lean"
    )


# Builds the rerun command from this script's actual invocation path so copy-paste works under symlinks.
def trim_write_command(args: argparse.Namespace) -> str:
    return f"python3 {script_invocation()} trim --root {args.root} --write"


# Checks whether KB size suggests agent-assisted semantic compacting, reporting concrete numbers per signal.
def trim_compact_recommended(
    kb: Path,
    max_file_lines: int = TRIM_MAX_FILE_LINES,
    max_total_chars: int = TRIM_MAX_TOTAL_CHARS,
    max_inbox_notes: int = TRIM_MAX_INBOX_NOTES,
) -> tuple[bool, list[str]]:
    reasons = []
    total_chars = 0
    oversize = []
    for path in stable_topic_docs(kb):
        text = path.read_text(encoding="utf-8")
        total_chars += len(text)
        line_count = len(text.splitlines())
        if line_count > max_file_lines:
            oversize.append((path.relative_to(kb), line_count))
    if total_chars > max_total_chars:
        reasons.append(f"stable KB docs total {total_chars} chars (max {max_total_chars})")
    for relative, line_count in oversize:
        reasons.append(f"{relative} is {line_count} lines (max {max_file_lines})")
    inbox_count = len(list((kb / "inbox").glob("*.md"))) if (kb / "inbox").exists() else 0
    if inbox_count > max_inbox_notes:
        reasons.append(f"inbox has {inbox_count} notes (max {max_inbox_notes})")
    return bool(reasons), reasons


# Removes deleted topic paths from routes and promotes remaining entries when needed.
def prune_routes(routes: list[dict[str, object]], deleted: set[Path]) -> tuple[list[dict[str, object]], bool]:
    changed = False
    pruned = []
    deleted_strings = {str(path) for path in deleted}
    for route in routes:
        read_first = [str(path) for path in route.get("read_first", []) if str(path) not in deleted_strings]
        also_consider = [str(path) for path in route.get("also_consider", []) if str(path) not in deleted_strings]
        if read_first != route.get("read_first", []) or also_consider != route.get("also_consider", []):
            changed = True
        if not read_first and also_consider:
            read_first = [also_consider.pop(0)]
            changed = True
        if not read_first and not also_consider:
            changed = True
            continue
        updated = dict(route)
        updated["read_first"] = read_first
        updated["also_consider"] = also_consider
        pruned.append(updated)
    return pruned, changed


# Runs KB validation without printing so maintenance commands can summarize it.
def validate_kb(kb: Path) -> tuple[list[str], list[str]]:
    errors = []
    warnings = []

    if not kb.exists():
        return ["missing .agent-kb/"], warnings
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
    return errors, warnings


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
    errors, warnings = validate_kb(kb)

    for warning in warnings:
        print(f"WARN: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        return 1
    print(f"OK: validated {kb}")
    return 0


# Diagnoses or applies safe deterministic KB trimming.
def command_trim(args: argparse.Namespace) -> int:
    root = repo_root(args)
    kb = kb_dir(root)
    if not kb.exists():
        print("ERROR: missing .agent-kb/")
        return 1

    routes, route_errors = parse_routes_yaml(kb / "routes.yaml")
    if route_errors:
        for error in route_errors:
            print(f"ERROR: {error}")
        return 1

    candidates = empty_scaffold_topics(kb)
    husks = husk_after_merge_topics(kb)
    compact_recommended, compact_reasons = trim_compact_recommended(
        kb, args.max_file_lines, args.max_total_chars, args.max_inbox_notes
    )

    if not args.write:
        if not candidates and not husks and not compact_recommended:
            print("Trim diagnosis: KB is already lean.")
            print("No write step recommended.")
            return 0
        diagnosis = "cleanup recommended" if candidates or husks else "compact recommended"
        print(f"Trim diagnosis: {diagnosis}.")
        print()
        print("Details:")
        for path in candidates:
            print(f"- delete empty scaffold topic: {path.relative_to(kb)}")
        for path in husks:
            print(f"- husk after merge: {path.relative_to(kb)} (content empty, Change Log grown; delete manually)")
        for reason in compact_reasons:
            print(f"- compact signal: {reason}")
        if candidates:
            print()
            print("Next:")
            print(f"  {trim_write_command(args)}")
        print()
        print("Agent compact prompt:")
        print(trim_compact_prompt(args))
        return 0

    deleted = set()
    for path in candidates:
        path.unlink()
        deleted.add(path.relative_to(kb))

    pruned_routes, routes_changed = prune_routes(routes, deleted)
    if deleted or routes_changed:
        (kb / "routes.yaml").write_text(render_routes_yaml(pruned_routes), encoding="utf-8")
        (kb / "map.md").write_text(render_map(pruned_routes), encoding="utf-8")

    errors, _ = validate_kb(kb)
    print("Trim complete.")
    print(f"- Deleted {len(deleted)} empty topic nodes.")
    print(f"- Updated routes.yaml: {'yes' if deleted or routes_changed else 'no'}.")
    print(f"- Regenerated map.md: {'yes' if deleted or routes_changed else 'no'}.")
    print(f"- Validate: {'OK' if not errors else 'FAILED'}.")
    if deleted:
        print("Deleted nodes:")
        for path in sorted(deleted):
            print(f"- {path}")
    if husks:
        print("Husks after merge (delete manually):")
        for path in sorted(husks):
            print(f"- {path.relative_to(kb)}")
    if compact_recommended:
        print("Agent compact still recommended for non-empty topics.")
    for error in errors:
        print(f"ERROR: {error}")
    return 1 if errors else 0


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
    upgrade_parser.add_argument("--write-plan", action="store_true", help="Replace .agent-kb/plans/current.md with the default empty plan.")
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

    trim_parser = subparsers.add_parser("trim", help="Diagnose or apply safe KB trimming.")
    trim_parser.add_argument("--root", default=".", help="Repository root to manage.")
    trim_parser.add_argument("--write", action="store_true", help="Apply deterministic cleanup and validate.")
    trim_parser.add_argument("--max-file-lines", type=int, default=TRIM_MAX_FILE_LINES, help=f"Lines above which a topic is flagged for compacting (default {TRIM_MAX_FILE_LINES}).")
    trim_parser.add_argument("--max-total-chars", type=int, default=TRIM_MAX_TOTAL_CHARS, help=f"Total stable-doc character budget before compacting is suggested (default {TRIM_MAX_TOTAL_CHARS}).")
    trim_parser.add_argument("--max-inbox-notes", type=int, default=TRIM_MAX_INBOX_NOTES, help=f"Inbox note count above which compacting is suggested (default {TRIM_MAX_INBOX_NOTES}).")
    trim_parser.add_argument("--verbose", action="store_true", help="Deprecated: trim now prints details by default.")
    trim_parser.set_defaults(func=command_trim)
    return parser


# Parses arguments and dispatches the selected command.
def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
