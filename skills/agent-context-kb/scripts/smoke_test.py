#!/usr/bin/env python3
"""Run lightweight edge checks for the agent KB CLI."""

from __future__ import annotations

import json
import importlib.util
import re
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("agent_kb.py")
DEV_COMPLIANCE_SCRIPT = Path(__file__).parent / "dev" / "compliance_analyzer.py"
EVAL_RUNNER = Path(__file__).resolve().parents[3] / "evals" / "run_bundle.py"
sys.path.insert(0, str(Path(__file__).parent))
from transcript_reads import claude_project_name  # noqa: E402


# Runs the KB CLI against a temporary repository and captures output for assertions.
def run_cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--root", str(root)],
        check=False,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


# Runs the private compliance analyzer against temporary transcript directories.
def run_compliance(root: Path, claude_dir: Path, codex_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(DEV_COMPLIANCE_SCRIPT),
            "--root",
            str(root),
            "--claude-dir",
            str(claude_dir),
            "--codex-dir",
            str(codex_dir),
            *args,
        ],
        check=False,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


# Writes JSONL records for synthetic transcript fixtures.
def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


# Writes a JSON-compatible YAML document for eval runner fixtures.
def write_json_yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


# Loads the eval runner module so smoke checks can exercise pure parser helpers.
def load_eval_runner_module():
    spec = importlib.util.spec_from_file_location("eval_run_bundle", EVAL_RUNNER)
    require(spec is not None and spec.loader is not None, "eval runner module should be loadable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Runs git in a fixture repository and returns stripped stdout.
def run_fixture_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    require(result.returncode == 0, f"fixture git {' '.join(args)} should succeed", result)
    return result.stdout.strip()


# Creates a minimal git repo and returns its current HEAD commit.
def create_fixture_commit(repo: Path, files: dict[str, str]) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    run_fixture_git(repo, "init")
    run_fixture_git(repo, "config", "user.email", "agent-kb@example.test")
    run_fixture_git(repo, "config", "user.name", "Agent KB Smoke")
    for relative, text in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    run_fixture_git(repo, "add", ".")
    run_fixture_git(repo, "commit", "-m", "fixture")
    return run_fixture_git(repo, "rev-parse", "HEAD")


# Builds the fixture Claude project directory with the observed dot-to-dash encoding.
def fixture_claude_project_name(root: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(root.resolve()))


# Checks Claude's observed project-directory encoding for punctuation-heavy paths.
def test_claude_project_name_observed_encoding() -> None:
    root = Path("/Users/lsl/Desktop/glucose/.claude/worktrees/rustling_jumping_volcano")
    expected = "-Users-lsl-Desktop-glucose--claude-worktrees-rustling-jumping-volcano"
    require(claude_project_name(root) == expected, "Claude project encoding should replace non-alphanumerics")


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


# Checks that init respects a CLAUDE.md-primary repo when AGENTS.md is only a pointer.
def test_init_uses_claude_when_agents_points_to_it() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        agents = root / "AGENTS.md"
        claude = root / "CLAUDE.md"
        agents.write_text("See CLAUDE.md for agent instructions.\n", encoding="utf-8")
        claude.write_text("# Claude Instructions\n\nKeep the main instructions here.\n", encoding="utf-8")
        result = run_cli(root, "init")
        require(result.returncode == 0, "init should succeed with CLAUDE.md-primary instructions", result)
        require("CLAUDE.md protocol appended." in result.stdout, "init should report the CLAUDE.md protocol target", result)
        require(agents.read_text(encoding="utf-8") == "See CLAUDE.md for agent instructions.\n", "AGENTS.md pointer should be left unchanged")
        require("## Project Knowledge Base" in claude.read_text(encoding="utf-8"), "CLAUDE.md should receive the KB protocol")


# Checks that upgrade refreshes the KB protocol in CLAUDE.md when that is the existing protocol owner.
def test_upgrade_updates_claude_protocol_owner() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        (root / "AGENTS.md").write_text("See CLAUDE.md for agent instructions.\n", encoding="utf-8")
        (root / "CLAUDE.md").write_text("# Claude Instructions\n\nKeep the main instructions here.\n", encoding="utf-8")
        init_root(root)
        claude = root / "CLAUDE.md"
        claude.write_text("# Claude Instructions\n\n## Project Knowledge Base\n\nOld protocol.\n", encoding="utf-8")
        result = run_cli(root, "upgrade")
        require(result.returncode == 0, "upgrade should succeed with CLAUDE.md as protocol owner", result)
        require("CLAUDE.md protocol updated." in result.stdout, "upgrade should report the CLAUDE.md protocol target", result)
        text = claude.read_text(encoding="utf-8")
        require("Old protocol." not in text, "upgrade should replace the old CLAUDE.md protocol section")
        require("Use `.agent-kb/` before broad code search" in text, "upgrade should write the current protocol into CLAUDE.md")


# Checks that CLAUDE.md receives the protocol while an existing AGENTS.md copy stays synced.
def test_claude_pointer_takes_protocol_ownership() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        agents = root / "AGENTS.md"
        claude = root / "CLAUDE.md"
        agents.write_text(
            "# Repo Instructions\n\n"
            "## Project Knowledge Base\n\n"
            "Old protocol.\n\n"
            "## Commands\n\n"
            "Run tests.\n",
            encoding="utf-8",
        )
        claude.write_text("# Claude Instructions\n\nSee AGENTS.md for agent instructions.\n", encoding="utf-8")
        result = run_cli(root, "init")
        require(result.returncode == 0, "init should succeed with CLAUDE.md pointing to AGENTS.md", result)
        require("CLAUDE.md protocol appended." in result.stdout, "init should report CLAUDE.md as the protocol target", result)
        require("## Project Knowledge Base" in claude.read_text(encoding="utf-8"), "CLAUDE.md should receive the KB protocol")
        agents_text = agents.read_text(encoding="utf-8")
        require("Old protocol." not in agents_text, "AGENTS.md should receive the synced current KB protocol")
        require("Use `.agent-kb/` before broad code search" in agents_text, "AGENTS.md should keep a Codex-visible protocol")
        require("## Commands" in agents_text, "AGENTS.md should preserve unrelated instructions")


# Checks that upgrade replaces the old long runtime protocol with the slim protocol.
def test_upgrade_replaces_long_runtime_protocol() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        agents = root / "AGENTS.md"
        agents.write_text(
            """# Agent Instructions

## Project Knowledge Base

`.agent-kb/` is the project knowledge base for coding agents.

When you need to understand how this codebase works, start here.

1. Read `.agent-kb/start.md`.
2. Read `.agent-kb/routes.yaml`.
3. Read the KB documents those routes point to.

After the task, only when it created reusable project knowledge:
- Update the relevant topic file.
- Do not write progress logs.
""",
            encoding="utf-8",
        )
        result = run_cli(root, "upgrade")
        require(result.returncode == 0, "upgrade should succeed with an old long protocol", result)
        text = agents.read_text(encoding="utf-8")
        require("Use `.agent-kb/` before broad code search" in text, "upgrade should write the slim protocol", result)
        require("When you need to understand how this codebase works" not in text, "upgrade should remove old rationale prose")


# Checks that trim diagnosis names concrete candidates by default and points to the write step.
def test_trim_diagnoses_empty_scaffold() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        result = run_cli(root, "trim")
        require(result.returncode == 0, "trim diagnosis should succeed", result)
        require("Trim diagnosis: cleanup recommended." in result.stdout, "trim should recommend cleanup", result)
        require("Details:" in result.stdout, "trim should print details by default", result)
        require(
            "delete empty scaffold topic: architecture/overview.md" in result.stdout,
            "trim should name the concrete deletion candidate by default",
            result,
        )
        require("trim --root" in result.stdout and "--write" in result.stdout, "trim should show the write command", result)
        require(
            "Agent compact prompt:" not in result.stdout,
            "deletable scaffolds are deterministic cleanup, not semantic compaction; no compact prompt",
            result,
        )


# Checks that a low --max-file-lines flag flags a topic with a magnitude, and that line-only overage stays minor.
def test_trim_threshold_flag_reports_oversize_topic() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        topic = root / ".agent-kb" / "architecture" / "overview.md"
        topic.write_text(
            topic.read_text(encoding="utf-8").replace("None yet.", "Real content.\n" + "padding line\n" * 30),
            encoding="utf-8",
        )
        result = run_cli(root, "trim", "--max-file-lines", "10")
        require(result.returncode == 0, "trim with a custom threshold should succeed", result)
        require(
            "architecture/overview.md:" in result.stdout and "over /" in result.stdout,
            "trim should report the oversized topic with overage magnitude",
            result,
        )
        require(
            "minor signal: architecture/overview.md:" in result.stdout,
            "line-only overage should stay minor, not be promoted to a compact recommendation",
            result,
        )


# Checks that char overage is caught even when the line count is well under budget (line-count can't game it).
def test_trim_flags_char_oversize_with_few_lines() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        topic = root / ".agent-kb" / "architecture" / "overview.md"
        # One long line: far under any line budget but far over a small char budget.
        topic.write_text(
            topic.read_text(encoding="utf-8").replace("None yet.", "blah " * 200),
            encoding="utf-8",
        )
        result = run_cli(root, "trim", "--max-file-lines", "1000", "--max-file-chars", "200")
        require(result.returncode == 0, "trim should succeed with a custom char budget", result)
        require(
            "lines (ok)" in result.stdout and "chars (" in result.stdout and "major" in result.stdout,
            "trim should flag char overage as major even when lines are under budget",
            result,
        )
        require(
            "only a proxy" in result.stdout and "genuine, distinct durable facts" in result.stdout,
            "a compact recommendation should always carry the proxy / stop-on-genuine guardrail",
            result,
        )


# Checks that a small overage is reported as minor/optional, not a full compact recommendation.
def test_trim_minor_overage_is_optional() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        kb = root / ".agent-kb"
        # Fill every empty scaffold so they are neither deletion candidates nor noise in the diagnosis.
        for path in kb.rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            if "None yet." in text:
                path.write_text(text.replace("None yet.", "Durable content for this topic."), encoding="utf-8")
        # Push one topic just over the default 120-line budget (well under the 10% major threshold).
        overview = kb / "architecture" / "overview.md"
        overview.write_text(overview.read_text(encoding="utf-8") + "\n".join("- detail" for _ in range(110)) + "\n", encoding="utf-8")
        result = run_cli(root, "trim")
        require(result.returncode == 0, "trim should succeed on a minor overage", result)
        require("Trim diagnosis: minor — optional." in result.stdout, "small overage should be minor, not compact", result)
        require("Safe to stop here" in result.stdout, "minor overage should tell the agent it can stop", result)
        require("Agent compact prompt:" not in result.stdout, "minor overage should not emit the compact prompt", result)


# Checks that --recheck runs validate inline so the compaction loop is one command per round.
def test_trim_recheck_runs_validate() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        result = run_cli(root, "trim", "--recheck")
        require(result.returncode == 0, "trim --recheck should succeed", result)
        require("Recheck (validate):" in result.stdout, "trim --recheck should print a validate summary", result)


# Checks that trim flags an emptied husk but never auto-deletes it (its Change Log carries history).
def test_trim_flags_husk_after_merge_without_deleting() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        topic = root / ".agent-kb" / "architecture" / "overview.md"
        # Grow the Change Log so the emptied file looks like a post-merge husk, not a pristine scaffold.
        topic.write_text(
            topic.read_text(encoding="utf-8").rstrip() + "\n- 2026-06-20 - Merged inbox note `legacy`.\n",
            encoding="utf-8",
        )
        result = run_cli(root, "trim")
        require(result.returncode == 0, "trim diagnosis should succeed with a husk", result)
        require(
            "husk after merge: architecture/overview.md" in result.stdout,
            "trim should flag the emptied husk",
            result,
        )
        require(
            "delete empty scaffold topic: architecture/overview.md" not in result.stdout,
            "a husk with grown Change Log should not be offered for auto-deletion",
            result,
        )
        result = run_cli(root, "trim", "--write")
        require(result.returncode == 0, "trim --write should succeed alongside a husk", result)
        require(topic.exists(), "trim --write must not delete a husk")
        require("Husks after merge (delete manually):" in result.stdout, "write mode should remind about husks", result)


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


# Checks that init writes a KB-local .gitignore and upgrade restores it when missing.
def test_kb_gitignore_init_and_upgrade() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        ignore = root / ".agent-kb" / ".gitignore"
        require(ignore.exists(), "init should create .agent-kb/.gitignore")
        require(".log/" in ignore.read_text(encoding="utf-8"), "KB gitignore should ignore the .log/ dir")
        ignore.unlink()
        result = run_cli(root, "upgrade")
        require(result.returncode == 0, "upgrade should succeed when KB gitignore is missing", result)
        require(".agent-kb/.gitignore created." in result.stdout, "upgrade should report KB gitignore creation", result)
        require(ignore.exists(), "upgrade should recreate the KB gitignore")


# Checks that validate logs structured metrics plus redacted args, and stats surfaces KB health.
def test_validate_metrics_and_health() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        orphan = root / ".agent-kb" / "architecture" / "orphan.md"
        orphan.write_text(
            "# Orphan\n\n## Summary\n\nx\n\n## Read When\n\n- t\n\n## Current Knowledge\n\nUnreachable.\n",
            encoding="utf-8",
        )
        result = run_cli(root, "validate")
        require(result.returncode == 0, "validate should succeed with only warnings", result)
        log = (root / ".agent-kb" / ".log" / "events.jsonl").read_text(encoding="utf-8")
        events = [json.loads(line) for line in log.splitlines() if line.strip()]
        validate_events = [event for event in events if event.get("command") == "validate"]
        require(bool(validate_events), "validate run should be logged")
        last = validate_events[-1]
        require("args" in last, "event should record redacted args")
        require("metrics" in last and last["metrics"]["warnings"] >= 1, "validate should log a warning-count metric", result)
        result = run_cli(root, "stats", "--no-backfill")
        require("Latest outcomes (per command)" in result.stdout, "stats should show the per-command outcomes section", result)
        require("warnings=" in result.stdout, "stats outcomes should show the validate warning count", result)


# Checks that free-text note args are redacted in the event log (secrets rule).
def test_note_body_redacted_in_log() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        secret = "SENSITIVE-BODY-TEXT"
        result = run_cli(root, "note", "--title", "T", "--target", "architecture/overview.md", "--body", secret)
        require(result.returncode == 0, "note should succeed", result)
        log = (root / ".agent-kb" / ".log" / "events.jsonl").read_text(encoding="utf-8")
        require(secret not in log, "note body must never appear in the event log")
        require("<redacted:" in log, "redacted free-text args should be marked")


# Checks that CLI runs are logged and that stats reports command usage.
def test_stats_reports_cli_usage() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        run_cli(root, "validate")
        log = root / ".agent-kb" / ".log" / "events.jsonl"
        require(log.exists(), "CLI runs should append to the event log")
        result = run_cli(root, "stats", "--no-backfill")
        require(result.returncode == 0, "stats should succeed", result)
        require("CLI command usage" in result.stdout, "stats should report command usage", result)
        require("init" in result.stdout and "validate" in result.stdout, "stats should list run commands", result)
        require("KB file churn" in result.stdout, "stats should report the churn section", result)


# Checks that stats backfills KB read events from local transcripts and dedupes repeated scans.
def test_stats_backfills_kb_reads() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp)
        root = base / "repo.dot"
        root.mkdir()
        subdir = root / "src"
        subdir.mkdir()
        init_root(root)
        require(
            fixture_claude_project_name(root) != str(root.resolve()).replace("/", "-"),
            "Claude fixture should encode dots differently from the old slash-only helper",
        )
        claude_dir = base / "claude" / "projects"
        codex_dir = base / "codex" / "sessions"
        write_jsonl(
            claude_dir / fixture_claude_project_name(root) / "session-read.jsonl",
            [
                {
                    "timestamp": "2026-07-05T00:00:00Z",
                    "cwd": str(root),
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Read",
                                "input": {"file_path": str(root / ".agent-kb" / "start.md")},
                            }
                        ]
                    },
                }
            ],
        )
        write_jsonl(
            codex_dir / "2026" / "07" / "05" / "rollout-read.jsonl",
            [
                {"timestamp": "2026-07-05T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(root)}},
                {
                    "timestamp": "2026-07-05T00:00:01Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps({"cmd": "sed -n '1,40p' .agent-kb/routes.yaml", "workdir": str(root)}),
                    },
                },
            ],
        )
        write_jsonl(
            codex_dir / "2026" / "07" / "05" / "rollout-no-read.jsonl",
            [{"timestamp": "2026-07-05T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(root)}}],
        )
        write_jsonl(
            codex_dir / "2026" / "07" / "05" / "rollout-python-no-read.jsonl",
            [
                {"timestamp": "2026-07-05T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(root)}},
                {
                    "timestamp": "2026-07-05T00:00:01Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps(
                            {"cmd": "python3 skills/agent-context-kb/scripts/agent_kb.py validate --root .", "workdir": str(root)}
                        ),
                    },
                },
            ],
        )
        write_jsonl(
            codex_dir / "2026" / "07" / "05" / "rollout-git-kb-no-read.jsonl",
            [
                {"timestamp": "2026-07-05T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(root)}},
                {
                    "timestamp": "2026-07-05T00:00:01Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps({"cmd": "git -C .agent-kb commit -m noop", "workdir": str(root)}),
                    },
                },
            ],
        )
        write_jsonl(
            codex_dir / "2026" / "07" / "05" / "rollout-subdir-no-read.jsonl",
            [{"timestamp": "2026-07-05T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(subdir)}}],
        )
        write_jsonl(
            codex_dir / "2026" / "07" / "05" / "rollout-response-item-read.jsonl",
            [
                {"timestamp": "2026-07-05T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(root)}},
                {
                    "timestamp": "2026-07-05T00:00:01Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "shell",
                        "arguments": json.dumps(
                            {"command": ["bash", "-lc", "sed -n '1,40p' .agent-kb/start.md"], "workdir": str(root)}
                        ),
                    },
                },
            ],
        )

        result = run_cli(
            root,
            "stats",
            "--claude-dir",
            str(claude_dir),
            "--codex-dir",
            str(codex_dir),
            "--dead-sessions",
            "1",
            "--top",
            "10",
        )
        require(result.returncode == 0, "stats with transcript backfill should succeed", result)
        require("Backfilled KB reads: 3 new event(s)." in result.stdout, "stats should backfill three KB reads", result)
        require("KB hit rate: 3/7 (42.9%)" in result.stdout, "stats should report hit rate with non-read denominator", result)
        require("start.md" in result.stdout and "routes.yaml" in result.stdout, "stats should show read KB files", result)
        require("Dead knowledge candidates" in result.stdout, "stats should report dead knowledge candidates", result)

        result = run_cli(
            root,
            "stats",
            "--claude-dir",
            str(claude_dir),
            "--codex-dir",
            str(codex_dir),
        )
        require(result.returncode == 0, "second stats backfill should succeed", result)
        require("Backfilled KB reads: 0 new event(s)." in result.stdout, "stats backfill should be idempotent", result)
        log = (root / ".agent-kb" / ".log" / "events.jsonl").read_text(encoding="utf-8")
        read_events = [json.loads(line) for line in log.splitlines() if '"event": "kb_read"' in line]
        require(len(read_events) == 3, "event log should contain exactly three deduped kb_read events")


# Checks that the private compliance analyzer parses synthetic Claude and Codex transcripts.
def test_compliance_analyzer_synthetic_transcripts() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp)
        root = base / "repo.dot"
        root.mkdir()
        init_root(root)
        (root / "src.py").write_text("print('hi')\n", encoding="utf-8")

        claude_dir = base / "claude" / "projects"
        codex_dir = base / "codex" / "sessions"
        write_jsonl(
            claude_dir / fixture_claude_project_name(root) / "session-good.jsonl",
            [
                {
                    "timestamp": "2026-07-05T00:00:00Z",
                    "cwd": str(root),
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Read",
                                "input": {"file_path": str(root / ".agent-kb" / "start.md")},
                            }
                        ]
                    },
                },
                {
                    "timestamp": "2026-07-05T00:00:01Z",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Grep",
                                "input": {"path": str(root), "pattern": "print"},
                            }
                        ]
                    },
                },
            ],
        )
        write_jsonl(
            claude_dir / fixture_claude_project_name(root) / "session-agents-good.jsonl",
            [
                {
                    "timestamp": "2026-07-05T00:00:00Z",
                    "cwd": str(root),
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Read",
                                "input": {"file_path": str(root / "AGENTS.md")},
                            }
                        ]
                    },
                },
                {
                    "timestamp": "2026-07-05T00:00:01Z",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Read",
                                "input": {"file_path": str(root / ".agent-kb" / "start.md")},
                            }
                        ]
                    },
                },
                {
                    "timestamp": "2026-07-05T00:00:02Z",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Grep",
                                "input": {"path": str(root), "pattern": "print"},
                            }
                        ]
                    },
                },
            ],
        )
        write_jsonl(
            claude_dir / fixture_claude_project_name(root) / "session-agents-miss.jsonl",
            [
                {
                    "timestamp": "2026-07-05T00:00:00Z",
                    "cwd": str(root),
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Read",
                                "input": {"file_path": str(root / "AGENTS.md")},
                            }
                        ]
                    },
                },
                {
                    "timestamp": "2026-07-05T00:00:01Z",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Grep",
                                "input": {"path": str(root), "pattern": "print"},
                            }
                        ]
                    },
                },
            ],
        )
        write_jsonl(
            codex_dir / "2026" / "07" / "05" / "rollout-bad.jsonl",
            [
                {"timestamp": "2026-07-05T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(root)}},
                {
                    "timestamp": "2026-07-05T00:00:01Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps({"cmd": "rg print src.py", "workdir": str(root)}),
                    },
                },
                {
                    "timestamp": "2026-07-05T00:00:02Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps({"cmd": "sed -n '1,40p' .agent-kb/start.md", "workdir": str(root)}),
                    },
                },
            ],
        )
        write_jsonl(
            codex_dir / "2026" / "07" / "05" / "rollout-kb-write.jsonl",
            [
                {"timestamp": "2026-07-05T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(root)}},
                {
                    "timestamp": "2026-07-05T00:00:01Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps(
                            {"cmd": "echo Later >> .agent-kb/start.md", "workdir": str(root)}
                        ),
                    },
                },
                {
                    "timestamp": "2026-07-05T00:00:02Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps({"cmd": "rg print src.py", "workdir": str(root)}),
                    },
                },
            ],
        )
        write_jsonl(
            codex_dir / "2026" / "07" / "05" / "rollout-python-then-read.jsonl",
            [
                {"timestamp": "2026-07-05T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(root)}},
                {
                    "timestamp": "2026-07-05T00:00:01Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps(
                            {"cmd": "python3 skills/agent-context-kb/scripts/agent_kb.py validate --root .", "workdir": str(root)}
                        ),
                    },
                },
                {
                    "timestamp": "2026-07-05T00:00:02Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps({"cmd": "sed -n '1,40p' .agent-kb/start.md", "workdir": str(root)}),
                    },
                },
            ],
        )
        write_jsonl(
            codex_dir / "2026" / "07" / "05" / "rollout-git-kb-then-source.jsonl",
            [
                {"timestamp": "2026-07-05T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(root)}},
                {
                    "timestamp": "2026-07-05T00:00:01Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps({"cmd": "git -C .agent-kb commit -m noop", "workdir": str(root)}),
                    },
                },
                {
                    "timestamp": "2026-07-05T00:00:02Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps({"cmd": "rg print src.py", "workdir": str(root)}),
                    },
                },
            ],
        )
        outside = base / "outside"
        outside.mkdir()
        write_jsonl(
            codex_dir / "2026" / "07" / "05" / "rollout-outside-then-kb.jsonl",
            [
                {"timestamp": "2026-07-05T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(outside)}},
                {
                    "timestamp": "2026-07-05T00:00:01Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps({"cmd": "rg print outside.py", "workdir": str(outside)}),
                    },
                },
                {
                    "timestamp": "2026-07-05T00:00:02Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps({"cmd": "sed -n '1,40p' .agent-kb/start.md", "workdir": str(root)}),
                    },
                },
            ],
        )
        write_jsonl(
            codex_dir / "2026" / "07" / "05" / "rollout-response-item-read.jsonl",
            [
                {"timestamp": "2026-07-05T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(root)}},
                {
                    "timestamp": "2026-07-05T00:00:01Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "shell",
                        "arguments": json.dumps(
                            {"command": ["bash", "-lc", "sed -n '1,40p' .agent-kb/routes.yaml"], "workdir": str(root)}
                        ),
                    },
                },
            ],
        )
        write_jsonl(
            codex_dir / "2026" / "07" / "05" / "rollout-non-entry-kb.jsonl",
            [
                {"timestamp": "2026-07-05T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(root)}},
                {
                    "timestamp": "2026-07-05T00:00:01Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps(
                            {"cmd": "sed -n '1,40p' .agent-kb/workflows/local-dev.md", "workdir": str(root)}
                        ),
                    },
                },
                {
                    "timestamp": "2026-07-05T00:00:02Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps({"cmd": "rg print src.py", "workdir": str(root)}),
                    },
                },
            ],
        )
        late_records = [
            {"timestamp": "2026-07-05T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(root)}},
            {
                "timestamp": "2026-07-05T00:00:01Z",
                "type": "function_call",
                "payload": {
                    "name": "functions.exec_command",
                    "arguments": json.dumps({"cmd": "rg print src.py", "workdir": str(root)}),
                },
            },
        ]
        late_records.extend(
            {
                "timestamp": f"2026-07-05T00:00:{second:02d}Z",
                "type": "function_call",
                "payload": {
                    "name": "functions.exec_command",
                    "arguments": json.dumps({"cmd": "date", "workdir": str(root)}),
                },
            }
            for second in range(2, 25)
        )
        late_records.append(
            {
                "timestamp": "2026-07-05T00:00:25Z",
                "type": "function_call",
                "payload": {
                    "name": "functions.exec_command",
                    "arguments": json.dumps({"cmd": "sed -n '1,40p' .agent-kb/start.md", "workdir": str(root)}),
                },
            }
        )
        write_jsonl(codex_dir / "2026" / "07" / "05" / "rollout-late-long.jsonl", late_records)

        result = run_compliance(root, claude_dir, codex_dir, "--details")
        require(result.returncode == 0, "compliance analyzer should succeed", result)
        require("Sessions analyzed: 11" in result.stdout, "analyzer should count all synthetic sessions", result)
        require("KB entry hit rate: 7/11 (63.6%)" in result.stdout, "analyzer should report entry reads", result)
        require("Any KB hit rate: 8/11 (72.7%)" in result.stdout, "analyzer should report all KB reads", result)
        require("Read compliance: 5/11 (45.5%)" in result.stdout, "analyzer should report raw compliant sessions", result)
        require(
            "Applicable read compliance (auto): 2/8 (25.0%)" in result.stdout,
            "analyzer should report auto-applicable compliance",
            result,
        )
        require("First source action before KB: 6" in result.stdout, "analyzer should report pre-KB source actions", result)
        require("late KB read: 2" in result.stdout, "analyzer should classify late reads", result)
        require("1-3 actions late: 1" in result.stdout, "analyzer should bucket short late reads", result)
        require("20+ actions late: 1" in result.stdout, "analyzer should bucket long late reads", result)
        require("no KB read: 3" in result.stdout, "analyzer should classify missing KB reads", result)
        require("non-entry KB read: 1" in result.stdout, "analyzer should classify non-entry KB reads", result)
        require(
            "KB-first not applicable: 3" in result.stdout,
            "analyzer should count sessions without source actions separately",
            result,
        )
        require("Breakdown by harness:" in result.stdout, "analyzer should print harness breakdown", result)
        require("  claude\n  Sessions analyzed: 3" in result.stdout, "analyzer should report Claude sessions separately", result)
        require("Claude AGENTS.md delivery:" in result.stdout, "analyzer should print AGENTS.md delivery split", result)
        require("  read AGENTS.md\n  Sessions analyzed: 2" in result.stdout, "delivery split should count AGENTS.md readers", result)
        require("  did not read AGENTS.md\n  Sessions analyzed: 1" in result.stdout, "delivery split should count Claude sessions without AGENTS.md", result)
        require("category=late_kb_read late_bucket=20+ actions late" in result.stdout, "details should include late bucket")
        require("read_agents_md=True" in result.stdout, "details should expose AGENTS.md read status")
        require(
            "Write-back compliance: deferred" in result.stdout,
            "analyzer should document that write-back compliance is deferred",
            result,
        )

        write_jsonl(
            codex_dir / "2026" / "07" / "05" / "rollout-post-cutoff.jsonl",
            [
                {"timestamp": "2026-07-05T00:01:00Z", "type": "session_meta", "payload": {"cwd": str(root)}},
                {
                    "timestamp": "2026-07-05T00:01:01Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps({"cmd": "sed -n '1,40p' .agent-kb/start.md", "workdir": str(root)}),
                    },
                },
            ],
        )
        write_jsonl(
            codex_dir / "2026" / "07" / "05" / "rollout-missing-timestamp.jsonl",
            [
                {"type": "session_meta", "payload": {"cwd": str(root)}},
                {
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps({"cmd": "rg print src.py", "workdir": str(root)}),
                    },
                },
            ],
        )
        result = run_compliance(root, claude_dir, codex_dir, "--since", "2026-07-05T00:00:30Z")
        require(result.returncode == 0, "--since ISO filter should succeed", result)
        require(
            "Since filter: 2026-07-05T00:00:30Z -> 2026-07-05T00:00:30+00:00" in result.stdout,
            "since filter should report the resolved cutoff",
            result,
        )
        require(
            "Sessions excluded by missing/invalid timestamp: 1" in result.stdout,
            "since filter should count sessions without real timestamps",
            result,
        )
        require("Sessions analyzed: 1" in result.stdout, "since filter should keep only post-cutoff sessions", result)


# Checks that the Release 2 eval runner can parse a bundle and write summary JSON without invoking an agent.
def test_eval_runner_dry_run() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-eval-") as tmp:
        base = Path(tmp)
        repo = base / "repo"
        kb_repo = base / "kb"
        repo_commit = create_fixture_commit(repo, {"README.md": "# Fixture\n"})
        kb_commit = create_fixture_commit(kb_repo, {"start.md": "# Agent KB Start\n"})
        bundle = base / "bundles" / "demo"
        results = base / "results"
        write_json_yaml(
            bundle / "bundle.yaml",
            {
                "name": "demo",
                "repo_path": str(repo),
                "repo_commit": repo_commit,
                "kb_commit": kb_commit,
                "task_file": "tasks.yaml",
                "runner": {
                    "repetitions": 1,
                    "agent_model": "sonnet",
                    "agent_effort": "medium",
                    "judge_models": {"claude": "sonnet", "codex": "gpt-5"},
                    "judge_efforts": {"codex": "medium"},
                },
            },
        )
        write_json_yaml(
            bundle / "tasks.yaml",
            {
                "tasks": [
                    {
                        "id": "dry-run-task",
                        "prompt": "Say this is a dry run.",
                        "assertions": [
                            {
                                "id": "read-start",
                                "check": "tool_read",
                                "path": ".agent-kb/start.md",
                                "description": "The agent reads the KB start file.",
                            },
                            {"id": "placeholder", "check": "judge", "description": "A judge can score this later."},
                        ],
                    }
                ]
            },
        )
        result = subprocess.run(
            [
                sys.executable,
                str(EVAL_RUNNER),
                "--bundle",
                str(bundle),
                "--kb-repo",
                str(kb_repo),
                "--results-dir",
                str(results),
                "--dry-run",
            ],
            check=False,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        require(result.returncode == 0, "eval runner dry-run should succeed", result)
        output_files = list((results / "demo").glob("*.json"))
        require(len(output_files) == 1, "eval runner should write one summary JSON", result)
        summary = json.loads(output_files[0].read_text(encoding="utf-8"))
        require(summary["dry_run"] is True, "eval summary should record dry-run mode")
        require(summary["judge"] is None, "eval summary should record absent judge harness")
        require(summary["repo_path"] == str(repo.resolve()), "eval summary should record bundle repo_path")
        require(summary["runs"][0]["agent"]["status"] == "dry_run", "eval summary should avoid agent calls")
        require(
            summary["runs"][0]["agent"]["provenance"]["requested_model"] == "sonnet",
            "eval summary should record requested agent model",
        )
        require(
            summary["runs"][0]["assertions"][0]["status"] == "dry_run",
            "eval assertions should be marked dry_run without agent calls",
        )
        require(not (results / ".raw").exists(), "dry-run should not write raw artifacts")


# Checks that the eval runner reports pinned workspace restoration failures clearly.
def test_eval_runner_rejects_bad_pin() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-eval-") as tmp:
        base = Path(tmp)
        repo = base / "repo"
        kb_repo = base / "kb"
        repo_commit = create_fixture_commit(repo, {"README.md": "# Fixture\n"})
        create_fixture_commit(kb_repo, {"start.md": "# Agent KB Start\n"})
        bundle = base / "bundles" / "demo"
        results = base / "results"
        write_json_yaml(
            bundle / "bundle.yaml",
            {
                "name": "demo",
                "repo_path": str(repo),
                "repo_commit": repo_commit,
                "kb_commit": "not-a-real-commit",
                "task_file": "tasks.yaml",
                "runner": {"repetitions": 1},
            },
        )
        write_json_yaml(bundle / "tasks.yaml", {"tasks": [{"id": "t", "prompt": "Dry run.", "assertions": []}]})
        result = subprocess.run(
            [
                sys.executable,
                str(EVAL_RUNNER),
                "--bundle",
                str(bundle),
                "--kb-repo",
                str(kb_repo),
                "--results-dir",
                str(results),
                "--dry-run",
            ],
            check=False,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        require(result.returncode == 1, "eval runner should reject a bad kb_commit", result)
        require("git archive --format=tar not-a-real-commit failed" in result.stderr, "bad pin failure should name git archive", result)


# Checks deterministic eval assertion helpers without invoking Claude.
def test_eval_runner_behavior_and_judge_parsers() -> None:
    runner = load_eval_runner_module()
    stream = "\n".join(
        [
            json.dumps({"type": "system", "model": "claude-sonnet-test"}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "name": "Read", "input": {"file_path": ".agent-kb/start.md"}},
                            {"type": "text", "text": "I checked the KB."},
                        ]
                    },
                }
            ),
            json.dumps({"type": "result", "result": "Final answer.", "total_cost_usd": 0.01}),
        ]
    )
    parsed = runner.parse_claude_stream(stream)
    require(parsed["final_answer"] == "Final answer.", "stream parser should prefer the final result text")
    require(parsed["cost_usd"] == 0.01, "stream parser should capture cost")
    require(parsed["actual_model"] == "claude-sonnet-test", "stream parser should capture actual model")
    scored = runner.score_behavior_assertion(
        {"id": "read-start", "check": "tool_read", "path": ".agent-kb/start.md"},
        parsed["tool_calls"],
    )
    require(scored["passed"] is True, "tool_read assertion should pass when the path was read")
    scored = runner.score_behavior_assertion(
        {"id": "read-start", "check": "tool_read", "path": ".agent-kb/start.md"},
        [{"name": "Bash", "input": {"command": "cd .agent-kb && cat start.md"}}],
    )
    require(scored["passed"] is True, "tool_read assertion should pass for shell reads of the same path")
    scored = runner.score_behavior_assertion(
        {"id": "access-kb", "check": "kb_access", "path": ".agent-kb"},
        [{"name": "Bash", "input": {"command": "grep -r partition .agent-kb"}}],
    )
    require(scored["passed"] is True, "kb_access assertion should pass for shell searches under the KB")
    scored = runner.score_behavior_assertion({"id": "no-edit", "check": "no_edit"}, parsed["tool_calls"])
    require(scored["passed"] is True, "no_edit assertion should pass without edit tools")
    judge_stdout = json.dumps(
        {
            "result": json.dumps(
                {
                    "assertions": [
                        {"id": "semantic", "passed": True, "reason": "Matches reference.", "confidence": 0.9}
                    ]
                }
            ),
            "usage": {"input_tokens": 1},
            "cost_usd": 0.02,
            "model": "claude-sonnet-test",
        }
    )
    judged = runner.parse_judge_output(judge_stdout)
    require(judged["assertions"][0]["id"] == "semantic", "judge parser should parse assertion rows")
    require(judged["cost_usd"] == 0.02, "judge parser should capture cost")
    require(judged["actual_model"] == "claude-sonnet-test", "judge parser should capture actual model")
    codex_judge_stdout = "\n".join(
        [
            json.dumps({"type": "turn.started", "model": "gpt-5-test"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "assertions": [
                                            {
                                                "id": "semantic",
                                                "passed": False,
                                                "reason": "Does not match.",
                                                "confidence": 0.8,
                                            }
                                        ]
                                    }
                                ),
                            }
                        ],
                    },
                }
            ),
        ]
    )
    codex_judged = runner.parse_judge_output(codex_judge_stdout)
    require(codex_judged["assertions"][0]["passed"] is False, "judge parser should parse Codex JSONL output")
    require(codex_judged["actual_model"] == "gpt-5-test", "judge parser should capture Codex model")
    with tempfile.TemporaryDirectory(prefix="agent-kb-eval-") as tmp:
        base = Path(tmp)
        captured = {}
        original_run = runner.subprocess.run

        # Captures the subprocess command while returning a synthetic Claude stream.
        def fake_run(args, **kwargs):
            captured["args"] = args
            return subprocess.CompletedProcess(args, 0, stdout=stream, stderr="")

        try:
            runner.subprocess.run = fake_run
            config = {
                "agent_model": "sonnet",
                "agent_effort": "medium",
                "judge_model": None,
                "judge_models": {"codex": "gpt-5"},
                "judge_effort": None,
                "judge_efforts": {"codex": "medium"},
            }
            provenance = {
                "harness": "claude",
                "cli_version": "claude-test",
                "requested_model": "sonnet",
                "actual_model": None,
                "effort": "medium",
                "max_turns": None,
            }
            agent_result = runner.run_claude("Prompt.", base, False, base / "results", base / "results" / ".raw", "task", 1, config, provenance)
        finally:
            runner.subprocess.run = original_run
        require("--output-format" in captured["args"], "Claude agent command should set output format")
        require("stream-json" in captured["args"], "Claude agent command should request stream-json output")
        require("--verbose" in captured["args"], "Claude stream-json command should include --verbose")
        require("--model" in captured["args"] and "sonnet" in captured["args"], "Claude agent command should pass configured model")
        require("--effort" in captured["args"] and "medium" in captured["args"], "Claude agent command should pass configured effort")
        require(agent_result["status"] == "ok", "fake Claude run should parse as ok")
        require(agent_result["provenance"]["actual_model"] == "claude-sonnet-test", "agent provenance should record actual model")
        codex_command = runner.codex_command("Judge.", config, "judge")
        require("--model" in codex_command and "gpt-5" in codex_command, "Codex judge command should pass configured model")
        require(
            any(item.startswith("model_reasoning_effort=") for item in codex_command),
            "Codex judge command should pass configured effort",
        )
        rows, judge_result = runner.assertion_rows(
            {"assertions": [{"id": "semantic", "check": "judge", "description": "Semantic assertion."}]},
            {"status": "error", "tool_calls": [], "final_answer": ""},
            base,
            "claude",
            False,
            base / "results",
            base / "results" / ".raw",
            "task",
            1,
            config,
            {
                "harness": "claude",
                "cli_version": "claude-test",
                "requested_model": "sonnet",
                "actual_model": None,
                "effort": None,
                "max_turns": None,
            },
        )
        require(judge_result is None, "failed agent run should not invoke judge")
        require(rows[0]["status"] == "agent_error", "semantic assertion should record agent_error")


# Checks that raw-answer calibration rejudges existing summaries and computes agreement.
def test_eval_runner_calibration_from_raw() -> None:
    runner = load_eval_runner_module()
    with tempfile.TemporaryDirectory(prefix="agent-kb-eval-") as tmp:
        base = Path(tmp)
        repo = base / "repo"
        kb_repo = base / "kb"
        repo_commit = create_fixture_commit(repo, {"README.md": "# Fixture\n"})
        kb_commit = create_fixture_commit(kb_repo, {"start.md": "# Agent KB Start\n"})
        bundle = base / "bundles" / "demo"
        results = base / "results"
        run_id = "20260705T000000Z"
        raw_dir = results / ".raw" / "demo" / run_id
        raw_stdout = raw_dir / "task-r1-agent.stdout.jsonl"
        raw_stdout.parent.mkdir(parents=True, exist_ok=True)
        raw_stdout.write_text(json.dumps({"type": "result", "result": "Final answer."}) + "\n", encoding="utf-8")
        write_json_yaml(
            bundle / "bundle.yaml",
            {
                "name": "demo",
                "repo_path": str(repo),
                "repo_commit": repo_commit,
                "kb_commit": kb_commit,
                "task_file": "tasks.yaml",
                "runner": {"repetitions": 1, "judge_models": {"codex": "gpt-5"}},
            },
        )
        write_json_yaml(
            bundle / "tasks.yaml",
            {
                "tasks": [
                    {
                        "id": "task",
                        "prompt": "Answer.",
                        "assertions": [{"id": "semantic", "check": "judge", "description": "Matches."}],
                    }
                ]
            },
        )
        summary_path = results / "demo" / "source.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(
                {
                    "bundle": "demo",
                    "repo_commit": repo_commit,
                    "kb_commit": kb_commit,
                    "harness": "claude",
                    "runs": [
                        {
                            "task_id": "task",
                            "repetition": 1,
                            "harness": "claude",
                            "agent": {"raw_artifacts": {"stdout": str(raw_stdout.relative_to(results))}},
                            "assertions": [
                                {"id": "semantic", "check": "judge", "status": "scored", "passed": True, "reason": "Old."}
                            ],
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        original_run_judge = runner.run_judge

        # Returns a deterministic rejudge result without invoking an external CLI.
        def fake_run_judge(*args, **kwargs):
            return {
                "status": "ok",
                "raw_artifacts": {"stdout": ".raw/demo/calibration/task-r1-judge.stdout.jsonl"},
                "assertions": [{"id": "semantic", "passed": True, "reason": "New.", "confidence": 0.9}],
                "provenance": {"harness": "codex", "requested_model": "gpt-5"},
            }

        try:
            runner.run_judge = fake_run_judge
            calibration = runner.calibrate_summary(bundle, repo, kb_repo, summary_path, "codex", results, "calibration")
        finally:
            runner.run_judge = original_run_judge
        require(calibration["agreement"]["compared"] == 1, "calibration should compare one semantic assertion")
        require(calibration["agreement"]["agreed"] == 1, "calibration should count matching judge decisions")
        require(calibration["rows"][0]["agreement"] is True, "calibration row should record agreement")


# Checks that init sets up the chosen versioning mode (nested default, shared, local).
def test_init_versioning_modes() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        result = run_cli(root, "init")
        require(result.returncode == 0, "default init should succeed", result)
        require("Versioning mode: nested." in result.stdout, "default mode should be nested", result)
        ignore = (root / ".gitignore").read_text(encoding="utf-8")
        require(".agent-kb/" in ignore, "nested init should ignore .agent-kb/ in the parent repo", result)
        require((root / ".agent-kb" / ".git").exists(), "nested init should create the nested repo")
        meta = (root / ".agent-kb" / ".kb-meta.yaml").read_text(encoding="utf-8")
        require("mode: nested" in meta, "meta should record nested mode")

    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        result = run_cli(root, "init", "--shared")
        require(result.returncode == 0, "shared init should succeed", result)
        require(not (root / ".gitignore").exists(), "shared init should not gitignore the KB")
        require(not (root / ".agent-kb" / ".git").exists(), "shared init should not create a nested repo")
        meta = (root / ".agent-kb" / ".kb-meta.yaml").read_text(encoding="utf-8")
        require("mode: shared" in meta, "meta should record shared mode")

    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        result = run_cli(root, "init", "--local")
        require(result.returncode == 0, "local init should succeed", result)
        require(".agent-kb/" in (root / ".gitignore").read_text(encoding="utf-8"), "local init should gitignore the KB", result)
        require(not (root / ".agent-kb" / ".git").exists(), "local init should not create a nested repo")
        meta = (root / ".agent-kb" / ".kb-meta.yaml").read_text(encoding="utf-8")
        require("mode: local" in meta, "meta should record local mode")


# Runs all smoke tests and prints a compact success line.
def main() -> int:
    tests = [
        test_claude_project_name_observed_encoding,
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
        test_init_uses_claude_when_agents_points_to_it,
        test_upgrade_updates_claude_protocol_owner,
        test_claude_pointer_takes_protocol_ownership,
        test_upgrade_replaces_long_runtime_protocol,
        test_trim_diagnoses_empty_scaffold,
        test_trim_threshold_flag_reports_oversize_topic,
        test_trim_flags_char_oversize_with_few_lines,
        test_trim_minor_overage_is_optional,
        test_trim_recheck_runs_validate,
        test_trim_flags_husk_after_merge_without_deleting,
        test_trim_write_deletes_empty_scaffold_topics,
        test_trim_write_promotes_remaining_route_entry,
        test_trim_write_keeps_non_empty_topic,
        test_trim_write_keeps_malformed_topic,
        test_trim_write_keeps_linked_empty_topic,
        test_kb_gitignore_init_and_upgrade,
        test_validate_metrics_and_health,
        test_note_body_redacted_in_log,
        test_stats_reports_cli_usage,
        test_stats_backfills_kb_reads,
        test_compliance_analyzer_synthetic_transcripts,
        test_eval_runner_dry_run,
        test_eval_runner_rejects_bad_pin,
        test_eval_runner_behavior_and_judge_parsers,
        test_eval_runner_calibration_from_raw,
        test_init_versioning_modes,
    ]
    for test in tests:
        test()
    print(f"OK: {len(tests)} smoke checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
