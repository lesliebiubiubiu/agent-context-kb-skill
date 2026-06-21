#!/usr/bin/env python3
"""Run lightweight edge checks for the agent KB CLI."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("agent_kb.py")


# Runs the KB CLI against a temporary repository and captures output for assertions.
def run_cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--root", str(root)],
        check=False,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


# Fails the smoke test with command output when an expected condition is false.
def require(condition: bool, message: str, result: subprocess.CompletedProcess[str] | None = None) -> None:
    if condition:
        return
    if result is not None:
        details = f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    else:
        details = ""
    raise AssertionError(f"{message}{details}")


# Creates a fresh KB scaffold in a temporary repository.
def init_root(root: Path) -> None:
    result = run_cli(root, "init")
    require(result.returncode == 0, "init should succeed", result)


# Checks the happy path and verifies compile leaves a blank line before the next heading.
def test_compile_format() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        result = run_cli(
            root,
            "note",
            "--title",
            "Format Check",
            "--target",
            "architecture/overview.md",
            "--body",
            "Merged body.",
        )
        require(result.returncode == 0, "note should succeed", result)
        result = run_cli(root, "compile")
        require(result.returncode == 0, "compile should succeed", result)
        text = (root / ".agent-kb" / "architecture" / "overview.md").read_text(encoding="utf-8")
        require("Merged body.\n\n## Related" in text, "compiled note should be separated from the next heading")


# Checks that note rejects an empty body before creating inbox files.
def test_empty_note_body() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        result = run_cli(root, "note", "--title", "Empty", "--body", "")
        require(result.returncode == 1, "empty note body should fail", result)
        require("ERROR: note body is empty" in result.stderr, "empty note body should explain the failure", result)


# Checks that compile keeps notes whose target would escape the KB tree.
def test_path_traversal_target() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        result = run_cli(root, "note", "--title", "Escape", "--target", "../AGENTS.md", "--body", "Nope.")
        require(result.returncode == 0, "note with unsafe target should still be recorded", result)
        result = run_cli(root, "compile")
        require(result.returncode == 0, "compile should keep unsafe target notes unresolved", result)
        require("Unresolved: 1" in result.stdout, "unsafe target should remain unresolved", result)
        inbox_files = list((root / ".agent-kb" / "inbox").glob("*.md"))
        require(len(inbox_files) == 1, "unsafe target note should stay in inbox")


# Checks that validate reports document-relative broken links that normalize inside the KB.
def test_relative_broken_link() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        result = run_cli(
            root,
            "note",
            "--title",
            "Bad Link",
            "--target",
            "architecture/overview.md",
            "--body",
            "[bad](nested/../missing.md)",
        )
        require(result.returncode == 0, "bad link note should be recorded", result)
        result = run_cli(root, "compile")
        require(result.returncode == 0, "bad link note should compile into target", result)
        result = run_cli(root, "validate")
        require(result.returncode == 1, "validate should fail on normalized broken links", result)
        require("broken link in architecture/overview.md: nested/../missing.md" in result.stdout, "broken link should be reported", result)


# Checks that validate warns when a stable topic is not reachable from routes, map, or links.
def test_unreachable_topic_warning() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        orphan = root / ".agent-kb" / "architecture" / "orphan.md"
        orphan.write_text(
            """# Orphan

## Summary

Not routed.

## Read When

- Testing reachability.

## Current Knowledge

Unreachable topic.
""",
            encoding="utf-8",
        )
        result = run_cli(root, "validate")
        require(result.returncode == 0, "unreachable topics should warn without failing", result)
        require(
            "WARN: stable topic is not reachable from routes/map/links: architecture/orphan.md" in result.stdout,
            "unreachable topic warning should be reported",
            result,
        )


# Checks that validate warns when old starter placeholder text remains in a topic.
def test_placeholder_warning() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        topic = root / ".agent-kb" / "architecture" / "overview.md"
        topic.write_text(
            topic.read_text(encoding="utf-8").replace("No entries yet.", "No durable knowledge recorded yet."),
            encoding="utf-8",
        )
        result = run_cli(root, "validate")
        require(result.returncode == 0, "placeholder text should warn without failing", result)
        require(
            "WARN: architecture/overview.md still contains placeholder text: No durable knowledge recorded yet."
            in result.stdout,
            "placeholder warning should be reported",
            result,
        )


# Checks that validate uses routes.yaml as the canonical route source.
def test_validate_uses_routes_yaml() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        routes = root / ".agent-kb" / "routes.yaml"
        routes.write_text(
            routes.read_text(encoding="utf-8").replace("architecture/overview.md", "architecture/missing.md", 1),
            encoding="utf-8",
        )
        result = run_cli(root, "validate")
        require(result.returncode == 1, "validate should fail on missing routes.yaml paths", result)
        require("ERROR: route path does not exist: architecture/missing.md" in result.stdout, "routes.yaml path should be checked", result)


# Checks that init creates the lightweight current plan and routes to it.
def test_init_creates_current_plan_route() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        plan = root / ".agent-kb" / "plans" / "current.md"
        routes = (root / ".agent-kb" / "routes.yaml").read_text(encoding="utf-8")
        require(plan.exists(), "init should create plans/current.md")
        require("## Current Focus" in plan.read_text(encoding="utf-8"), "current plan should use plan sections")
        require("plans/current.md" in routes, "routes.yaml should include the current plan route")


# Checks that upgrade creates the current plan when older KBs do not have it.
def test_upgrade_creates_missing_current_plan() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        plan = root / ".agent-kb" / "plans" / "current.md"
        plan.unlink()
        result = run_cli(root, "upgrade")
        require(result.returncode == 0, "upgrade should succeed when current plan is missing", result)
        require(".agent-kb/plans/current.md created." in result.stdout, "upgrade should report current plan creation", result)
        require(plan.exists(), "upgrade should recreate missing current plan")


# Checks that upgrade reports customized scaffold files without replacing them by default.
def test_upgrade_preserves_custom_scaffold_by_default() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        start = root / ".agent-kb" / "start.md"
        route_map = root / ".agent-kb" / "map.md"
        start.write_text("# Custom Start\n", encoding="utf-8")
        route_map.write_text("# Custom Map\n", encoding="utf-8")
        result = run_cli(root, "upgrade")
        require(result.returncode == 0, "upgrade should succeed on an existing KB", result)
        require(".agent-kb/start.md needs review." in result.stdout, "custom start should need review", result)
        require(".agent-kb/map.md needs review." in result.stdout, "custom map should need review", result)
        require(start.read_text(encoding="utf-8") == "# Custom Start\n", "upgrade should preserve custom start by default")
        require(route_map.read_text(encoding="utf-8") == "# Custom Map\n", "upgrade should preserve custom map by default")


# Checks that upgrade can explicitly replace start.md with the current protocol template.
def test_upgrade_can_write_start_template() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        start = root / ".agent-kb" / "start.md"
        start.write_text("# Custom Start\n", encoding="utf-8")
        result = run_cli(root, "upgrade", "--write-start")
        require(result.returncode == 0, "upgrade --write-start should succeed", result)
        require(".agent-kb/start.md updated." in result.stdout, "write-start should report update", result)
        require("This directory is the project knowledge base" in start.read_text(encoding="utf-8"), "write-start should restore template")


# Checks that upgrade renders map.md from the current routes.yaml file.
def test_upgrade_writes_map_from_routes() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        routes = root / ".agent-kb" / "routes.yaml"
        route_map = root / ".agent-kb" / "map.md"
        routes.write_text(
            """routes:
  - id: docs
    task: Documentation
    read_first:
      - workflows/local-dev.md
    also_consider:
      - conventions/comments.md
""",
            encoding="utf-8",
        )
        route_map.write_text("# stale map\n", encoding="utf-8")
        result = run_cli(root, "upgrade", "--write-map")
        require(result.returncode == 0, "upgrade --write-map should succeed", result)
        require(".agent-kb/routes.yaml custom routes preserved." in result.stdout, "custom routes should be reported as preserved", result)
        require("custom routes preserved; map generated from routes.yaml" in result.stdout, "write-map should explain route preservation", result)
        text = route_map.read_text(encoding="utf-8")
        require("| Documentation | workflows/local-dev.md | conventions/comments.md |" in text, "write-map should render current routes")


# Checks that trim diagnosis stays short while pointing to the write step.
def test_trim_diagnoses_empty_scaffold() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        result = run_cli(root, "trim")
        require(result.returncode == 0, "trim diagnosis should succeed", result)
        require("Trim diagnosis: cleanup recommended." in result.stdout, "trim should recommend cleanup", result)
        require("trim --root" in result.stdout and "--write" in result.stdout, "trim should show the write command", result)
        require("Agent compact prompt:" in result.stdout, "trim should print the compact prompt", result)


# Checks that trim --write deletes empty topics and prunes routes and map output.
def test_trim_write_deletes_empty_scaffold_topics() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        result = run_cli(root, "trim", "--write")
        require(result.returncode == 0, "trim --write should succeed", result)
        require("Trim complete." in result.stdout, "trim --write should summarize completion", result)
        require("- Validate: OK." in result.stdout, "trim --write should validate", result)
        require(not (root / ".agent-kb" / "architecture" / "overview.md").exists(), "empty topic should be deleted")
        require((root / ".agent-kb" / "plans" / "current.md").exists(), "current plan should never be deleted")
        routes = (root / ".agent-kb" / "routes.yaml").read_text(encoding="utf-8")
        route_map = (root / ".agent-kb" / "map.md").read_text(encoding="utf-8")
        require("architecture/overview.md" not in routes, "deleted topic should be pruned from routes")
        require("Architecture / module boundaries" not in route_map, "empty route should be pruned from map")


# Checks that trim promotes a remaining also_consider entry when read_first is deleted.
def test_trim_write_promotes_remaining_route_entry() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        test_env = root / ".agent-kb" / "debugging" / "test-environment.md"
        test_env.write_text(
            test_env.read_text(encoding="utf-8").replace("No entries yet.", "Test environment notes."),
            encoding="utf-8",
        )
        result = run_cli(root, "trim", "--write")
        require(result.returncode == 0, "trim --write should succeed when promoting entries", result)
        require(not (root / ".agent-kb" / "debugging" / "known-failures.md").exists(), "empty read_first should be deleted")
        require(test_env.exists(), "non-empty also_consider should remain")
        routes = (root / ".agent-kb" / "routes.yaml").read_text(encoding="utf-8")
        require("task: Debugging / known failures" in routes, "route with promoted entry should remain")
        require("read_first:\n      - debugging/test-environment.md" in routes, "remaining entry should be promoted")


# Checks that trim keeps non-empty topics even when they still resemble scaffold files.
def test_trim_write_keeps_non_empty_topic() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        topic = root / ".agent-kb" / "architecture" / "overview.md"
        topic.write_text(
            topic.read_text(encoding="utf-8").replace("None yet.", "The CLI lives in `agent-kb/scripts/agent_kb.py`."),
            encoding="utf-8",
        )
        result = run_cli(root, "trim", "--write")
        require(result.returncode == 0, "trim --write should succeed with non-empty topics", result)
        require(topic.exists(), "non-empty topic should be kept")
        routes = (root / ".agent-kb" / "routes.yaml").read_text(encoding="utf-8")
        require("architecture/overview.md" in routes, "route should keep non-empty read_first")


# Checks that trim does not delete malformed topics just because sections are missing.
def test_trim_write_keeps_malformed_topic() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        topic = root / ".agent-kb" / "architecture" / "overview.md"
        topic.write_text("# Architecture Overview\n\nNo entries yet.\n", encoding="utf-8")
        result = run_cli(root, "trim", "--write")
        require(result.returncode == 0, "trim --write should leave validation warnings non-fatal", result)
        require(topic.exists(), "malformed topic should not be auto-deleted")


# Checks that trim keeps empty topics when non-deleted docs still link to them.
def test_trim_write_keeps_linked_empty_topic() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        overview = root / ".agent-kb" / "architecture" / "overview.md"
        boundaries = root / ".agent-kb" / "architecture" / "boundaries.md"
        overview.write_text(
            overview.read_text(encoding="utf-8").replace("None yet.", "See [boundaries](boundaries.md)."),
            encoding="utf-8",
        )
        result = run_cli(root, "trim", "--write")
        require(result.returncode == 0, "trim --write should keep linked empty topics valid", result)
        require(overview.exists(), "linking non-empty topic should remain")
        require(boundaries.exists(), "linked empty topic should not be auto-deleted")


# Runs all smoke tests and prints a compact success line.
def main() -> int:
    tests = [
        test_compile_format,
        test_empty_note_body,
        test_path_traversal_target,
        test_relative_broken_link,
        test_unreachable_topic_warning,
        test_placeholder_warning,
        test_validate_uses_routes_yaml,
        test_init_creates_current_plan_route,
        test_upgrade_creates_missing_current_plan,
        test_upgrade_preserves_custom_scaffold_by_default,
        test_upgrade_can_write_start_template,
        test_upgrade_writes_map_from_routes,
        test_trim_diagnoses_empty_scaffold,
        test_trim_write_deletes_empty_scaffold_topics,
        test_trim_write_promotes_remaining_route_entry,
        test_trim_write_keeps_non_empty_topic,
        test_trim_write_keeps_malformed_topic,
        test_trim_write_keeps_linked_empty_topic,
    ]
    for test in tests:
        test()
    print(f"OK: {len(tests)} smoke checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
