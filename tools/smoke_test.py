#!/usr/bin/env python3
"""Run lightweight edge checks for the agent KB CLI."""

from __future__ import annotations

import json
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parents[1]
SKILL_SCRIPTS = REPO_DIR / "skills" / "agent-context-kb" / "scripts"
SCRIPT = SKILL_SCRIPTS / "agent_kb.py"
DEV_COMPLIANCE_SCRIPT = Path(__file__).with_name("compliance_analyzer.py")
EVAL_RUNNER = REPO_DIR / "evals" / "run_bundle.py"
sys.path.insert(0, str(SKILL_SCRIPTS))
from transcript_reads import claude_project_name  # noqa: E402


# Runs the KB CLI against a temporary repository and captures output for assertions.
# `cwd` matters for transcript attribution tests, where a bare path must not resolve against it.
def run_cli(root: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--root", str(root)],
        check=False,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=None if cwd is None else str(cwd),
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
    root = Path("/Users/dev/Desktop/glucose/.claude/worktrees/rustling_jumping_volcano")
    expected = "-Users-dev-Desktop-glucose--claude-worktrees-rustling-jumping-volcano"
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


# Checks that compile keeps notes whose target is a directory instead of crashing.
def test_directory_target() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        result = run_cli(root, "note", "--title", "Dir target", "--target", "inbox", "--body", "Nope.")
        require(result.returncode == 0, "note with directory target should still be recorded", result)
        result = run_cli(root, "compile")
        require(result.returncode == 0, "compile should keep directory target notes unresolved", result)
        require("Unresolved: 1" in result.stdout, "directory target should remain unresolved", result)
        inbox_files = list((root / ".agent-kb" / "inbox").glob("*.md"))
        require(len(inbox_files) == 1, "directory target note should stay in inbox")


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


# Checks that init directs the agent to offer distillation and records it as the plan's next step.
def test_init_empty_scaffold_warm_start_prompt() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        result = run_cli(root, "init")
        require(result.returncode == 0, "init should succeed", result)
        require("NEXT STEP - REQUIRED BEFORE YOU CLOSE OUT" in result.stdout, "init should make the distillation offer mandatory", result)
        require("offer to run it now" in result.stdout, "init should tell the agent to offer distillation", result)
        require("Only run it if the user confirms" in result.stdout, "init should keep distillation prompt-only", result)
        require("Not code summaries or obvious code facts" in result.stdout, "init should guard against bad distillation", result)
        require("git log -p" in result.stdout, "distillation prompt should demand patch-level history mining", result)
        require("anomal" in result.stdout, "distillation prompt should name anomalies as the mining target", result)
        plan = (root / ".agent-kb" / "plans" / "current.md").read_text(encoding="utf-8")
        require("one-time distillation pass" in plan, "init should record the pending distillation in the current plan")


# Checks that validate flags a still-empty scaffold with an advisory and stays silent once topics have content.
def test_validate_empty_scaffold_advisory() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        result = run_cli(root, "validate")
        require(result.returncode == 0, "validate should pass on a fresh scaffold", result)
        require("ADVISORY: this KB is an empty scaffold" in result.stdout, "validate should flag the empty scaffold", result)
        overview = root / ".agent-kb" / "architecture" / "overview.md"
        overview.write_text(
            overview.read_text(encoding="utf-8") + "\nDurable fact: module boundaries follow the plugin split.\n",
            encoding="utf-8",
        )
        result = run_cli(root, "validate")
        require(result.returncode == 0, "validate should pass once a topic has content", result)
        require("ADVISORY: this KB is an empty scaffold" not in result.stdout, "advisory should stop once the KB has content", result)


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


# Checks that upgrade reports normal project-owned plan content as preserved, not as a review item.
def test_upgrade_preserves_custom_plan_without_review() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        plan = root / ".agent-kb" / "plans" / "current.md"
        before = plan.read_text(encoding="utf-8")
        result = run_cli(root, "upgrade")
        require(result.returncode == 0, "upgrade should succeed with custom current plan content", result)
        require(".agent-kb/plans/current.md custom preserved." in result.stdout, "custom plan content should be reported as preserved", result)
        require("needs review: plans/current.md" not in result.stdout, "custom plan content should not enter review files", result)
        require(plan.read_text(encoding="utf-8") == before, "upgrade should not rewrite custom plan content")


# Checks that parseable custom routes are preserved without being counted as review work.
def test_upgrade_preserves_custom_routes_without_review() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        routes = root / ".agent-kb" / "routes.yaml"
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
        before = routes.read_text(encoding="utf-8")
        result = run_cli(root, "upgrade")
        require(result.returncode == 0, "upgrade should succeed with custom routes", result)
        require(".agent-kb/routes.yaml custom routes preserved." in result.stdout, "custom routes should be reported as preserved", result)
        require("needs review: routes.yaml" not in result.stdout, "custom routes should not enter review files", result)
        require(routes.read_text(encoding="utf-8") == before, "upgrade should not rewrite custom routes")


# Checks that upgrade backfills metadata for older KBs and nudges nested-mode verification.
def test_upgrade_backfills_missing_meta() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        meta = root / ".agent-kb" / ".kb-meta.yaml"
        meta.unlink()
        result = run_cli(root, "upgrade")
        require(result.returncode == 0, "upgrade should succeed when metadata is missing", result)
        require(".agent-kb/.kb-meta.yaml created." in result.stdout, "upgrade should report metadata creation", result)
        require("Metadata mode inferred as nested." in result.stdout, "upgrade should report the inferred mode", result)
        require("git -C .agent-kb status" in result.stdout, "nested upgrade should show the nested verification hint", result)
        text = meta.read_text(encoding="utf-8")
        require("schema_version: 1" in text, "backfilled metadata should stamp the current schema")
        require("mode: nested" in text, "backfilled metadata should preserve the inferred nested mode")


# Checks that upgrade refreshes stale metadata while preserving mode and created fields.
def test_upgrade_refreshes_meta_schema() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        meta = root / ".agent-kb" / ".kb-meta.yaml"
        meta.write_text("schema_version: 0\nmode: shared\ncreated: 2026-01-02\n", encoding="utf-8")
        result = run_cli(root, "upgrade")
        require(result.returncode == 0, "upgrade should succeed with stale metadata", result)
        require(".agent-kb/.kb-meta.yaml updated." in result.stdout, "upgrade should report metadata update", result)
        text = meta.read_text(encoding="utf-8")
        require("schema_version: 1" in text, "upgrade should refresh the schema stamp")
        require("mode: shared" in text, "upgrade should preserve the recorded mode")
        require("created: 2026-01-02" in text, "upgrade should preserve the created date")


# Checks that validate warns about missing or stale metadata without failing.
def test_validate_warns_schema_drift() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        meta = root / ".agent-kb" / ".kb-meta.yaml"
        meta.unlink()
        result = run_cli(root, "validate")
        require(result.returncode == 0, "missing metadata should warn without failing", result)
        require("WARN: KB scaffold has no .agent-kb/.kb-meta.yaml" in result.stdout, "validate should warn about missing metadata", result)
        meta.write_text("schema_version: 0\nmode: nested\ncreated: 2026-01-02\n", encoding="utf-8")
        result = run_cli(root, "validate")
        require(result.returncode == 0, "stale metadata should warn without failing", result)
        require("WARN: KB scaffold is schema 0; this skill writes schema 1 - run upgrade" in result.stdout, "validate should warn about schema drift", result)
        meta.write_text("schema_version: 2\nmode: nested\ncreated: 2026-01-02\n", encoding="utf-8")
        result = run_cli(root, "validate")
        require(result.returncode == 0, "newer metadata should warn without failing", result)
        require(
            "WARN: KB scaffold is schema 2; this skill writes schema 1 - update this skill before modifying the KB" in result.stdout,
            "validate should tell users to update the skill for newer KB schemas",
            result,
        )


# Checks that upgrade does not downgrade metadata written by a newer skill.
def test_upgrade_preserves_newer_meta_schema() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        meta = root / ".agent-kb" / ".kb-meta.yaml"
        meta.write_text("schema_version: 2\nmode: nested\ncreated: 2026-01-02\n", encoding="utf-8")
        result = run_cli(root, "upgrade")
        require(result.returncode == 0, "upgrade should succeed when metadata is newer", result)
        require("schema 2 is newer than this skill; update the skill before upgrading" in result.stdout, "upgrade should explain the newer schema", result)
        require("schema_version: 2" in meta.read_text(encoding="utf-8"), "upgrade should not downgrade a newer schema stamp")


# Checks that a no-op protocol upgrade reports current instead of updated.
def test_upgrade_reports_protocol_current_on_noop() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        result = run_cli(root, "upgrade")
        require(result.returncode == 0, "upgrade should succeed on a current protocol", result)
        require("AGENTS.md protocol current." in result.stdout, "no-op protocol rewrite should be reported as current", result)
        require("CLAUDE.md protocol current." in result.stdout, "no-op CLAUDE.md rewrite should be reported as current", result)


# Checks that init injects the full protocol into both entry files while preserving their content.
def test_init_injects_protocol_into_both_files() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        agents = root / "AGENTS.md"
        claude = root / "CLAUDE.md"
        agents.write_text("# Repo Instructions\n\nGeneral conventions here.\n", encoding="utf-8")
        claude.write_text("# Claude Instructions\n\nKeep the main instructions here.\n", encoding="utf-8")
        result = run_cli(root, "init")
        require(result.returncode == 0, "init should succeed with both entry files present", result)
        require("AGENTS.md protocol appended." in result.stdout, "init should report the AGENTS.md injection", result)
        require("CLAUDE.md protocol appended." in result.stdout, "init should report the CLAUDE.md injection", result)
        agents_text = agents.read_text(encoding="utf-8")
        claude_text = claude.read_text(encoding="utf-8")
        require("General conventions here." in agents_text, "AGENTS.md content should be preserved")
        require("Keep the main instructions here." in claude_text, "CLAUDE.md content should be preserved")
        require("`.agent-kb/` is this project's memory for coding agents" in agents_text, "AGENTS.md should carry the full protocol")
        require("`.agent-kb/` is this project's memory for coding agents" in claude_text, "CLAUDE.md should carry the full protocol")


# Checks that upgrade refreshes a stale protocol section in both entry files.
def test_upgrade_refreshes_stale_protocol_in_both_files() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        agents = root / "AGENTS.md"
        claude = root / "CLAUDE.md"
        agents.write_text("# Repo Instructions\n\n## Project Knowledge Base\n\nOld protocol.\n", encoding="utf-8")
        claude.write_text("# Claude Instructions\n\n## Project Knowledge Base\n\nOld protocol.\n", encoding="utf-8")
        result = run_cli(root, "upgrade")
        require(result.returncode == 0, "upgrade should succeed with stale protocols", result)
        require("AGENTS.md protocol updated." in result.stdout, "upgrade should report the AGENTS.md refresh", result)
        require("CLAUDE.md protocol updated." in result.stdout, "upgrade should report the CLAUDE.md refresh", result)
        for path in (agents, claude):
            text = path.read_text(encoding="utf-8")
            require("Old protocol." not in text, f"upgrade should replace the old {path.name} protocol section")
            require("`.agent-kb/` is this project's memory for coding agents" in text, f"upgrade should write the current protocol into {path.name}")


# Checks that upgrade replaces an old-style pointer section with the full protocol.
def test_upgrade_replaces_pointer_section_with_protocol() -> None:
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
        claude.write_text(
            "# Claude Instructions\n\n"
            "## Project Knowledge Base\n\n"
            "See [AGENTS.md](AGENTS.md) for the `.agent-kb/` protocol.\n",
            encoding="utf-8",
        )
        result = run_cli(root, "init")
        require(result.returncode == 0, "init should succeed on a pointer-section repo", result)
        agents_text = agents.read_text(encoding="utf-8")
        require("Old protocol." not in agents_text, "AGENTS.md should replace the old protocol section")
        require("`.agent-kb/` is this project's memory for coding agents" in agents_text, "AGENTS.md should carry the full protocol")
        require("## Commands" in agents_text, "AGENTS.md should preserve unrelated instructions")
        claude_text = claude.read_text(encoding="utf-8")
        require("for the `.agent-kb/` protocol" not in claude_text, "CLAUDE.md pointer section should be migrated away")
        require("`.agent-kb/` is this project's memory for coding agents" in claude_text, "CLAUDE.md should carry the full protocol")


# Checks that init on a bare repo creates both entry files with the full protocol.
def test_init_creates_both_files_with_protocol() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        result = run_cli(root, "init")
        require(result.returncode == 0, "init should succeed on a bare repo", result)
        require("AGENTS.md protocol created." in result.stdout, "init should report the AGENTS.md creation", result)
        require("CLAUDE.md protocol created." in result.stdout, "init should report the CLAUDE.md creation", result)
        for name in ("AGENTS.md", "CLAUDE.md"):
            text = (root / name).read_text(encoding="utf-8")
            require("`.agent-kb/` is this project's memory for coding agents" in text, f"{name} should carry the full protocol")


# Checks that upgrade migrates a legacy generated pointer file into a full-protocol file.
def test_upgrade_migrates_legacy_pointer_file() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        agents = root / "AGENTS.md"
        claude = root / "CLAUDE.md"
        agents.write_text("# Repo Instructions\n\n## Commands\n\nRun tests.\n", encoding="utf-8")
        init_root(root)
        # Recreate the legacy generated state: CLAUDE.md is only a pointer to the AGENTS.md owner.
        claude.write_text(
            "See [AGENTS.md](AGENTS.md) for repository instructions and the `.agent-kb/` knowledge base protocol.\n",
            encoding="utf-8",
        )
        result = run_cli(root, "upgrade")
        require(result.returncode == 0, "upgrade should succeed on the legacy pointer file", result)
        require("CLAUDE.md protocol appended." in result.stdout, "upgrade should report the pointer-file migration", result)
        claude_text = claude.read_text(encoding="utf-8")
        require("See [AGENTS.md](AGENTS.md) for repository instructions." in claude_text, "CLAUDE.md should keep a plain cross-reference to AGENTS.md")
        require("knowledge base protocol." not in claude_text, "CLAUDE.md should drop the legacy pointer protocol claim")
        require("`.agent-kb/` is this project's memory for coding agents" in claude_text, "CLAUDE.md should now carry the full protocol")


# Checks that validate warns when an entry file lacks the current protocol.
def test_validate_warns_missing_protocol() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        init_root(root)
        (root / "CLAUDE.md").write_text("# Notes\n\nUnrelated instructions.\n", encoding="utf-8")
        result = run_cli(root, "validate")
        require(result.returncode == 0, "protocol presence issues should be warnings, not errors", result)
        require("CLAUDE.md does not carry the current KB runtime protocol" in result.stdout, "validate should warn about the missing protocol", result)


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
        require("`.agent-kb/` is this project's memory for coding agents" in text, "upgrade should write the slim protocol", result)
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


# Writes a synthetic KB whose graph exceeds only the trim structure advisory budgets.
def write_structural_advisory_kb(root: Path) -> None:
    kb = root / ".agent-kb"
    kb.mkdir(parents=True)
    (kb / "inbox").mkdir()
    (kb / "start.md").write_text("# Agent KB Start\n", encoding="utf-8")
    (kb / "map.md").write_text("# KB Map\n", encoding="utf-8")

    # Writes one KB-relative Markdown doc with enough content to avoid scaffold cleanup signals.
    def write_doc(relative: str, body: str = "Durable note.") -> None:
        path = kb / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {path.stem.title()}\n\n{body}\n", encoding="utf-8")

    write_doc("docs/doc0.md", "See [doc1](doc1.md).")
    write_doc("docs/doc1.md", "See [doc2](doc2.md).")
    write_doc("docs/doc2.md", "See [doc3](doc3.md).")
    write_doc("docs/doc3.md", "See [doc4](doc4.md).")
    write_doc("docs/doc4.md", "Deep durable note.")
    hub_links = "\n".join(f"- [leaf {index}](leaf-{index}.md)" for index in range(12))
    write_doc("docs/hub.md", hub_links)
    for index in range(12):
        write_doc(f"docs/leaf-{index}.md")
    for index in range(14):
        write_doc(f"routes/entry-{index}.md")

    route_entries = [
        ("chain", "Deep chain", "docs/doc0.md"),
        ("hub", "Hub", "docs/hub.md"),
    ]
    route_entries.extend((f"route-{index}", f"Route {index}", f"routes/entry-{index}.md") for index in range(14))
    lines = ["routes:"]
    for route_id, task, path in route_entries:
        lines.extend([
            f"  - id: {route_id}",
            f"    task: {task}",
            "    read_first:",
            f"      - {path}",
            "    also_consider:",
        ])
    (kb / "routes.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


# Checks that trim reports depth, hub fanout, and route-count structure advisories without failing.
def test_trim_reports_structure_advisories() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp)
        write_structural_advisory_kb(root)
        result = run_cli(root, "trim")
        require(result.returncode == 0, "trim structure advisories should be non-blocking", result)
        require("Trim diagnosis: structure advisories." in result.stdout, "trim should diagnose advisory-only structure signals", result)
        require("depth advisory: docs/doc4.md is depth 4 from routes (budget 3)" in result.stdout, "trim should report deep reachable docs", result)
        require("hub advisory: docs/hub.md links to 12 docs (budget 10)" in result.stdout, "trim should report hub fanout", result)
        require("route-count advisory: routes.yaml has 16 routes (budget 15)" in result.stdout, "trim should report route count overage", result)
        require("Structure advisories are soft" in result.stdout, "trim should give a soft next instruction", result)


# Checks this repository's KB does not trigger the new structural trim advisories.
def test_trim_structure_advisories_no_self_false_positive() -> None:
    result = run_cli(REPO_DIR, "trim")
    require(result.returncode == 0, "repo self trim check should succeed", result)
    require("depth advisory:" not in result.stdout, "repo KB should not trigger depth advisory", result)
    require("hub advisory:" not in result.stdout, "repo KB should not trigger hub advisory", result)
    require("route-count advisory:" not in result.stdout, "repo KB should not trigger route-count advisory", result)


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
            "--backfill",
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
        # No flag on this run: the stored consent from the first run is what keeps the scan on.
        require(result.returncode == 0, "second stats backfill should succeed", result)
        require("Backfilled KB reads: 0 new event(s)." in result.stdout, "stats backfill should be idempotent", result)
        log = (root / ".agent-kb" / ".log" / "events.jsonl").read_text(encoding="utf-8")
        read_events = [json.loads(line) for line in log.splitlines() if '"event": "kb_read"' in line]
        require(len(read_events) == 3, "event log should contain exactly three deduped kb_read events")


# Appends a backfill-shaped kb_read event so stats fixtures can seed read history at a chosen KB path.
def append_kb_read_event(root: Path, file: str, session: str) -> None:
    log = root / ".agent-kb" / ".log" / "events.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": "2026-07-05T00:00:00Z",
        "event": "kb_read",
        "kind": "kb_read",
        "source": "backfill",
        "harness": "claude",
        "session": session,
        "file": file,
        "chars": 1200,
    }
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


# Collects the rows printed under one stats section, stopping at the next header or blank line.
def stats_section_rows(stdout: str, header: str) -> list[str]:
    rows: list[str] = []
    header_indent = None
    for line in stdout.splitlines():
        if header_indent is None:
            if line.strip().startswith(header):
                header_indent = len(line) - len(line.lstrip())
            continue
        indent = len(line) - len(line.lstrip())
        if not line.strip() or indent <= header_indent:
            break
        rows.append(line.strip())
    return rows


# Checks that reads logged before a git rename still count for the doc under its current path.
def test_stats_follows_kb_renames() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp) / "repo"
        root.mkdir(parents=True)
        result = run_cli(root, "init", "--shared")
        require(result.returncode == 0, "shared init should succeed", result)
        create_fixture_commit(root, {"README.md": "fixture\n"})
        run_fixture_git(root, "mv", ".agent-kb/architecture/overview.md", ".agent-kb/architecture/system-overview.md")
        run_fixture_git(root, "commit", "-m", "rename overview")
        append_kb_read_event(root, "architecture/overview.md", "session-old-path")

        result = run_cli(root, "stats", "--no-backfill", "--dead-sessions", "1", "--top", "30")
        require(result.returncode == 0, "stats should succeed after a KB rename", result)
        dead = stats_section_rows(result.stdout, "Dead knowledge candidates")
        require(
            "- architecture/system-overview.md" not in dead,
            "a doc read under its pre-rename path should not be reported dead",
            result,
        )
        most_read = stats_section_rows(result.stdout, "Most-read KB files")
        require(
            any(row.startswith("architecture/system-overview.md ") for row in most_read),
            "most-read chart should credit the current path",
            result,
        )
        require(
            not any(row.startswith("architecture/overview.md ") for row in most_read),
            "most-read chart should not keep a separate row for the pre-rename path",
            result,
        )


# Checks that stats degrades to path-exact read matching when no git history is available.
def test_stats_dead_candidates_without_git() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp) / "repo"
        root.mkdir(parents=True)
        # Shared mode in a directory that is not a git repo: no nested KB repo, no parent history.
        result = run_cli(root, "init", "--shared")
        require(result.returncode == 0, "shared init should succeed", result)
        kb = root / ".agent-kb"
        (kb / "architecture" / "overview.md").rename(kb / "architecture" / "system-overview.md")
        append_kb_read_event(root, "architecture/overview.md", "session-old-path")

        result = run_cli(root, "stats", "--no-backfill", "--dead-sessions", "1", "--top", "30")
        require(result.returncode == 0, "stats should succeed without git history", result)
        require("Traceback" not in result.stderr, "stats should not crash without git history", result)
        require(
            "- architecture/system-overview.md" in stats_section_rows(result.stdout, "Dead knowledge candidates"),
            "without rename history the moved doc keeps today's dead-candidate behaviour",
            result,
        )


# Checks that a bare path is never resolved against the process cwd, only against a session workdir.
def test_root_relative_rejects_bare_paths() -> None:
    from transcript_reads import command_paths, root_relative

    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp).resolve()
        (root / ".agent-kb").mkdir()
        # Stand inside root: that is the case where a bare path silently became a root path.
        previous_cwd = Path.cwd()
        os.chdir(root)
        try:
            require(
                root_relative(".agent-kb/start.md", root) is None,
                "a bare path must not resolve against the process cwd",
            )
        finally:
            os.chdir(previous_cwd)
        require(
            root_relative(str(root / ".agent-kb" / "start.md"), root) == Path(".agent-kb/start.md"),
            "an absolute path inside root must still resolve",
        )
        require(
            command_paths("cat .agent-kb/start.md", root, None) == [],
            "without a workdir a bare path must be dropped, not guessed",
        )
        require(
            Path(".agent-kb/start.md") in command_paths("cat .agent-kb/start.md", root, root),
            "with a workdir inside root a bare path must resolve",
        )
        require(
            Path(".agent-kb/start.md") in command_paths("cat .agent-kb/start.md; echo done", root, root),
            "shell punctuation glued to an operand must not create a phantom path",
        )

    from transcript_reads import codex_kb_read_path

    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        root = Path(tmp).resolve()
        (root / ".agent-kb").mkdir()
        require(
            codex_kb_read_path("cat .agent-kb/start.md; echo done", root, root) == Path(".agent-kb/start.md"),
            "a trailing `;` must be trimmed rather than dropping a real KB read",
        )
        require(
            codex_kb_read_path("cat .agent-kb/`, x", root, root) is None,
            "a junk token after `.agent-kb/` must not be recorded as a KB doc read",
        )


# Checks that Codex `exec` JS wrapper payloads surface their reads in both parsers.
def test_codex_record_tool_payload_survives_dict_type() -> None:
    from transcript_reads import codex_record_tool_payload

    # Transcripts embed tool JSON schemas, whose `type` is a dict. Testing an unhashable
    # value against a set raises TypeError, which aborted extraction for the whole file.
    schema_node = {"type": {"type": "string", "description": "a parameter"}}
    require(
        codex_record_tool_payload(schema_node) is None,
        "a dict-valued `type` must return None, not raise",
    )
    require(
        codex_record_tool_payload({"type": "response_item", "payload": {"type": {"nested": True}}}) is None,
        "a dict-valued payload `type` must return None, not raise",
    )
    require(
        codex_record_tool_payload({"type": "function_call", "payload": {"name": "exec"}}) == {"name": "exec"},
        "a well-formed function_call record must still return its payload",
    )


def test_codex_exec_wrapper_reads() -> None:
    from transcript_reads import parse_codex_tool_events, parse_codex_transcript

    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp).resolve()
        root = base / "repo"
        (root / ".agent-kb" / "workflows").mkdir(parents=True)
        workdir = str(root)
        # Unquoted `cmd` key, and a read hidden behind `&&` in a compound command.
        compound_input = (
            'const r = await tools.exec_command({cmd:"pwd && sed -n \'1,240p\' .agent-kb/start.md",'
            '"workdir":"' + workdir + '","yield_time_ms":10000}); text(r.output);\n'
        )
        # Quoted `cmd` key, the other spelling Codex records.
        quoted_input = 'const r = await tools.exec_command({"cmd":"cat .agent-kb/routes.yaml","workdir":"' + workdir + '"});'
        # Several commands in one payload, each carrying its own workdir.
        promise_input = (
            "const results = await Promise.all(["
            'tools.exec_command({"cmd":"sed -n \'1,10p\' .agent-kb/workflows/local-dev.md","workdir":"' + workdir + '"}), '
            'tools.exec_command({cmd:"cat .agent-kb/map.md","workdir":"' + workdir + '"})'
            "]);"
        )
        patch_input = "*** Begin Patch\n*** Update File: .agent-kb/start.md\n@@\n-old\n+new\n*** End Patch\n"
        transcript = base / "codex" / "sessions" / "rollout-exec-wrapper.jsonl"
        write_jsonl(
            transcript,
            [
                {"timestamp": "2026-07-05T00:00:00Z", "type": "session_meta", "payload": {"cwd": workdir}},
                {
                    "timestamp": "2026-07-05T00:00:01Z",
                    "type": "response_item",
                    "payload": {"type": "custom_tool_call", "name": "exec", "input": compound_input},
                },
                {
                    "timestamp": "2026-07-05T00:00:02Z",
                    "type": "response_item",
                    "payload": {"type": "custom_tool_call", "name": "exec", "input": quoted_input},
                },
                {
                    "timestamp": "2026-07-05T00:00:03Z",
                    "type": "response_item",
                    "payload": {"type": "custom_tool_call", "name": "exec", "input": promise_input},
                },
                {
                    "timestamp": "2026-07-05T00:00:04Z",
                    "type": "response_item",
                    "payload": {"type": "custom_tool_call", "name": "apply_patch", "input": patch_input},
                },
            ],
        )

        scan = parse_codex_transcript(transcript, root)
        require(
            sorted(read.file for read in scan.reads)
            == ["map.md", "routes.yaml", "start.md", "workflows/local-dev.md"],
            f"the exec JS wrapper should yield every read it ran, got {sorted(r.file for r in scan.reads)}",
        )
        require(
            sum(1 for read in scan.reads if read.file == "start.md") == 1,
            "an `apply_patch` body must not be counted as a KB read",
        )

        events = parse_codex_tool_events(transcript, root)
        require(
            [event.kind for event in events]
            == ["kb_entry_read", "kb_entry_read", "kb_read", "kb_read", "source_edit"],
            f"the analyzer should see the same wrapper commands, got {[e.kind for e in events]}",
        )


# Checks that a Claude Bash command reading a KB doc counts, while a bare mention does not.
def test_claude_bash_kb_read() -> None:
    from transcript_reads import parse_claude_tool_events, parse_claude_transcript

    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp).resolve()
        root = base / "repo"
        (root / ".agent-kb" / "plans").mkdir(parents=True)
        transcript = base / "claude" / "projects" / "session-bash-read.jsonl"
        write_jsonl(
            transcript,
            [
                {
                    "timestamp": "2026-07-05T00:00:00Z",
                    "cwd": str(root),
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Bash",
                                "input": {"command": "sed -n '1,40p' .agent-kb/start.md"},
                            }
                        ]
                    },
                },
                {
                    # No cwd on this record: the session cwd seen earlier has to carry over.
                    "timestamp": "2026-07-05T00:00:01Z",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Bash",
                                "input": {"command": "git add .agent-kb/plans/current.md"},
                            }
                        ]
                    },
                },
            ],
        )

        scan = parse_claude_transcript(transcript, root)
        require(
            [read.file for read in scan.reads] == ["start.md"],
            f"a Claude Bash `sed` on a KB doc should count as a read, got {[r.file for r in scan.reads]}",
        )
        events = parse_claude_tool_events(transcript, root)
        require(
            [event.kind for event in events] == ["kb_entry_read"],
            f"the analyzer should see the Bash KB read and skip `git add`, got {[e.kind for e in events]}",
        )


# Checks that a session working in another project never counts as a read of this repo's KB.
def test_stats_ignores_other_project_transcripts() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp)
        root = base / "repo"
        other = base / "other-project"
        root.mkdir()
        other.mkdir()
        init_root(root)
        init_root(other)
        (other / ".agent-kb" / "architecture").mkdir(parents=True, exist_ok=True)
        (other / ".agent-kb" / "architecture" / "vlm-eval.md").write_text("foreign\n", encoding="utf-8")
        codex_dir = base / "codex" / "sessions"
        write_jsonl(
            codex_dir / "rollout-other-project.jsonl",
            [
                {"timestamp": "2026-08-11T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(other)}},
                {
                    "timestamp": "2026-08-11T00:00:01Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps(
                            {"cmd": "sed -n '1,40p' .agent-kb/architecture/vlm-eval.md", "workdir": str(other)}
                        ),
                    },
                },
            ],
        )
        write_jsonl(
            codex_dir / "rollout-own.jsonl",
            [
                {"timestamp": "2026-08-11T01:00:00Z", "type": "session_meta", "payload": {"cwd": str(root)}},
                {
                    "timestamp": "2026-08-11T01:00:01Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps({"cmd": "sed -n '1,40p' .agent-kb/start.md", "workdir": str(root)}),
                    },
                },
            ],
        )

        # Run from inside the repo: that is what made a foreign bare path land under root.
        result = run_cli(root, "stats", "--backfill", "--codex-dir", str(codex_dir), "--no-backfill-claude", cwd=root)
        require(result.returncode == 0, "stats should succeed with a foreign transcript present", result)
        require("vlm-eval" not in result.stdout, "another project's KB read must not be counted", result)
        require("Backfilled KB reads: 1 new event(s)." in result.stdout, "only the own-repo read should count", result)
        require("scanned sessions: 1" in result.stdout, "the foreign session must not inflate the denominator", result)
        log = (root / ".agent-kb" / ".log" / "events.jsonl").read_text(encoding="utf-8")
        require("vlm-eval" not in log, "the foreign read must not reach events.jsonl either")


# Checks that --rebuild-reads drops derived kb_read events while keeping CLI history.
def test_stats_rebuild_reads_drops_polluted_events() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        init_root(root)
        log_path = root / ".agent-kb" / ".log" / "events.jsonl"
        polluted = [
            {"ts": "2026-08-01T00:00:00Z", "event": "kb_read", "session": "codex:foreign", "file": "architecture/vlm-eval.md", "chars": 0},
            {"ts": "2026-08-01T00:00:01Z", "event": "cli", "command": "validate", "exit": 0},
        ]
        with log_path.open("a", encoding="utf-8") as handle:
            for event in polluted:
                handle.write(json.dumps(event) + "\n")

        result = run_cli(root, "stats", "--backfill", "--rebuild-reads", "--no-backfill-claude", "--no-backfill-codex")
        require(result.returncode == 0, "stats --rebuild-reads should succeed", result)
        require("dropped 1 transcript-derived event(s)." in result.stdout, "the polluted read should be dropped", result)
        text = log_path.read_text(encoding="utf-8")
        require("vlm-eval" not in text, "the polluted kb_read must be gone from the log")
        require('"command": "validate"' in text, "CLI history must be preserved by a rebuild")

        result = run_cli(root, "stats", "--rebuild-reads", "--no-backfill")
        require(result.returncode == 1, "--rebuild-reads with --no-backfill should be refused", result)


# Builds one Codex rollout: a session_meta plus an optional KB read, as this repo's real rollouts look.
def write_codex_rollout(path: Path, root: Path, meta: dict, read_file: str | None = None) -> None:
    records = [{"timestamp": "2026-08-11T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(root), **meta}}]
    if read_file:
        records.append(
            {
                "timestamp": "2026-08-11T00:00:01Z",
                "type": "function_call",
                "payload": {
                    "name": "functions.exec_command",
                    "arguments": json.dumps({"cmd": f"sed -n '1,40p' .agent-kb/{read_file}", "workdir": str(root)}),
                },
            }
        )
    write_jsonl(path, records)


# Checks that a Codex sub-agent rollout is credited to the session that spawned it, not counted alone.
def test_codex_subagent_rollout_merges_into_parent() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        init_root(root)
        codex_dir = base / "codex" / "sessions"
        write_codex_rollout(
            codex_dir / "rollout-parent.jsonl",
            root,
            {"id": "sid-parent", "session_id": "sid-parent", "thread_source": "user"},
        )
        write_codex_rollout(
            codex_dir / "rollout-subagent.jsonl",
            root,
            {
                "id": "sid-child",
                "session_id": "sid-parent",
                "thread_source": "subagent",
                "parent_thread_id": "sid-parent",
            },
            read_file="start.md",
        )

        result = run_cli(root, "stats", "--backfill", "--codex-dir", str(codex_dir), "--no-backfill-claude")
        require(result.returncode == 0, "stats should succeed with a sub-agent rollout present", result)
        require("scanned sessions: 1" in result.stdout, "a sub-agent rollout must not be its own session", result)
        require("KB hit rate: 1/1 (100.0%)" in result.stdout, "the sub-agent read should credit its parent session", result)


# Checks that the several rollout files of one resumed Codex session collapse into one session.
def test_codex_resume_pair_collapses_to_one_session() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        init_root(root)
        codex_dir = base / "codex" / "sessions"
        write_codex_rollout(
            codex_dir / "rollout-first.jsonl",
            root,
            {"id": "sid-first", "session_id": "sid-first", "thread_source": "user"},
            read_file="start.md",
        )
        # A resumed rollout replays the earlier thread and ends on the session it continued into.
        write_jsonl(
            codex_dir / "rollout-resumed.jsonl",
            [
                {
                    "timestamp": "2026-08-11T01:00:00Z",
                    "type": "session_meta",
                    "payload": {"cwd": str(root), "id": "sid-first", "session_id": "sid-first", "thread_source": "user"},
                },
                {
                    "timestamp": "2026-08-11T01:00:01Z",
                    "type": "session_meta",
                    "payload": {
                        "cwd": str(root),
                        "id": "sid-resumed",
                        "session_id": "sid-first",
                        "thread_source": "user",
                        "forked_from_id": "sid-first",
                    },
                },
            ],
        )

        result = run_cli(root, "stats", "--backfill", "--codex-dir", str(codex_dir), "--no-backfill-claude")
        require(result.returncode == 0, "stats should succeed with a resumed rollout present", result)
        require("scanned sessions: 1" in result.stdout, "a resumed rollout must not add a second session", result)
        require("KB hit rate: 1/1 (100.0%)" in result.stdout, "the resumed session keeps its earlier read", result)


# Checks that a sub-agent session with no user thread of its own leaves numerator and denominator.
def test_codex_subagent_only_session_is_excluded() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        init_root(root)
        codex_dir = base / "codex" / "sessions"
        write_codex_rollout(
            codex_dir / "rollout-user.jsonl",
            root,
            {"id": "sid-user", "session_id": "sid-user", "thread_source": "user"},
        )
        write_codex_rollout(
            codex_dir / "rollout-orphan-subagent.jsonl",
            root,
            {"id": "sid-orphan", "session_id": "sid-missing-parent", "thread_source": "subagent"},
            read_file="start.md",
        )

        result = run_cli(root, "stats", "--backfill", "--codex-dir", str(codex_dir), "--no-backfill-claude")
        require(result.returncode == 0, "stats should succeed with an orphan sub-agent rollout", result)
        require("scanned sessions: 1" in result.stdout, "a sub-agent-only session must not be counted", result)
        require("KB hit rate: 0/1 (0.0%)" in result.stdout, "its read must not count either", result)
        require("Backfilled KB reads: 0 new event(s)." in result.stdout, "no orphan read should reach the log", result)


# Checks that a Claude sidechain transcript is folded into the session that spawned it.
def test_claude_sidechain_credits_parent_session() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        init_root(root)
        claude_dir = base / "claude" / "projects" / fixture_claude_project_name(root)
        write_jsonl(
            claude_dir / "main-thread.jsonl",
            [{"timestamp": "2026-08-11T00:00:00Z", "cwd": str(root), "sessionId": "claude-sid"}],
        )
        write_jsonl(
            claude_dir / "sidechain.jsonl",
            [
                {
                    "timestamp": "2026-08-11T00:00:01Z",
                    "cwd": str(root),
                    "sessionId": "claude-sid",
                    "isSidechain": True,
                    "agentId": "agent-1",
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

        result = run_cli(root, "stats", "--backfill", "--claude-dir", str(base / "claude" / "projects"), "--no-backfill-codex")
        require(result.returncode == 0, "stats should succeed with a sidechain transcript present", result)
        require("scanned sessions: 1" in result.stdout, "a sidechain file must not be its own session", result)
        require("KB hit rate: 1/1 (100.0%)" in result.stdout, "the sidechain read should credit its parent session", result)


# Checks that a cache written by an older scanner makes stats rebuild kb_read events by itself.
def test_stats_rebuilds_reads_on_cache_version_change() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        init_root(root)
        codex_dir = base / "codex" / "sessions"
        write_codex_rollout(
            codex_dir / "rollout-read.jsonl",
            root,
            {"id": "sid-user", "session_id": "sid-user", "thread_source": "user"},
            read_file="start.md",
        )

        result = run_cli(root, "stats", "--backfill", "--codex-dir", str(codex_dir), "--no-backfill-claude")
        require("Backfilled KB reads: 1 new event(s)." in result.stdout, "first run should log the read", result)

        # Simulate a log left behind by an older scanner: file-shaped session ids plus its cache.
        log_path = root / ".agent-kb" / ".log" / "events.jsonl"
        cache_path = root / ".agent-kb" / ".log" / "transcript-backfill-cache.json"
        log_path.write_text(
            json.dumps({"ts": "2026-08-01T00:00:00Z", "event": "cli", "command": "validate", "exit": 0})
            + "\n"
            + json.dumps(
                {
                    "ts": "2026-08-01T00:00:01Z",
                    "event": "kb_read",
                    "source": "backfill",
                    "session": "codex:rollout-read",
                    "file": "start.md",
                    "chars": 10,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        cache_path.write_text(json.dumps({"version": 1, "files": {}}) + "\n", encoding="utf-8")

        result = run_cli(root, "stats", "--backfill", "--codex-dir", str(codex_dir), "--no-backfill-claude")
        require(result.returncode == 0, "stats should succeed after a cache version change", result)
        require("dropped 1 stale transcript-derived event(s)" in result.stdout, "the stale read should be dropped", result)
        require("Backfilled KB reads: 1 new event(s)." in result.stdout, "the read should be rebuilt", result)
        require("KB hit rate: 1/1 (100.0%)" in result.stdout, "the rebuilt read must join the new session id", result)
        text = log_path.read_text(encoding="utf-8")
        require("codex:rollout-read" not in text, "the stale file-shaped session id must be gone")
        require('"command": "validate"' in text, "CLI history must survive an automatic rebuild")


# Checks that transcript backfill is opt-in, that each explicit answer is remembered per repo,
# and that the disclosure shows exactly while no answer is stored.
def test_stats_backfill_is_opt_in_and_remembered() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        init_root(root)
        codex_dir = base / "codex" / "sessions"
        write_codex_rollout(
            codex_dir / "rollout-read.jsonl",
            root,
            {"id": "sid-user", "session_id": "sid-user", "thread_source": "user"},
            read_file="start.md",
        )
        consent = root / ".agent-kb" / ".log" / "transcript-consent.json"

        # Nothing decided yet: stats stops before reporting anything, so the question still binds.
        result = run_cli(root, "stats", "--codex-dir", str(codex_dir), "--no-backfill-claude")
        require(result.returncode == 2, "an undecided backfill should block stats, not just warn", result)
        require("Backfilled KB reads:" not in result.stdout, "a default stats run must not scan transcripts", result)
        require("KB file churn" not in result.stdout, "a blocked run must not deliver statistics anyway", result)
        require("~/.codex/sessions" in result.stdout, "the undecided state should disclose what backfill reads", result)
        # Help text gets summarized away by a relaying agent; an open question does not.
        require("ACTION NEEDED" in result.stdout, "the undecided state should ask for a decision, not just describe flags", result)
        require(not consent.exists(), "a default run must not store a decision")

        # --backfill scans and remembers the decision.
        result = run_cli(root, "stats", "--backfill", "--codex-dir", str(codex_dir), "--no-backfill-claude")
        require("Backfilled KB reads: 1 new event(s)." in result.stdout, "--backfill should scan transcripts", result)
        require("Transcript backfill: on" in result.stdout, "an enabled backfill should report its state", result)
        require("~/.codex/sessions" not in result.stdout, "the disclosure should stop once a choice is stored", result)
        stored = json.loads(consent.read_text(encoding="utf-8"))
        require(stored.get("backfill") is True, f"consent should record the granted decision, got {stored}")
        require(bool(stored.get("decided_at")), f"consent should record when it was decided, got {stored}")

        # A bare run still scans: the stored consent, not the flag, is what enables it.
        result = run_cli(root, "stats", "--codex-dir", str(codex_dir), "--no-backfill-claude")
        require("Backfilled KB reads: 0 new event(s)." in result.stdout, "stored consent should keep the scan on", result)

        # --no-backfill stores a refusal, which keeps reporting itself instead of going silent.
        result = run_cli(root, "stats", "--no-backfill", "--codex-dir", str(codex_dir), "--no-backfill-claude")
        require("Backfilled KB reads:" not in result.stdout, "--no-backfill should not scan", result)
        require("Transcript backfill: off by stored choice" in result.stdout, "a stored refusal must stay findable", result)
        stored = json.loads(consent.read_text(encoding="utf-8"))
        require(stored.get("backfill") is False, f"consent should record the refusal, got {stored}")

        # --forget-backfill returns to the undecided state, blocking disclosure and all.
        result = run_cli(root, "stats", "--forget-backfill", "--codex-dir", str(codex_dir), "--no-backfill-claude")
        require(result.returncode == 2, "forgetting a decision should block the next report", result)
        require(not consent.exists(), "--forget-backfill should clear the stored decision")
        require("Backfilled KB reads:" not in result.stdout, "forgetting a decision should not scan", result)
        require("~/.codex/sessions" in result.stdout, "forgetting a decision should bring the disclosure back", result)


# Checks that a scraped KB path carrying control characters is dropped instead of being reported.
def test_transcript_control_character_path_is_dropped() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        init_root(root)
        codex_dir = base / "codex" / "sessions"
        # One command, two operands: a real KB read and an injected label carrying ESC and CR.
        injected = ".agent-kb/IGNORE\x1b[2K\rPREVIOUS-INSTRUCTIONS.md"
        write_jsonl(
            codex_dir / "rollout-injected.jsonl",
            [
                {"timestamp": "2026-07-05T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(root)}},
                {
                    "timestamp": "2026-07-05T00:00:01Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps({"cmd": f"cat .agent-kb/start.md '{injected}'", "workdir": str(root)}),
                    },
                },
            ],
        )

        result = run_cli(root, "stats", "--backfill", "--codex-dir", str(codex_dir), "--no-backfill-claude")
        require(result.returncode == 0, "stats should succeed with an injected read path present", result)
        require("Backfilled KB reads: 1 new event(s)." in result.stdout, "only the real read should count", result)
        require("PREVIOUS-INSTRUCTIONS" not in result.stdout, "an injected read path must not be printed", result)
        log = (root / ".agent-kb" / ".log" / "events.jsonl").read_text(encoding="utf-8")
        require("PREVIOUS-INSTRUCTIONS" not in log, "an injected read path must not reach events.jsonl either")


# Checks that an injected path is dropped from the compliance event stream instead of being
# reclassified: a junk path is not a KB doc, but it is not source exploration either.
def test_injected_read_path_leaves_the_event_stream() -> None:
    from transcript_reads import parse_claude_tool_events

    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp).resolve()
        root = base / "repo"
        (root / ".agent-kb").mkdir(parents=True)
        injected = f"{root}/.agent-kb/IGNORE\x1b[2K\rPREVIOUS-INSTRUCTIONS.md"
        transcript = base / "claude" / "projects" / "session-injected.jsonl"
        write_jsonl(
            transcript,
            [
                {
                    "timestamp": "2026-07-05T00:00:00Z",
                    "cwd": str(root),
                    "message": {
                        "content": [
                            {"type": "tool_use", "name": "Read", "input": {"file_path": f"{root}/.agent-kb/start.md"}},
                            {"type": "tool_use", "name": "Read", "input": {"file_path": injected}},
                        ]
                    },
                },
            ],
        )

        events = parse_claude_tool_events(transcript, root)
        require(
            [event.kind for event in events] == ["kb_entry_read"],
            f"an injected read path must not become an event of any kind, got {[e.kind for e in events]}",
        )
        require(
            all("PREVIOUS-INSTRUCTIONS" not in event.path for event in events),
            "an injected read path must not reach the compliance analyzer",
        )


# Checks that Codex ownership needs declared metadata: an in-root path inside a command is content,
# so a rollout that only mentions this repo that way is never parsed for reads.
def test_codex_ownership_requires_metadata() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp)
        root = base / "repo"
        other = base / "other-project"
        root.mkdir()
        other.mkdir()
        init_root(root)
        init_root(other)
        codex_dir = base / "codex" / "sessions"
        # Another project's session, reading this repo's KB by absolute path from its own workdir.
        write_jsonl(
            codex_dir / "rollout-foreign-absolute.jsonl",
            [
                {"timestamp": "2026-08-11T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(other)}},
                {
                    "timestamp": "2026-08-11T00:00:01Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps({"cmd": f"cat {root}/.agent-kb/start.md", "workdir": str(other)}),
                    },
                },
            ],
        )

        result = run_cli(root, "stats", "--backfill", "--codex-dir", str(codex_dir), "--no-backfill-claude", cwd=root)
        require(result.returncode == 0, "stats should succeed with a foreign rollout present", result)
        require("scanned sessions: 0" in result.stdout, "a rollout with no in-root metadata owns nothing", result)
        require("Backfilled KB reads: 0 new event(s)." in result.stdout, "its command must not be parsed for reads", result)


# Checks the other half of pass 1: a per-call workdir proves ownership on its own, including the
# `workdir` field of the JavaScript exec envelope, so a rollout without session_meta still counts.
def test_codex_ownership_from_call_workdir() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        init_root(root)
        codex_dir = base / "codex" / "sessions"
        js_input = 'const r = await tools.exec_command({cmd:"cat .agent-kb/start.md","workdir":"' + str(root) + '"});'
        write_jsonl(
            codex_dir / "rollout-no-meta.jsonl",
            [
                {
                    "timestamp": "2026-08-11T00:00:01Z",
                    "type": "response_item",
                    "payload": {"type": "custom_tool_call", "name": "exec", "input": js_input},
                },
            ],
        )

        result = run_cli(root, "stats", "--backfill", "--codex-dir", str(codex_dir), "--no-backfill-claude", cwd=root)
        require(result.returncode == 0, "stats should succeed with a session_meta-less rollout", result)
        require("scanned sessions: 1" in result.stdout, "a per-call workdir should establish ownership", result)
        require("KB hit rate: 1/1 (100.0%)" in result.stdout, "the read in that rollout should count", result)


# Checks that untrusted labels are escaped and capped before they are printed.
def test_safe_label_escapes_and_caps() -> None:
    from agent_kb import MAX_LABEL_CHARS, safe_label

    escaped = safe_label("start\n\r\x1b[2Kmd")
    require("\n" not in escaped and "\r" not in escaped, f"control characters should be escaped, got {escaped!r}")
    require("\x1b" not in escaped, f"escape sequences should not survive printing, got {escaped!r}")
    require(len(safe_label("x" * 500)) == MAX_LABEL_CHARS, "a long label should be capped to the print limit")


# Checks that a `~N` token stays resolvable instead of raising when no such user exists.
def test_resolve_path_tolerates_junk_home_token() -> None:
    from transcript_reads import resolve_path

    for token in ("~3", "~nosuchuser-agent-kb"):
        try:
            resolved = resolve_path(token)
        except Exception as err:  # noqa: BLE001 - the bug under test was an unexpected raise
            require(False, f"resolve_path({token!r}) should not raise, got {type(err).__name__}: {err}")
        require(
            resolved == Path(token).resolve(),
            f"resolve_path({token!r}) should fall back to plain resolve without expansion",
        )


# Checks that a transcript containing a `~3`-style token still backfills reads from every transcript.
def test_stats_backfill_survives_junk_home_token() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        init_root(root)
        codex_dir = base / "codex" / "sessions"
        write_jsonl(
            codex_dir / "rollout-tilde-token.jsonl",
            [
                {"timestamp": "2026-07-05T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(root)}},
                {
                    "timestamp": "2026-07-05T00:00:01Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps({"cmd": "cat ~3", "workdir": str(root)}),
                    },
                },
            ],
        )
        write_jsonl(
            codex_dir / "rollout-real-read.jsonl",
            [
                {"timestamp": "2026-07-05T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(root)}},
                {
                    "timestamp": "2026-07-05T00:00:01Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps({"cmd": "sed -n '1,40p' .agent-kb/start.md", "workdir": str(root)}),
                    },
                },
            ],
        )

        result = run_cli(root, "stats", "--backfill", "--codex-dir", str(codex_dir), "--dead-sessions", "1")
        require(result.returncode == 0, "stats should succeed despite a junk `~3` token", result)
        require("backfill failed" not in result.stdout, "a junk `~3` token should not fail the whole backfill", result)
        require("Backfilled KB reads: 1 new event(s)." in result.stdout, "the sibling transcript read should count", result)


# Checks that one unparseable transcript is skipped without discarding other transcripts' KB reads.
def test_backfill_skips_unparseable_transcript() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        init_root(root)
        codex_dir = base / "codex" / "sessions"
        for name in ("rollout-bad.jsonl", "rollout-good.jsonl"):
            write_jsonl(
                codex_dir / name,
                [
                    {"timestamp": "2026-07-05T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(root)}},
                    {
                        "timestamp": "2026-07-05T00:00:01Z",
                        "type": "function_call",
                        "payload": {
                            "name": "functions.exec_command",
                            "arguments": json.dumps(
                                {"cmd": "sed -n '1,40p' .agent-kb/start.md", "workdir": str(root)}
                            ),
                        },
                    },
                ],
            )

        import agent_kb

        original_parse = agent_kb.parse_codex_transcript

        # Raises only for the poisoned transcript so the scan has to isolate that one file.
        def flaky_parse(path: Path, scan_root: Path):
            if path.name == "rollout-bad.jsonl":
                raise RuntimeError("Could not determine home directory.")
            return original_parse(path, scan_root)

        agent_kb.parse_codex_transcript = flaky_parse
        try:
            # The CLI resolves --root before scanning, so mirror that here for symlinked temp paths.
            scan_root = root.resolve()
            scan = agent_kb.scan_transcripts_incremental(scan_root, scan_root / ".agent-kb", None, codex_dir)
        finally:
            agent_kb.parse_codex_transcript = original_parse

        require(
            [event.file for event in scan.reads] == ["start.md"],
            f"the good transcript's read should survive a failing sibling, got {[e.file for e in scan.reads]}",
        )


# Checks that one unparseable transcript is skipped without discarding other transcripts' compliance events.
def test_compliance_skips_unparseable_transcript() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        init_root(root)
        codex_dir = base / "codex" / "sessions"
        for name in ("rollout-bad.jsonl", "rollout-good.jsonl"):
            write_jsonl(
                codex_dir / name,
                [
                    {"timestamp": "2026-07-05T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(root)}},
                    {
                        "timestamp": "2026-07-05T00:00:01Z",
                        "type": "function_call",
                        "payload": {
                            "name": "functions.exec_command",
                            "arguments": json.dumps(
                                {"cmd": "sed -n '1,40p' .agent-kb/start.md", "workdir": str(root)}
                            ),
                        },
                    },
                ],
            )

        import transcript_reads

        original_parse = transcript_reads.parse_codex_tool_events

        # Raises only for the poisoned transcript so the collection has to isolate that one file.
        def flaky_parse(path: Path, scan_root: Path):
            if path.name == "rollout-bad.jsonl":
                raise RuntimeError("Could not determine home directory.")
            return original_parse(path, scan_root)

        transcript_reads.parse_codex_tool_events = flaky_parse
        try:
            # The analyzer resolves --root before scanning, so mirror that here for symlinked temp paths.
            scan_root = root.resolve()
            events = transcript_reads.collect_tool_events(scan_root, None, codex_dir)
        finally:
            transcript_reads.parse_codex_tool_events = original_parse

        require(
            [event.kind for event in events] == ["kb_entry_read"],
            f"the good transcript's event should survive a failing sibling, got {[e.kind for e in events]}",
        )


# Checks that a `~3` token in a non-read command cannot crash the compliance analyzer.
def test_compliance_survives_junk_home_token() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        init_root(root)
        claude_dir = base / "claude" / "projects"
        claude_dir.mkdir(parents=True)
        codex_dir = base / "codex" / "sessions"
        write_jsonl(
            codex_dir / "rollout-tilde-token.jsonl",
            [
                {"timestamp": "2026-07-05T00:00:00Z", "type": "session_meta", "payload": {"cwd": str(root)}},
                {
                    "timestamp": "2026-07-05T00:00:01Z",
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        # `rg` is not a KB_READ_COMMAND, so classify_codex_command scrapes every word.
                        "arguments": json.dumps({"cmd": "rg -n '~3' src", "workdir": str(root)}),
                    },
                },
            ],
        )

        result = run_compliance(root, claude_dir, codex_dir)
        require(result.returncode == 0, "compliance analyzer should survive a junk `~3` token", result)
        require(
            "Could not determine home directory" not in result.stderr,
            "a junk `~3` token should not reach the analyzer as an error",
            result,
        )


# Builds a Codex `session_meta` record carrying the logical session id and thread source.
def codex_session_meta(timestamp: str, workdir: Path, session_id: str, thread_source: str) -> dict:
    return {
        "timestamp": timestamp,
        "type": "session_meta",
        "payload": {"cwd": str(workdir), "session_id": session_id, "thread_source": thread_source},
    }


# Builds one Codex `exec` tool-call record for compliance fixtures.
def codex_exec_record(timestamp: str, command: str, workdir: Path) -> dict:
    return {
        "timestamp": timestamp,
        "type": "function_call",
        "payload": {
            "name": "functions.exec_command",
            "arguments": json.dumps({"cmd": command, "workdir": str(workdir)}),
        },
    }


# Checks that a sub-agent rollout is folded into the session that spawned it, never judged alone.
def test_compliance_merges_sub_agent_rollouts() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        init_root(root)
        (root / "src.py").write_text("print('hi')\n", encoding="utf-8")
        claude_dir = base / "claude" / "projects"
        claude_dir.mkdir(parents=True)
        codex_dir = base / "codex" / "sessions"
        write_jsonl(
            codex_dir / "rollout-parent.jsonl",
            [
                codex_session_meta("2026-08-12T00:00:00Z", root, "s-parent", "user"),
                codex_exec_record("2026-08-12T00:00:01Z", "git status", root),
                codex_exec_record("2026-08-12T00:00:02Z", "date", root),
                codex_exec_record("2026-08-12T00:00:03Z", "sed -n '1,40p' .agent-kb/start.md", root),
                codex_exec_record("2026-08-12T00:00:04Z", "rg print src.py", root),
            ],
        )
        # The guardian explores at record 1, before the parent's KB read at record 3: merging on the
        # per-file record index would rank it first and invent a late-KB-read verdict for the parent.
        write_jsonl(
            codex_dir / "rollout-guardian.jsonl",
            [
                codex_session_meta("2026-08-12T00:00:05Z", root, "s-parent", "guardian"),
                codex_exec_record("2026-08-12T00:00:06Z", "rg print src.py", root),
            ],
        )
        write_jsonl(
            codex_dir / "rollout-guardian-orphan.jsonl",
            [
                codex_session_meta("2026-08-12T00:10:00Z", root, "s-orphan", "guardian"),
                codex_exec_record("2026-08-12T00:10:01Z", "rg print src.py", root),
            ],
        )

        result = run_compliance(root, claude_dir, codex_dir, "--details")
        require(result.returncode == 0, "compliance analyzer should succeed on sub-agent rollouts", result)
        require(
            "Sessions analyzed (logical user sessions): 1" in result.stdout,
            "a sub-agent rollout should not add a session of its own",
            result,
        )
        require(
            "- codex:s-parent [codex] compliant category=compliant" in result.stdout,
            "sub-agent exploration must not turn the parent session into a late KB read",
            result,
        )
        require(
            "codex:s-orphan" not in result.stdout,
            "a sub-agent-only session should leave the report entirely",
            result,
        )
        require(
            "rollout-guardian" not in result.stdout,
            "no session should still be named after a transcript file",
            result,
        )


# Checks that a resumed session is one session, ordered as whole files rather than interleaved.
def test_compliance_merges_resumed_rollouts() -> None:
    from transcript_reads import transcript_file_key

    with tempfile.TemporaryDirectory(prefix="agent-kb-smoke-") as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        init_root(root)
        (root / "src.py").write_text("print('hi')\n", encoding="utf-8")
        claude_dir = base / "claude" / "projects"
        claude_dir.mkdir(parents=True)
        codex_dir = base / "codex" / "sessions"
        write_jsonl(
            codex_dir / "rollout-first.jsonl",
            [
                codex_session_meta("2026-08-12T01:00:00Z", root, "s-resumed", "user"),
                codex_exec_record("2026-08-12T01:00:01Z", "git status", root),
                codex_exec_record("2026-08-12T01:00:02Z", "date", root),
                codex_exec_record("2026-08-12T01:00:03Z", "sed -n '1,40p' .agent-kb/start.md", root),
            ],
        )
        # The resumed file explores at its record 1, so interleaving by record index would place it
        # before the KB read the first file recorded at index 3.
        write_jsonl(
            codex_dir / "rollout-resumed.jsonl",
            [
                codex_session_meta("2026-08-12T02:00:00Z", root, "s-resumed", "user"),
                codex_exec_record("2026-08-12T02:00:01Z", "rg print src.py", root),
            ],
        )

        result = run_compliance(root, claude_dir, codex_dir, "--details")
        require(result.returncode == 0, "compliance analyzer should succeed on a resumed session", result)
        require(
            "Sessions analyzed (logical user sessions): 1" in result.stdout,
            "a resumed session should be counted once, not once per transcript file",
            result,
        )
        require(
            "- codex:s-resumed [codex] compliant category=compliant first_kb=3 first_any_kb=3 first_source=5" in result.stdout,
            "the resumed file's records should continue after the first file's four records",
            result,
        )
        require(
            transcript_file_key(Path("a.jsonl"), "") > transcript_file_key(Path("z.jsonl"), "2026-08-12T00:00:00Z"),
            "a file with no timestamp must sort last so it cannot jump ahead of another file's KB read",
        )


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
        require("Sessions analyzed (logical user sessions): 11" in result.stdout, "analyzer should count all synthetic sessions", result)
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
        require("  claude\n  Sessions analyzed (logical user sessions): 3" in result.stdout, "analyzer should report Claude sessions separately", result)
        require("Claude AGENTS.md delivery:" in result.stdout, "analyzer should print AGENTS.md delivery split", result)
        require("  read AGENTS.md\n  Sessions analyzed (logical user sessions): 2" in result.stdout, "delivery split should count AGENTS.md readers", result)
        require("  did not read AGENTS.md\n  Sessions analyzed (logical user sessions): 1" in result.stdout, "delivery split should count Claude sessions without AGENTS.md", result)
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
        require("Sessions analyzed (logical user sessions): 1" in result.stdout, "since filter should keep only post-cutoff sessions", result)


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
                    "default_harness": "codex",
                    "repetitions": 1,
                    "agent_model": "sonnet",
                    "agent_models": {"claude": "sonnet", "codex": "gpt-5"},
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
                    },
                    {
                        "id": "skipped-task",
                        "prompt": "This task should not run.",
                        "assertions": [],
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
                "--task",
                "dry-run-task",
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
        require(summary["harness"] == "codex", "eval summary should use the bundle default harness")
        require(summary["judge"] is None, "eval summary should record absent judge harness")
        require(summary["repo_path"] == str(repo.resolve()), "eval summary should record bundle repo_path")
        require("runner_commit" in summary, "eval summary should record runner commit provenance")
        require("tasks_file_sha256" in summary, "eval summary should record task file provenance")
        require(len(summary["runs"]) == 1, "eval --task should only run the selected task")
        require(summary["runs"][0]["task_id"] == "dry-run-task", "eval --task should preserve the requested task")
        require(summary["runs"][0]["agent"]["status"] == "dry_run", "eval summary should avoid agent calls")
        require(
            summary["runs"][0]["agent"]["provenance"]["requested_model"] == "gpt-5",
            "eval summary should record requested agent model",
        )
        require(
            summary["runs"][0]["agent"]["provenance"]["sandbox"] == "read-only",
            "Codex dry-run provenance should record the sandbox",
        )
        require(summary["kb_mode"] == "nested", "eval summary should record nested KB mode")
        require(summary["kb_commit"] == kb_commit, "nested eval summary should record the pinned KB commit")
        require(
            summary["runs"][0]["assertions"][0]["status"] == "dry_run",
            "eval assertions should be marked dry_run without agent calls",
        )
        require(
            summary["runs"][0]["kb_access"] == {
                "reads": [],
                "first_read_index": None,
                "access_mode": "none",
                "route_followed": False,
            },
            "eval summary should record empty KB telemetry for dry-run agent calls",
        )
        require(not (results / ".raw").exists(), "dry-run should not write raw artifacts")


# Checks that shared KB bundles pin the KB through repo_commit instead of a nested KB repo.
def test_eval_runner_shared_kb_dry_run() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-kb-eval-") as tmp:
        base = Path(tmp)
        repo = base / "repo"
        repo_commit = create_fixture_commit(
            repo,
            {
                "README.md": "# Fixture\n",
                ".agent-kb/start.md": "# Agent KB Start\n",
                ".agent-kb/.kb-meta.yaml": "schema_version: 1\nmode: shared\n",
            },
        )
        bundle = base / "bundles" / "demo"
        results = base / "results"
        write_json_yaml(
            bundle / "bundle.yaml",
            {
                "name": "demo",
                "repo_path": str(repo),
                "repo_commit": repo_commit,
                "task_file": "tasks.yaml",
                "runner": {"default_harness": "codex", "repetitions": 1},
            },
        )
        write_json_yaml(bundle / "tasks.yaml", {"tasks": [{"id": "t", "prompt": "Dry run.", "assertions": []}]})
        result = subprocess.run(
            [
                sys.executable,
                str(EVAL_RUNNER),
                "--bundle",
                str(bundle),
                "--results-dir",
                str(results),
                "--dry-run",
            ],
            check=False,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        require(result.returncode == 0, "shared eval runner dry-run should succeed without kb_commit", result)
        output_files = list((results / "demo").glob("*.json"))
        require(len(output_files) == 1, "shared eval runner should write one summary JSON", result)
        summary = json.loads(output_files[0].read_text(encoding="utf-8"))
        require(summary["kb_mode"] == "shared", "shared eval summary should record shared KB mode")
        require(summary["kb_commit"] == repo_commit, "shared eval summary should use repo_commit as kb_commit provenance")


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
    codex_stream = "\n".join(
        [
            json.dumps({"type": "turn.started", "model": "gpt-5-test"}),
            json.dumps({"type": "session_meta", "payload": {"cwd": str(Path("/tmp/eval-workspace"))}}),
            json.dumps(
                {
                    "type": "item.started",
                    "item": {
                        "type": "command_execution",
                        "command": "/bin/zsh -lc \"sed -n '1,40p' .agent-kb/routes.yaml\"",
                        "status": "in_progress",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": "/bin/zsh -lc \"sed -n '1,40p' .agent-kb/routes.yaml\"",
                        "status": "completed",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "shell",
                        "arguments": json.dumps(
                            {
                                "command": ["bash", "-lc", "sed -n '1,40p' .agent-kb/start.md"],
                                "workdir": "/tmp/eval-workspace",
                            }
                        ),
                    },
                }
            ),
            json.dumps(
                {
                    "type": "function_call",
                    "payload": {
                        "name": "apply_patch",
                        "arguments": json.dumps({"patch": "*** Begin Patch\n*** End Patch\n"}),
                    },
                }
            ),
            json.dumps({"type": "agent_message", "text": "Codex final answer."}),
        ]
    )
    codex_parsed = runner.parse_codex_stream(codex_stream)
    require(codex_parsed["actual_model"] == "gpt-5-test", "Codex stream parser should capture actual model")
    require(codex_parsed["final_answer"] == "Codex final answer.", "Codex stream parser should capture final answer")
    require([call["name"] for call in codex_parsed["tool_calls"]] == ["shell", "shell", "apply_patch"], "Codex tool calls should normalize")
    prices = runner.price_table({"pricing": runner.default_pricing()}, "codex", "gpt-5.5")
    require(prices is not None and prices["input_per_million"] == 5.0, "runner should load shared Codex pricing")
    estimated = runner.estimate_cost_usd(
        {"input_tokens": 100_000, "cached_input_tokens": 60_000, "output_tokens": 10_000, "reasoning_output_tokens": 4_000},
        {
            "input_per_million": 5.0,
            "cached_input_per_million": 0.5,
            "output_per_million": 10.0,
            "reasoning_output_per_million": 2.0,
        },
    )
    require(abs(estimated - 0.298) < 0.000001, "runner should estimate subset token costs without double-counting")
    scored = runner.score_behavior_assertion(
        {"id": "read-start", "check": "tool_read", "path": ".agent-kb/start.md"},
        codex_parsed["tool_calls"],
    )
    require(scored["passed"] is True, "Codex shell reads should satisfy tool_read")
    scored = runner.score_behavior_assertion({"id": "no-edit", "check": "no_edit"}, codex_parsed["tool_calls"])
    require(scored["passed"] is False, "Codex apply_patch should fail no_edit")
    codex_meta_model_stream = json.dumps({"type": "session_meta", "payload": {"model_slug": "gpt-5-session"}})
    codex_meta_parsed = runner.parse_codex_stream(codex_meta_model_stream)
    require(codex_meta_parsed["actual_model"] == "gpt-5-session", "Codex parser should capture model_slug from session metadata")
    require(
        runner.stderr_warnings("websocket connection reset by peer") == [
            {
                "code": "websocket_connection_reset",
                "message": "stderr reported a websocket connection reset that the CLI recovered from",
            }
        ],
        "stderr warning parser should flag websocket connection resets",
    )
    require(
        runner.provenance_warnings({"actual_model": None})[0]["code"] == "actual_model_missing",
        "provenance warning parser should flag missing actual_model",
    )
    with tempfile.TemporaryDirectory(prefix="agent-kb-rollout-") as rollout_tmp:
        rollout = Path(rollout_tmp) / "rollout.jsonl"
        write_jsonl(
            rollout,
            [
                {"type": "session_meta", "payload": {"cwd": "/tmp/eval-workspace"}},
                {
                    "type": "function_call",
                    "payload": {
                        "name": "functions.exec_command",
                        "arguments": json.dumps({"cmd": "cat .agent-kb/routes.yaml", "workdir": "/tmp/eval-workspace"}),
                    },
                },
            ],
        )
        rollout_calls = runner.parse_codex_rollout_tool_calls(rollout)
    scored = runner.score_behavior_assertion(
        {"id": "read-routes", "check": "tool_read", "path": ".agent-kb/routes.yaml"},
        rollout_calls,
    )
    require(scored["passed"] is True, "Codex rollout fallback should normalize shell reads")
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
    prompt = runner.judge_prompt(
        {"id": "task", "prompt": "Answer."},
        "Final answer.",
        [{"id": "semantic", "description": "Matches."}],
        [{"path": "README.md", "text": "Reference.", "truncated": False}],
    )
    require('"assertions"' in prompt and "Final answer." in prompt, "judge prompt should render schema and payload")
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
    codex_agent_message_stdout = "\n".join(
        [
            json.dumps({"type": "turn.started", "model": "gpt-5-test"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": json.dumps(
                            {
                                "assertions": [
                                    {
                                        "id": "semantic",
                                        "passed": True,
                                        "reason": "Matches.",
                                        "confidence": 0.9,
                                    }
                                ]
                            }
                        ),
                    },
                }
            ),
        ]
    )
    codex_agent_message_judged = runner.parse_judge_output(codex_agent_message_stdout)
    require(
        codex_agent_message_judged["assertions"][0]["passed"] is True,
        "judge parser should parse Codex agent_message JSONL output",
    )
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
        codex_command = runner.codex_command("Judge.", config, "judge", base)
        require("--model" in codex_command and "gpt-5" in codex_command, "Codex judge command should pass configured model")
        require("--cd" in codex_command and str(base) in codex_command, "Codex command should pass the pinned workspace")
        require("--ignore-user-config" in codex_command, "Codex command should avoid ambient user config")
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


# Checks that eval run KB-access telemetry records read order without scoring assertions.
def test_eval_runner_kb_access_summary() -> None:
    runner = load_eval_runner_module()
    root = Path("/tmp/eval-workspace")
    routed = runner.kb_access_summary(
        [
            {"name": "shell", "input": {"cmd": "/bin/zsh -lc \"cat .agent-kb/start.md\"", "workdir": str(root)}},
            {"name": "Read", "input": {"file_path": str(root / ".agent-kb" / "routes.yaml")}},
            {"name": "shell", "input": {"cmd": "sed -n '1,40p' .agent-kb/plans/current.md .agent-kb/start.md", "workdir": str(root)}},
        ],
        root,
    )
    require(
        routed == {
            "reads": [".agent-kb/start.md", ".agent-kb/routes.yaml", ".agent-kb/plans/current.md"],
            "first_read_index": 0,
            "access_mode": "routed",
            "route_followed": True,
        },
        "KB telemetry should classify start/routes before a topic doc as routed",
    )
    direct = runner.kb_access_summary(
        [
            {"name": "Read", "input": {"file_path": ".agent-kb/plans/current.md"}},
            {"name": "Read", "input": {"file_path": ".agent-kb/start.md"}},
            {"name": "Read", "input": {"file_path": ".agent-kb/routes.yaml"}},
        ],
        root,
    )
    require(direct["access_mode"] == "direct", "KB telemetry should classify topic-first reads as direct")
    require(direct["first_read_index"] == 0, "KB telemetry should record the first KB read tool index")
    none = runner.kb_access_summary(
        [{"name": "shell", "input": {"cmd": "rg agent evals", "workdir": str(root)}}],
        root,
    )
    require(
        none == {"reads": [], "first_read_index": None, "access_mode": "none", "route_followed": False},
        "KB telemetry should classify runs with no KB reads as none",
    )


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
        test_directory_target,
        test_relative_broken_link,
        test_unreachable_topic_warning,
        test_placeholder_warning,
        test_validate_uses_routes_yaml,
        test_init_creates_current_plan_route,
        test_init_empty_scaffold_warm_start_prompt,
        test_validate_empty_scaffold_advisory,
        test_upgrade_creates_missing_current_plan,
        test_upgrade_preserves_custom_scaffold_by_default,
        test_upgrade_can_write_start_template,
        test_upgrade_writes_map_from_routes,
        test_upgrade_preserves_custom_plan_without_review,
        test_upgrade_preserves_custom_routes_without_review,
        test_upgrade_backfills_missing_meta,
        test_upgrade_refreshes_meta_schema,
        test_validate_warns_schema_drift,
        test_upgrade_preserves_newer_meta_schema,
        test_upgrade_reports_protocol_current_on_noop,
        test_init_injects_protocol_into_both_files,
        test_upgrade_refreshes_stale_protocol_in_both_files,
        test_upgrade_replaces_pointer_section_with_protocol,
        test_init_creates_both_files_with_protocol,
        test_upgrade_migrates_legacy_pointer_file,
        test_validate_warns_missing_protocol,
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
        test_trim_reports_structure_advisories,
        test_trim_structure_advisories_no_self_false_positive,
        test_kb_gitignore_init_and_upgrade,
        test_validate_metrics_and_health,
        test_note_body_redacted_in_log,
        test_stats_reports_cli_usage,
        test_stats_backfills_kb_reads,
        test_stats_follows_kb_renames,
        test_stats_dead_candidates_without_git,
        test_root_relative_rejects_bare_paths,
        test_codex_record_tool_payload_survives_dict_type,
        test_codex_exec_wrapper_reads,
        test_claude_bash_kb_read,
        test_stats_ignores_other_project_transcripts,
        test_stats_rebuild_reads_drops_polluted_events,
        test_codex_subagent_rollout_merges_into_parent,
        test_codex_resume_pair_collapses_to_one_session,
        test_codex_subagent_only_session_is_excluded,
        test_claude_sidechain_credits_parent_session,
        test_stats_rebuilds_reads_on_cache_version_change,
        test_stats_backfill_is_opt_in_and_remembered,
        test_transcript_control_character_path_is_dropped,
        test_injected_read_path_leaves_the_event_stream,
        test_codex_ownership_requires_metadata,
        test_codex_ownership_from_call_workdir,
        test_safe_label_escapes_and_caps,
        test_resolve_path_tolerates_junk_home_token,
        test_stats_backfill_survives_junk_home_token,
        test_backfill_skips_unparseable_transcript,
        test_compliance_skips_unparseable_transcript,
        test_compliance_survives_junk_home_token,
        test_compliance_merges_sub_agent_rollouts,
        test_compliance_merges_resumed_rollouts,
        test_compliance_analyzer_synthetic_transcripts,
        test_eval_runner_dry_run,
        test_eval_runner_shared_kb_dry_run,
        test_eval_runner_rejects_bad_pin,
        test_eval_runner_behavior_and_judge_parsers,
        test_eval_runner_kb_access_summary,
        test_eval_runner_calibration_from_raw,
        test_init_versioning_modes,
    ]
    for test in tests:
        test()
    print(f"OK: {len(tests)} smoke checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
