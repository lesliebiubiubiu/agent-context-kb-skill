#!/usr/bin/env python3
"""Run a lightweight C1 regression eval bundle."""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from contextlib import contextmanager
from pathlib import Path


# Reads a JSON-compatible YAML file using the standard library.
def read_json_yaml(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} must use the JSON-compatible YAML subset: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object at the top level")
    return value


# Runs git in a repository and returns completed output or raises a clear error.
def git_run(repo: Path, args: list[str], stdout: int | None = subprocess.PIPE) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        stdout=stdout,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"git {' '.join(args)} failed in {repo}: {detail}")
    return result


# Returns a stable UTC timestamp for naming result files.
def result_timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# Creates a detached worktree for the pinned repository commit.
def create_repo_worktree(repo_root: Path, checkout: Path, repo_commit: str) -> None:
    git_run(repo_root, ["worktree", "add", "--detach", str(checkout), repo_commit], stdout=subprocess.DEVNULL)


# Removes a temporary git worktree and falls back to pruning stale metadata.
def remove_repo_worktree(repo_root: Path, checkout: Path) -> None:
    result = subprocess.run(
        ["git", "worktree", "remove", "--force", str(checkout)],
        cwd=repo_root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        subprocess.run(["git", "worktree", "prune"], cwd=repo_root, check=False, stdout=subprocess.DEVNULL)


# Restores the nested KB snapshot into the isolated repository checkout.
def restore_kb_snapshot(kb_repo: Path, checkout: Path, kb_commit: str) -> None:
    kb_checkout = checkout / ".agent-kb"
    kb_checkout.mkdir(parents=True, exist_ok=True)
    archive_result = git_run(kb_repo, ["archive", "--format=tar", kb_commit])
    with tarfile.open(fileobj=io.BytesIO(archive_result.stdout), mode="r:") as archive:
        archive.extractall(kb_checkout, filter="data")


# Verifies the isolated workspace has both the repo checkout and KB entry file.
def verify_workspace_snapshot(checkout: Path) -> None:
    if not (checkout / ".git").exists():
        raise ValueError("prepared workspace is missing the repository checkout")
    if not (checkout / ".agent-kb" / "start.md").exists():
        raise ValueError("prepared workspace is missing .agent-kb/start.md from the KB snapshot")


# Prepares and cleans up an isolated pinned workspace for an eval run.
@contextmanager
def prepared_workspace(repo_root: Path, kb_repo: Path, repo_commit: str, kb_commit: str):
    with tempfile.TemporaryDirectory(prefix="agent-kb-eval-worktree-") as tmp:
        checkout = Path(tmp) / "repo"
        create_repo_worktree(repo_root, checkout, repo_commit)
        try:
            restore_kb_snapshot(kb_repo, checkout, kb_commit)
            verify_workspace_snapshot(checkout)
            yield checkout
        finally:
            remove_repo_worktree(repo_root, checkout)


# Builds the prompt sent to the agent for one eval task.
def task_prompt(task: dict) -> str:
    prompt = str(task.get("prompt", "")).strip()
    if not prompt:
        raise ValueError(f"task {task.get('id', '<missing>')} is missing prompt")
    return prompt


# Runs Claude once in the pinned workspace and returns a compact non-transcript summary.
def run_claude(prompt: str, workspace: Path, dry_run: bool) -> dict:
    if dry_run:
        return {
            "status": "dry_run",
            "exit_code": 0,
            "output_chars": 0,
            "usage": None,
            "cost_usd": None,
        }
    result = subprocess.run(
        ["claude", "-p", "--output-format", "json", prompt],
        cwd=workspace,
        check=False,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    parsed = None
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError:
        pass
    output_text = ""
    usage = None
    cost_usd = None
    if isinstance(parsed, dict):
        output_text = str(parsed.get("result") or parsed.get("content") or "")
        usage = parsed.get("usage")
        cost_usd = parsed.get("cost_usd") or parsed.get("total_cost_usd") or parsed.get("cost")
    if not output_text:
        output_text = result.stdout
    return {
        "status": "ok" if result.returncode == 0 else "error",
        "exit_code": result.returncode,
        "output_chars": len(output_text),
        "stderr_chars": len(result.stderr),
        "usage": usage,
        "cost_usd": cost_usd,
    }


# Builds placeholder assertion rows until the judge step is implemented.
def assertion_rows(task: dict) -> list[dict]:
    assertions = task.get("assertions", [])
    if not isinstance(assertions, list):
        raise ValueError(f"task {task.get('id', '<missing>')} assertions must be a list")
    rows = []
    for assertion in assertions:
        if not isinstance(assertion, dict):
            raise ValueError(f"task {task.get('id', '<missing>')} has a non-object assertion")
        rows.append(
            {
                "id": assertion.get("id"),
                "description": assertion.get("description"),
                "status": "pending_judge",
                "passed": None,
            }
        )
    return rows


# Runs every task in a pinned workspace and returns the JSON-serializable summary.
def run_bundle(bundle_dir: Path, repo_root: Path, kb_repo: Path, harness: str, dry_run: bool) -> dict:
    bundle = read_json_yaml(bundle_dir / "bundle.yaml")
    tasks_path = bundle_dir / str(bundle.get("task_file", "tasks.yaml"))
    tasks_doc = read_json_yaml(tasks_path)
    tasks = tasks_doc.get("tasks", [])
    if not isinstance(tasks, list) or not tasks:
        raise ValueError(f"{tasks_path} must define a non-empty tasks list")
    if harness != "claude":
        raise ValueError("runner v1 only supports --harness claude")
    repetitions = int(bundle.get("runner", {}).get("repetitions", 1))
    repo_commit = str(bundle.get("repo_commit") or "")
    kb_commit = str(bundle.get("kb_commit") or "")
    if not repo_commit or not kb_commit:
        raise ValueError("bundle.yaml must define repo_commit and kb_commit")
    runs = []
    with prepared_workspace(repo_root, kb_repo, repo_commit, kb_commit) as workspace:
        for task in tasks:
            if not isinstance(task, dict):
                raise ValueError(f"{tasks_path} contains a non-object task")
            task_id = str(task.get("id") or "")
            if not task_id:
                raise ValueError(f"{tasks_path} contains a task without id")
            prompt = task_prompt(task)
            for repetition in range(1, repetitions + 1):
                agent_result = run_claude(prompt, workspace, dry_run)
                runs.append(
                    {
                        "task_id": task_id,
                        "repetition": repetition,
                        "harness": harness,
                        "agent": agent_result,
                        "assertions": assertion_rows(task),
                    }
                )
    return {
        "bundle": bundle.get("name", bundle_dir.name),
        "repo_commit": repo_commit,
        "kb_commit": kb_commit,
        "harness": harness,
        "dry_run": dry_run,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "runs": runs,
    }


# Builds the command-line parser for the eval runner.
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an agent-kb C1 eval bundle.")
    parser.add_argument("--bundle", required=True, help="Bundle directory containing bundle.yaml and tasks.yaml.")
    parser.add_argument("--repo-root", default=".", help="Git repository used for repo_commit worktrees.")
    parser.add_argument("--kb-repo", default=".agent-kb", help="Nested KB git repository used for kb_commit archives.")
    parser.add_argument("--harness", default="claude", help="Harness runner to use; v1 supports claude.")
    parser.add_argument("--results-dir", default="evals/results", help="Directory for summary result JSON files.")
    parser.add_argument("--dry-run", action="store_true", help="Validate the bundle without invoking the agent.")
    return parser


# Parses args, runs the bundle, and writes one summary JSON result.
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bundle_dir = Path(args.bundle).expanduser().resolve()
    repo_root = Path(args.repo_root).expanduser().resolve()
    kb_repo = Path(args.kb_repo).expanduser().resolve()
    results_root = Path(args.results_dir).expanduser().resolve()
    try:
        summary = run_bundle(bundle_dir, repo_root, kb_repo, args.harness, args.dry_run)
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    bundle_results = results_root / str(summary["bundle"])
    bundle_results.mkdir(parents=True, exist_ok=True)
    suffix = "dry-run" if args.dry_run else args.harness
    output_path = bundle_results / f"{result_timestamp()}-{suffix}.json"
    output_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
