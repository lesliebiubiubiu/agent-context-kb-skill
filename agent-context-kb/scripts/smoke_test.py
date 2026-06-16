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


# Checks that validate warns when a stable topic is not reachable from map routes or links.
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
            "WARN: stable topic is not reachable from map/links: architecture/orphan.md" in result.stdout,
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


# Runs all smoke tests and prints a compact success line.
def main() -> int:
    tests = [
        test_compile_format,
        test_empty_note_body,
        test_path_traversal_target,
        test_relative_broken_link,
        test_unreachable_topic_warning,
        test_placeholder_warning,
    ]
    for test in tests:
        test()
    print(f"OK: {len(tests)} smoke checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
