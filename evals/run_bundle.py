#!/usr/bin/env python3
"""Run a lightweight C1 regression eval bundle."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
PRICING_PATH = SCRIPT_DIR / "pricing.json"
TRANSCRIPT_SCRIPT_DIR = REPO_DIR / "skills" / "agent-context-kb" / "scripts"
sys.path.insert(0, str(TRANSCRIPT_SCRIPT_DIR))
from transcript_reads import (  # noqa: E402
    codex_command_arg,
    codex_record_tool_payload,
    codex_tool_call,
    iter_jsonl,
    path_is_inside_root,
    resolve_path,
)


EDIT_TOOL_NAMES = {"Edit", "MultiEdit", "Write", "NotebookEdit", "apply_patch"}
COMMAND_TOOL_NAMES = {"Bash", "Shell", "shell", "functions.exec_command"}
CODEX_SANDBOX = "read-only"
REFERENCE_FILE_CHAR_LIMIT = 6000
REFERENCE_TOTAL_CHAR_LIMIT = 20000
JUDGE_PROMPT_TEMPLATE = (
    "Judge the agent answer against the assertions using the reference files as ground truth. "
    "Return only JSON with this schema: "
    '{"assertions":[{"id":"...","passed":true,"reason":"...","confidence":0.0}]}. '
    "Do not include markdown.\n\n"
)


# Hashes stable text so prompt and task definitions can be compared across runs.
def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Reads the runner-level pricing table shared by all bundles.
def default_pricing() -> dict:
    if not PRICING_PATH.exists():
        return {}
    pricing = read_json_yaml(PRICING_PATH)
    if not isinstance(pricing, dict):
        raise ValueError(f"{PRICING_PATH} must contain a pricing object")
    return pricing


# Reads optional runner settings from bundle.yaml with one normalized shape.
def runner_config(bundle: dict) -> dict:
    config = bundle.get("runner", {})
    if not isinstance(config, dict):
        raise ValueError("bundle.yaml runner must be an object when present")
    agent_models = config.get("agent_models", {})
    agent_efforts = config.get("agent_efforts", {})
    judge_models = config.get("judge_models", {})
    judge_efforts = config.get("judge_efforts", {})
    pricing = config.get("pricing", default_pricing())
    if agent_models and not isinstance(agent_models, dict):
        raise ValueError("bundle.yaml runner.agent_models must be an object when present")
    if agent_efforts and not isinstance(agent_efforts, dict):
        raise ValueError("bundle.yaml runner.agent_efforts must be an object when present")
    if judge_models and not isinstance(judge_models, dict):
        raise ValueError("bundle.yaml runner.judge_models must be an object when present")
    if judge_efforts and not isinstance(judge_efforts, dict):
        raise ValueError("bundle.yaml runner.judge_efforts must be an object when present")
    if pricing and not isinstance(pricing, dict):
        raise ValueError("bundle.yaml runner.pricing must be an object when present")
    return {
        "default_harness": str(config.get("default_harness") or "claude"),
        "repetitions": int(config.get("repetitions", 1)),
        "agent_model": config.get("agent_model"),
        "agent_models": agent_models,
        "agent_effort": config.get("agent_effort"),
        "agent_efforts": agent_efforts,
        "judge_model": config.get("judge_model"),
        "judge_models": judge_models,
        "judge_effort": config.get("judge_effort"),
        "judge_efforts": judge_efforts,
        "pricing": pricing,
    }


# Selects a runner setting, allowing judge settings to vary by harness.
def runner_value(config: dict, role: str, field: str, harness: str | None = None):
    if harness:
        plural = {"model": f"{role}_models", "effort": f"{role}_efforts"}.get(field)
        values = config.get(plural)
        if isinstance(values, dict) and harness in values:
            return values[harness]
    return config.get(f"{role}_{field}")


# Captures a local CLI version without letting version lookup failures break evals.
def cli_version(command: str) -> str | None:
    try:
        result = subprocess.run(
            [command, "--version"],
            check=False,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError:
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    lines = [line for line in lines if not line.startswith("WARNING: proceeding")]
    return "\n".join(lines) or None


# Builds the provenance fields that are known before an agent or judge runs.
def base_provenance(harness: str, config: dict, role: str, versions: dict[str, str | None]) -> dict:
    return {
        "harness": harness,
        "cli_version": versions.get(harness),
        "requested_model": runner_value(config, role, "model", harness),
        "actual_model": None,
        "effort": runner_value(config, role, "effort", harness),
        "max_turns": None,
    }


# Looks up per-million-token prices for a harness/model/tier in runner pricing.
def price_table(config: dict, harness: str, model: str | None) -> dict | None:
    pricing = config.get("pricing")
    if not isinstance(pricing, dict) or not model:
        return None
    defaults = pricing.get("_default") if isinstance(pricing.get("_default"), dict) else {}
    harness_prices = pricing.get(harness)
    harness_defaults = {}
    if isinstance(harness_prices, dict) and isinstance(harness_prices.get("_default"), dict):
        harness_defaults = harness_prices["_default"]
    table = None
    if isinstance(harness_prices, dict) and isinstance(harness_prices.get(model), dict):
        table = harness_prices[model]
    elif isinstance(pricing.get(model), dict):
        table = pricing[model]
    if not isinstance(table, dict):
        return None
    if "input_per_million" in table:
        return table
    tier = str(harness_defaults.get("tier") or defaults.get("tier") or "standard")
    context = str(harness_defaults.get("context") or defaults.get("context") or "short_context")
    tier_table = table.get(tier)
    if isinstance(tier_table, dict) and "input_per_million" in tier_table:
        return tier_table
    if isinstance(tier_table, dict) and isinstance(tier_table.get(context), dict):
        return tier_table[context]
    return None


# Estimates USD cost from token usage and a per-million pricing table.
def estimate_cost_usd(usage, prices: dict | None) -> float | None:
    if not isinstance(usage, dict) or not isinstance(prices, dict):
        return None
    fields = {
        "input_tokens": "input_per_million",
        "cached_input_tokens": "cached_input_per_million",
        "cache_read_input_tokens": "cached_input_per_million",
        "output_tokens": "output_per_million",
        "reasoning_output_tokens": "reasoning_output_per_million",
    }
    total = 0.0
    used = False
    for token_field, price_field in fields.items():
        tokens = usage.get(token_field)
        price = prices.get(price_field)
        if tokens is None:
            continue
        if price is None and token_field == "reasoning_output_tokens":
            price = prices.get("output_per_million")
        if price is None:
            return None
        total += float(tokens) * float(price) / 1_000_000
        used = True
    return total if used else None


# Reads a JSON-compatible YAML file using the standard library.
def read_json_yaml(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} must use the JSON-compatible YAML subset: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object at the top level")
    return value


# Hashes a file's bytes so task definitions can be compared across result files.
def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


# Returns HEAD for a repository, or None if git cannot resolve it.
def git_head(repo: Path) -> str | None:
    try:
        return git_run(repo, ["rev-parse", "HEAD"]).stdout.decode("utf-8", errors="replace").strip()
    except ValueError:
        return None


# Returns a stable UTC timestamp for naming result files.
def result_timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# Resolves a path from bundle metadata against the bundle directory.
def resolve_bundle_path(bundle_dir: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (bundle_dir / path).resolve()


# Selects the target repository from bundle repo_path or the CLI fallback.
def resolve_repo_root(bundle_dir: Path, bundle: dict, repo_root: Path) -> Path:
    repo_path = bundle.get("repo_path")
    resolved = resolve_bundle_path(bundle_dir, str(repo_path)) if repo_path else repo_root.resolve()
    if not (resolved / ".git").exists():
        raise ValueError(f"repository path is not a git checkout: {resolved}")
    return resolved


# Selects the nested KB repository for the target repo unless explicitly overridden.
def resolve_kb_repo(repo_root: Path, kb_repo: Path | None) -> Path:
    resolved = kb_repo.resolve() if kb_repo else (repo_root / ".agent-kb").resolve()
    if not (resolved / ".git").exists():
        raise ValueError(f"KB path is not a git repository: {resolved}")
    return resolved


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


# Converts a task id into a safe filename fragment.
def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "task"


# Writes a raw local artifact and returns its path relative to the results root.
def write_raw_artifact(results_root: Path, raw_root: Path, name: str, text: str) -> str:
    raw_root.mkdir(parents=True, exist_ok=True)
    path = raw_root / name
    path.write_text(text, encoding="utf-8")
    try:
        return str(path.relative_to(results_root))
    except ValueError:
        return str(path)


# Copies a raw local artifact and returns its path relative to the results root.
def copy_raw_artifact(results_root: Path, raw_root: Path, name: str, source: Path) -> str:
    raw_root.mkdir(parents=True, exist_ok=True)
    path = raw_root / name
    shutil.copyfile(source, path)
    try:
        return str(path.relative_to(results_root))
    except ValueError:
        return str(path)


# Finds Claude tool_use records anywhere inside a stream-json event.
def extract_tool_calls(value) -> list[dict]:
    calls = []
    if isinstance(value, dict):
        if value.get("type") == "tool_use" and value.get("name"):
            calls.append({"name": value.get("name"), "input": value.get("input", {})})
        for child in value.values():
            calls.extend(extract_tool_calls(child))
    elif isinstance(value, list):
        for child in value:
            calls.extend(extract_tool_calls(child))
    return calls


# Finds assistant text content anywhere inside a stream-json event.
def extract_text_parts(value) -> list[str]:
    parts = []
    if isinstance(value, dict):
        if value.get("type") in {"text", "output_text", "agent_message"} and isinstance(value.get("text"), str):
            parts.append(value["text"])
        for child in value.values():
            parts.extend(extract_text_parts(child))
    elif isinstance(value, list):
        for child in value:
            parts.extend(extract_text_parts(child))
    return parts


# Finds the first nested value for a key in parsed Claude output.
def find_nested_key(value, key: str):
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = find_nested_key(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_nested_key(child, key)
            if found is not None:
                return found
    return None


# Parses Claude stream-json output into final text, usage, cost, model, and tool calls.
def parse_claude_stream(stdout: str) -> dict:
    tool_calls = []
    text_parts = []
    final_result = ""
    usage = None
    cost_usd = None
    actual_model = None
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            text_parts.append(line)
            continue
        tool_calls.extend(extract_tool_calls(event))
        text_parts.extend(extract_text_parts(event))
        if isinstance(event, dict) and event.get("type") == "result" and isinstance(event.get("result"), str):
            final_result = event["result"]
        usage = usage or find_nested_key(event, "usage")
        cost_usd = cost_usd or find_nested_key(event, "total_cost_usd") or find_nested_key(event, "cost_usd") or find_nested_key(event, "cost")
        actual_model = actual_model or find_nested_key(event, "model")
    final_answer = final_result or "\n".join(part for part in text_parts if part).strip()
    return {
        "final_answer": final_answer,
        "tool_calls": tool_calls,
        "usage": usage,
        "cost_usd": cost_usd,
        "actual_model": actual_model,
    }


# Parses generic JSONL agent output into final text, usage, cost, and model.
def parse_jsonl_agent_output(stdout: str) -> dict:
    text_parts = []
    usage = None
    cost_usd = None
    actual_model = None
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            text_parts.append(line)
            continue
        text_parts.extend(extract_text_parts(event))
        usage = usage or find_nested_key(event, "usage")
        cost_usd = cost_usd or find_nested_key(event, "total_cost_usd") or find_nested_key(event, "cost_usd") or find_nested_key(event, "cost")
        actual_model = actual_model or find_nested_key(event, "model")
    return {
        "final_answer": "\n".join(part for part in text_parts if part).strip(),
        "tool_calls": [],
        "usage": usage,
        "cost_usd": cost_usd,
        "actual_model": actual_model,
    }


# Converts a Codex function call payload into the scorer's existing tool-call shape.
def normalize_codex_tool_payload(payload: dict, fallback_workdir: Path | None = None) -> dict:
    name, args = codex_tool_call(payload)
    if "apply_patch" in name:
        return {"name": "apply_patch", "input": args}
    command = codex_command_arg(args)
    if command:
        workdir = args.get("workdir") or (str(fallback_workdir) if fallback_workdir else None)
        tool_input = {"command": command, "cmd": command}
        if workdir:
            tool_input["workdir"] = str(workdir)
        return {"name": "shell", "input": tool_input}
    return {"name": name or "tool", "input": args}


# Converts a Codex command_execution item into the scorer's shell-call shape.
def normalize_codex_command_execution(item: dict, fallback_workdir: Path | None = None) -> dict | None:
    if item.get("status") == "in_progress":
        return None
    command = item.get("command")
    if not isinstance(command, str) or not command:
        return None
    tool_input = {"command": command, "cmd": command}
    if fallback_workdir is not None:
        tool_input["workdir"] = str(fallback_workdir)
    return {"name": "shell", "input": tool_input}


# Finds Codex function-call records anywhere inside one JSON event.
def extract_codex_tool_calls(value, fallback_workdir: Path | None = None) -> list[dict]:
    calls = []
    if isinstance(value, dict):
        if value.get("type") == "command_execution":
            call = normalize_codex_command_execution(value, fallback_workdir)
            return [call] if call is not None else []
        payload = codex_record_tool_payload(value)
        if payload is not None:
            return [normalize_codex_tool_payload(payload, fallback_workdir)]
        elif value.get("type") in {"function_call", "custom_tool_call"} and (value.get("name") or value.get("tool_name")):
            return [normalize_codex_tool_payload(value, fallback_workdir)]
        for child in value.values():
            calls.extend(extract_codex_tool_calls(child, fallback_workdir))
    elif isinstance(value, list):
        for child in value:
            calls.extend(extract_codex_tool_calls(child, fallback_workdir))
    return calls


# Parses Codex JSONL output into final text, usage, cost, model, and normalized tool calls.
def parse_codex_stream(stdout: str) -> dict:
    parsed = parse_jsonl_agent_output(stdout)
    tool_calls = []
    current_workdir: Path | None = None
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = event.get("payload") if isinstance(event, dict) and isinstance(event.get("payload"), dict) else {}
        if isinstance(event, dict) and event.get("type") == "session_meta":
            cwd = str(payload.get("cwd") or payload.get("workdir") or "")
            current_workdir = resolve_path(cwd) if cwd else current_workdir
        tool_calls.extend(extract_codex_tool_calls(event, current_workdir))
    parsed["tool_calls"] = tool_calls
    return parsed


# Extracts the Codex thread id from JSONL stdout when the CLI reports one.
def codex_thread_id(stdout: str) -> str | None:
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "thread.started" and event.get("thread_id"):
            return str(event["thread_id"])
    return None


# Parses a Codex rollout JSONL file into normalized tool calls for fallback scoring.
def parse_codex_rollout_tool_calls(path: Path) -> list[dict]:
    calls = []
    current_workdir: Path | None = None
    for record in iter_jsonl(path):
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if record.get("type") == "session_meta":
            cwd = str(payload.get("cwd") or payload.get("workdir") or "")
            current_workdir = resolve_path(cwd) if cwd else current_workdir
            continue
        calls.extend(extract_codex_tool_calls(record, current_workdir))
    return calls


# Finds the Codex rollout JSONL produced for a workspace after a run starts.
def find_codex_rollout(
    workspace: Path,
    started_at: float,
    sessions_dir: Path | None = None,
    thread_id: str | None = None,
) -> Path | None:
    base = sessions_dir or (Path.home() / ".codex" / "sessions")
    if not base.exists():
        return None
    candidates = []
    for path in base.rglob("*.jsonl"):
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime >= started_at - 5:
            candidates.append((stat.st_mtime, path))
    if thread_id:
        for _mtime, path in sorted(candidates, reverse=True):
            if thread_id in path.name:
                return path
    for _mtime, path in sorted(candidates, reverse=True):
        for record in iter_jsonl(path):
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
            if record.get("type") != "session_meta":
                continue
            cwd = str(payload.get("cwd") or payload.get("workdir") or "")
            if cwd and path_is_inside_root(resolve_path(cwd), workspace):
                return path
    return None


# Builds the Claude print command with explicit runner-controlled settings.
def claude_command(prompt: str, output_format: str, config: dict, role: str, verbose: bool = False) -> list[str]:
    command = ["claude", "-p", "--output-format", output_format]
    model = runner_value(config, role, "model", "claude")
    effort = runner_value(config, role, "effort", "claude")
    if verbose:
        command.append("--verbose")
    if model:
        command.extend(["--model", str(model)])
    if effort:
        command.extend(["--effort", str(effort)])
    command.append(prompt)
    return command


# Builds the Codex exec command with explicit runner-controlled settings.
def codex_command(prompt: str, config: dict, role: str, workspace: Path | None = None) -> list[str]:
    command = ["codex", "exec", "--json", "--sandbox", CODEX_SANDBOX, "--ignore-user-config"]
    if workspace is not None:
        command.extend(["--cd", str(workspace)])
    model = runner_value(config, role, "model", "codex")
    effort = runner_value(config, role, "effort", "codex")
    if model:
        command.extend(["--model", str(model)])
    if effort:
        command.extend(["-c", f"model_reasoning_effort={json.dumps(str(effort))}"])
    command.append(prompt)
    return command


# Runs Claude once in the pinned workspace and stores raw stream output locally.
def run_claude(
    prompt: str,
    workspace: Path,
    dry_run: bool,
    results_root: Path,
    raw_root: Path,
    task_id: str,
    repetition: int,
    config: dict,
    provenance: dict,
) -> dict:
    if dry_run:
        return {
            "status": "dry_run",
            "exit_code": 0,
            "output_chars": 0,
            "tool_call_count": 0,
            "usage": None,
            "cost_usd": None,
            "raw_artifacts": {},
            "provenance": provenance,
            "final_answer": "",
            "tool_calls": [],
        }
    result = subprocess.run(
        claude_command(prompt, "stream-json", config, "agent", verbose=True),
        cwd=workspace,
        check=False,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    prefix = f"{safe_name(task_id)}-r{repetition}-agent"
    raw_artifacts = {
        "stdout": write_raw_artifact(results_root, raw_root, f"{prefix}.stdout.jsonl", result.stdout),
        "stderr": write_raw_artifact(results_root, raw_root, f"{prefix}.stderr.txt", result.stderr),
    }
    parsed = parse_claude_stream(result.stdout)
    provenance = {**provenance, "actual_model": parsed["actual_model"]}
    estimated_cost_usd = estimate_cost_usd(
        parsed["usage"],
        price_table(config, "claude", parsed["actual_model"] or provenance.get("requested_model")),
    )
    return {
        "status": "ok" if result.returncode == 0 else "error",
        "exit_code": result.returncode,
        "output_chars": len(parsed["final_answer"]),
        "stderr_chars": len(result.stderr),
        "tool_call_count": len(parsed["tool_calls"]),
        "usage": parsed["usage"],
        "cost_usd": parsed["cost_usd"],
        "estimated_cost_usd": estimated_cost_usd,
        "raw_artifacts": raw_artifacts,
        "provenance": provenance,
        "final_answer": parsed["final_answer"],
        "tool_calls": parsed["tool_calls"],
    }


# Runs Codex once in the pinned workspace and stores raw JSONL plus rollout output locally.
def run_codex(
    prompt: str,
    workspace: Path,
    dry_run: bool,
    results_root: Path,
    raw_root: Path,
    task_id: str,
    repetition: int,
    config: dict,
    provenance: dict,
) -> dict:
    provenance = {
        **provenance,
        "sandbox": CODEX_SANDBOX,
        "approval_policy": None,
        "ignore_user_config": True,
    }
    if dry_run:
        return {
            "status": "dry_run",
            "exit_code": 0,
            "output_chars": 0,
            "tool_call_count": 0,
            "usage": None,
            "cost_usd": None,
            "raw_artifacts": {},
            "provenance": provenance,
            "final_answer": "",
            "tool_calls": [],
        }
    started_at = time.time()
    result = subprocess.run(
        codex_command(prompt, config, "agent", workspace),
        cwd=workspace,
        check=False,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    prefix = f"{safe_name(task_id)}-r{repetition}-agent"
    raw_artifacts = {
        "stdout": write_raw_artifact(results_root, raw_root, f"{prefix}.stdout.jsonl", result.stdout),
        "stderr": write_raw_artifact(results_root, raw_root, f"{prefix}.stderr.txt", result.stderr),
    }
    parsed = parse_codex_stream(result.stdout)
    rollout = find_codex_rollout(workspace, started_at, thread_id=codex_thread_id(result.stdout))
    if rollout is not None:
        raw_artifacts["rollout"] = copy_raw_artifact(results_root, raw_root, f"{prefix}.rollout.jsonl", rollout)
        if not parsed["tool_calls"]:
            parsed["tool_calls"] = parse_codex_rollout_tool_calls(rollout)
    provenance = {**provenance, "actual_model": parsed["actual_model"]}
    estimated_cost_usd = estimate_cost_usd(
        parsed["usage"],
        price_table(config, "codex", parsed["actual_model"] or provenance.get("requested_model")),
    )
    return {
        "status": "ok" if result.returncode == 0 else "error",
        "exit_code": result.returncode,
        "output_chars": len(parsed["final_answer"]),
        "stderr_chars": len(result.stderr),
        "tool_call_count": len(parsed["tool_calls"]),
        "usage": parsed["usage"],
        "cost_usd": parsed["cost_usd"],
        "estimated_cost_usd": estimated_cost_usd,
        "raw_artifacts": raw_artifacts,
        "provenance": provenance,
        "final_answer": parsed["final_answer"],
        "tool_calls": parsed["tool_calls"],
    }


# Removes transient fields that should not be written into the summary JSON.
def agent_summary(agent_result: dict) -> dict:
    return {key: value for key, value in agent_result.items() if key not in {"final_answer", "tool_calls"}}


# Returns true when a tool input references the requested path.
def input_mentions_path(tool_input, expected_path: str) -> bool:
    if isinstance(tool_input, dict):
        return any(input_mentions_path(value, expected_path) for value in tool_input.values())
    if isinstance(tool_input, list):
        return any(input_mentions_path(value, expected_path) for value in tool_input)
    if isinstance(tool_input, str):
        return expected_path in tool_input
    return False


# Returns path spellings commonly used by tools from different working dirs.
def path_variants(expected_path: str) -> list[str]:
    variants = [expected_path]
    trimmed = expected_path.removeprefix("./")
    if trimmed not in variants:
        variants.append(trimmed)
    if trimmed.startswith(".agent-kb/"):
        variants.append(trimmed.removeprefix(".agent-kb/"))
    return variants


# Returns true when a tool input references any spelling of the requested path.
def input_mentions_any_path(tool_input, expected_path: str) -> bool:
    return any(input_mentions_path(tool_input, variant) for variant in path_variants(expected_path))


# Scores a deterministic assertion from captured tool calls.
def score_behavior_assertion(assertion: dict, tool_calls: list[dict]) -> dict:
    check = assertion.get("check")
    if check == "tool_read":
        expected_path = str(assertion.get("path") or "")
        passed = any(input_mentions_any_path(call.get("input"), expected_path) for call in tool_calls)
        reason = f"Tool input referenced {expected_path}." if passed else f"No tool input referenced {expected_path}."
    elif check == "kb_access":
        expected_path = str(assertion.get("path") or ".agent-kb")
        passed = any(input_mentions_any_path(call.get("input"), expected_path) for call in tool_calls)
        reason = f"Tool input referenced {expected_path}." if passed else f"No tool input referenced {expected_path}."
    elif check == "no_edit":
        edit_calls = [str(call.get("name")) for call in tool_calls if call.get("name") in EDIT_TOOL_NAMES]
        passed = not edit_calls
        reason = "No edit/write tools were used." if passed else f"Edit/write tools used: {', '.join(edit_calls)}."
    elif check == "no_command":
        blocked = str(assertion.get("command_contains") or "")
        command_calls = [
            call
            for call in tool_calls
            if call.get("name") in COMMAND_TOOL_NAMES and input_mentions_path(call.get("input"), blocked)
        ]
        passed = not command_calls
        reason = f"No command contained {blocked}." if passed else f"{len(command_calls)} command call(s) contained {blocked}."
    else:
        raise ValueError(f"unsupported behavior assertion check: {check}")
    return {"status": "scored", "passed": passed, "reason": reason}


# Reads bounded reference context files from the pinned workspace.
def load_reference_context(workspace: Path, task: dict) -> list[dict]:
    references = []
    remaining = REFERENCE_TOTAL_CHAR_LIMIT
    for raw_path in task.get("reference_files", []):
        relative = Path(str(raw_path))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"reference file must be a safe relative path: {raw_path}")
        path = (workspace / relative).resolve()
        try:
            path.relative_to(workspace.resolve())
        except ValueError as error:
            raise ValueError(f"reference file escapes workspace: {raw_path}") from error
        if not path.exists():
            raise ValueError(f"reference file does not exist in pinned workspace: {raw_path}")
        text = path.read_text(encoding="utf-8", errors="replace")
        take = min(len(text), REFERENCE_FILE_CHAR_LIMIT, remaining)
        references.append({"path": str(raw_path), "text": text[:take], "truncated": take < len(text)})
        remaining -= take
        if remaining <= 0:
            break
    return references


# Builds a strict JSON prompt for semantic assertion judging.
def judge_prompt(task: dict, final_answer: str, semantic_assertions: list[dict], references: list[dict]) -> str:
    payload = {
        "task_prompt": task_prompt(task),
        "agent_answer": final_answer,
        "reference_files": references,
        "assertions": [
            {"id": assertion.get("id"), "description": assertion.get("description")}
            for assertion in semantic_assertions
        ],
    }
    return JUDGE_PROMPT_TEMPLATE + json.dumps(payload, indent=2, sort_keys=True)


# Extracts the first JSON object from Claude judge text.
def extract_json_object(text: str) -> dict:
    stripped = text.strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("judge did not return a JSON object")
        value = json.loads(stripped[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("judge JSON must be an object")
    return value


# Parses judge output and returns the judge payload plus usage, cost, and model.
def parse_judge_output(stdout: str) -> dict:
    usage = None
    cost_usd = None
    actual_model = None
    try:
        outer = extract_json_object(stdout)
    except ValueError:
        parsed = parse_jsonl_agent_output(stdout)
        usage = parsed["usage"]
        cost_usd = parsed["cost_usd"]
        actual_model = parsed["actual_model"]
        result_text = parsed["final_answer"]
    else:
        usage = outer.get("usage")
        cost_usd = outer.get("cost_usd") or outer.get("total_cost_usd") or outer.get("cost")
        actual_model = find_nested_key(outer, "model")
        result_text = outer.get("result") or outer.get("content") or stdout
    payload = extract_json_object(str(result_text))
    assertions = payload.get("assertions")
    if not isinstance(assertions, list):
        raise ValueError("judge JSON must contain an assertions list")
    return {"assertions": assertions, "usage": usage, "cost_usd": cost_usd, "actual_model": actual_model}


# Runs the selected semantic judge and stores raw judge output locally.
def run_judge(
    task: dict,
    agent_result: dict,
    workspace: Path,
    results_root: Path,
    raw_root: Path,
    task_id: str,
    repetition: int,
    semantic_assertions: list[dict],
    judge_harness: str,
    config: dict,
    provenance: dict,
) -> dict:
    references = load_reference_context(workspace, task)
    prompt = judge_prompt(task, agent_result["final_answer"], semantic_assertions, references)
    if judge_harness == "claude":
        command = claude_command(prompt, "json", config, "judge")
        stdout_name = f"{safe_name(task_id)}-r{repetition}-judge.stdout.json"
    elif judge_harness == "codex":
        command = codex_command(prompt, config, "judge", workspace)
        stdout_name = f"{safe_name(task_id)}-r{repetition}-judge.stdout.jsonl"
    else:
        raise ValueError(f"unsupported judge harness: {judge_harness}")
    result = subprocess.run(
        command,
        cwd=workspace,
        check=False,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    prefix = f"{safe_name(task_id)}-r{repetition}-judge"
    provenance = {
        **provenance,
        "prompt_template_sha256": sha256_text(JUDGE_PROMPT_TEMPLATE),
    }
    raw_artifacts = {
        "stdout": write_raw_artifact(results_root, raw_root, stdout_name, result.stdout),
        "stderr": write_raw_artifact(results_root, raw_root, f"{prefix}.stderr.txt", result.stderr),
    }
    if result.returncode != 0:
        return {
            "status": "error",
            "exit_code": result.returncode,
            "raw_artifacts": raw_artifacts,
            "assertions": [],
            "provenance": provenance,
        }
    try:
        parsed = parse_judge_output(result.stdout)
    except ValueError as error:
        return {
            "status": "error",
            "exit_code": result.returncode,
            "raw_artifacts": raw_artifacts,
            "assertions": [],
            "error": str(error),
            "provenance": provenance,
        }
    provenance = {**provenance, "actual_model": parsed["actual_model"]}
    estimated_cost_usd = estimate_cost_usd(
        parsed["usage"],
        price_table(config, judge_harness, parsed["actual_model"] or provenance.get("requested_model")),
    )
    return {
        "status": "ok",
        "exit_code": result.returncode,
        "raw_artifacts": raw_artifacts,
        "assertions": parsed["assertions"],
        "usage": parsed["usage"],
        "cost_usd": parsed["cost_usd"],
        "estimated_cost_usd": estimated_cost_usd,
        "provenance": provenance,
    }


# Builds assertion rows by mixing deterministic checks with optional judge output.
def assertion_rows(
    task: dict,
    agent_result: dict,
    workspace: Path,
    judge_harness: str | None,
    dry_run: bool,
    results_root: Path,
    raw_root: Path,
    task_id: str,
    repetition: int,
    config: dict,
    judge_provenance: dict | None,
) -> tuple[list[dict], dict | None]:
    assertions = task.get("assertions", [])
    if not isinstance(assertions, list):
        raise ValueError(f"task {task.get('id', '<missing>')} assertions must be a list")
    rows = []
    semantic_assertions = []
    for assertion in assertions:
        if not isinstance(assertion, dict):
            raise ValueError(f"task {task.get('id', '<missing>')} has a non-object assertion")
        check = str(assertion.get("check") or "judge")
        row = {"id": assertion.get("id"), "description": assertion.get("description"), "check": check}
        if dry_run:
            row.update({"status": "dry_run", "passed": None})
        elif check == "judge":
            row.update({"status": "pending_judge", "passed": None})
            semantic_assertions.append(assertion)
        elif check in {"tool_read", "kb_access", "no_edit", "no_command"}:
            row.update(score_behavior_assertion(assertion, agent_result["tool_calls"]))
        else:
            raise ValueError(f"assertion {assertion.get('id', '<missing>')} has unsupported check: {check}")
        rows.append(row)
    judge_result = None
    if judge_harness and semantic_assertions and not dry_run:
        if agent_result["status"] != "ok":
            for row in rows:
                if row["check"] == "judge":
                    row.update({"status": "agent_error", "passed": None, "reason": "Agent run failed before judging."})
            return rows, None
        if judge_provenance is None:
            raise ValueError("judge provenance is required when judge_harness is set")
        judge_result = run_judge(
            task,
            agent_result,
            workspace,
            results_root,
            raw_root,
            task_id,
            repetition,
            semantic_assertions,
            judge_harness,
            config,
            judge_provenance,
        )
        if judge_result.get("status") != "ok":
            for row in rows:
                if row["check"] == "judge":
                    row.update({"status": "judge_error", "passed": None, "reason": judge_result.get("error") or "Judge run failed."})
            return rows, judge_result
        judged_by_id = {item.get("id"): item for item in judge_result.get("assertions", []) if isinstance(item, dict)}
        for row in rows:
            if row["check"] != "judge":
                continue
            judged = judged_by_id.get(row["id"])
            if not judged:
                row.update({"status": "judge_missing", "passed": None})
                continue
            row.update(
                {
                    "status": "scored",
                    "passed": bool(judged.get("passed")),
                    "reason": judged.get("reason"),
                    "confidence": judged.get("confidence"),
                }
            )
    return rows, judge_result


# Resolves a raw artifact path that may be stored relative to the results root.
def resolve_raw_artifact(results_root: Path, artifact: str) -> Path:
    path = Path(artifact)
    if path.is_absolute():
        return path
    return results_root / path


# Parses a stored agent raw artifact back into the final answer needed for rejudging.
def parse_agent_raw(harness: str, stdout: str) -> dict:
    if harness == "claude":
        return parse_claude_stream(stdout)
    if harness == "codex":
        return parse_codex_stream(stdout)
    return parse_jsonl_agent_output(stdout)


# Returns the judge assertions from a task definition.
def semantic_assertions_for_task(task: dict) -> list[dict]:
    assertions = task.get("assertions", [])
    if not isinstance(assertions, list):
        raise ValueError(f"task {task.get('id', '<missing>')} assertions must be a list")
    return [assertion for assertion in assertions if isinstance(assertion, dict) and str(assertion.get("check") or "judge") == "judge"]


# Rejudges semantic assertions from an existing summary and records inter-judge agreement.
def calibrate_summary(
    bundle_dir: Path,
    repo_root: Path,
    kb_repo: Path | None,
    summary_path: Path,
    judge_harness: str,
    results_root: Path,
    run_id: str,
) -> dict:
    bundle = read_json_yaml(bundle_dir / "bundle.yaml")
    config = runner_config(bundle)
    repo_root = resolve_repo_root(bundle_dir, bundle, repo_root)
    kb_repo = resolve_kb_repo(repo_root, kb_repo)
    tasks_path = bundle_dir / str(bundle.get("task_file", "tasks.yaml"))
    tasks_doc = read_json_yaml(tasks_path)
    tasks_by_id = {str(task.get("id")): task for task in tasks_doc.get("tasks", []) if isinstance(task, dict)}
    original = json.loads(summary_path.read_text(encoding="utf-8"))
    repo_commit = str(original.get("repo_commit") or bundle.get("repo_commit") or "")
    kb_commit = str(original.get("kb_commit") or bundle.get("kb_commit") or "")
    if not repo_commit or not kb_commit:
        raise ValueError("calibration needs repo_commit and kb_commit in the summary or bundle")
    versions = {"claude": cli_version("claude"), "codex": cli_version("codex")}
    judge_provenance = base_provenance(judge_harness, config, "judge", versions)
    raw_root = results_root / ".raw" / str(bundle.get("name", bundle_dir.name)) / run_id
    rows = []
    compared = 0
    agreed = 0
    with prepared_workspace(repo_root, kb_repo, repo_commit, kb_commit) as workspace:
        for run in original.get("runs", []):
            task_id = str(run.get("task_id") or "")
            task = tasks_by_id.get(task_id)
            if task is None:
                raise ValueError(f"summary references unknown task: {task_id}")
            semantic_assertions = semantic_assertions_for_task(task)
            if not semantic_assertions:
                continue
            agent_stdout = run.get("agent", {}).get("raw_artifacts", {}).get("stdout")
            if not agent_stdout:
                raise ValueError(f"summary run {task_id} is missing agent stdout raw artifact")
            stdout_path = resolve_raw_artifact(results_root, str(agent_stdout))
            parsed_agent = parse_agent_raw(str(run.get("harness") or original.get("harness") or "claude"), stdout_path.read_text(encoding="utf-8"))
            agent_result = {"final_answer": parsed_agent["final_answer"], "tool_calls": parsed_agent["tool_calls"], "status": "ok"}
            judge_result = run_judge(
                task,
                agent_result,
                workspace,
                results_root,
                raw_root,
                task_id,
                int(run.get("repetition") or 1),
                semantic_assertions,
                judge_harness,
                config,
                judge_provenance,
            )
            old_by_id = {
                assertion.get("id"): assertion
                for assertion in run.get("assertions", [])
                if isinstance(assertion, dict) and assertion.get("check") == "judge"
            }
            new_by_id = {item.get("id"): item for item in judge_result.get("assertions", []) if isinstance(item, dict)}
            for assertion in semantic_assertions:
                assertion_id = assertion.get("id")
                old = old_by_id.get(assertion_id, {})
                new = new_by_id.get(assertion_id, {})
                old_passed = old.get("passed")
                new_passed = new.get("passed")
                agreement = old_passed == new_passed if isinstance(old_passed, bool) and isinstance(new_passed, bool) else None
                if agreement is not None:
                    compared += 1
                    agreed += 1 if agreement else 0
                rows.append(
                    {
                        "task_id": task_id,
                        "repetition": run.get("repetition"),
                        "assertion_id": assertion_id,
                        "old_passed": old_passed,
                        "new_passed": new_passed,
                        "agreement": agreement,
                        "old_reason": old.get("reason"),
                        "new_reason": new.get("reason"),
                        "judge_status": judge_result.get("status"),
                        "judge_raw_artifacts": judge_result.get("raw_artifacts"),
                        "judge_provenance": judge_result.get("provenance"),
                    }
                )
    agreement_rate = agreed / compared if compared else None
    return {
        "bundle": bundle.get("name", bundle_dir.name),
        "source_summary": str(summary_path),
        "judge": judge_harness,
        "run_id": run_id,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "agreement": {"compared": compared, "agreed": agreed, "rate": agreement_rate},
        "rows": rows,
    }


# Runs every task in a pinned workspace and returns the JSON-serializable summary.
def run_bundle(
    bundle_dir: Path,
    repo_root: Path,
    kb_repo: Path | None,
    harness: str,
    dry_run: bool,
    judge_harness: str | None,
    results_root: Path,
    run_id: str,
) -> dict:
    bundle = read_json_yaml(bundle_dir / "bundle.yaml")
    config = runner_config(bundle)
    harness = harness or config["default_harness"]
    repo_root = resolve_repo_root(bundle_dir, bundle, repo_root)
    kb_repo = resolve_kb_repo(repo_root, kb_repo)
    tasks_path = bundle_dir / str(bundle.get("task_file", "tasks.yaml"))
    tasks_doc = read_json_yaml(tasks_path)
    tasks = tasks_doc.get("tasks", [])
    if not isinstance(tasks, list) or not tasks:
        raise ValueError(f"{tasks_path} must define a non-empty tasks list")
    if harness not in {"claude", "codex"}:
        raise ValueError("--harness must be claude or codex")
    if judge_harness and judge_harness not in {"claude", "codex"}:
        raise ValueError("--judge must be claude or codex")
    repetitions = config["repetitions"]
    repo_commit = str(bundle.get("repo_commit") or "")
    kb_commit = str(bundle.get("kb_commit") or "")
    if not repo_commit or not kb_commit:
        raise ValueError("bundle.yaml must define repo_commit and kb_commit")
    runs = []
    raw_root = results_root / ".raw" / str(bundle.get("name", bundle_dir.name)) / run_id
    versions = {"claude": cli_version("claude"), "codex": cli_version("codex")}
    agent_provenance = base_provenance(harness, config, "agent", versions)
    judge_provenance = base_provenance(judge_harness, config, "judge", versions) if judge_harness else None
    agent_runner = run_claude if harness == "claude" else run_codex
    with prepared_workspace(repo_root, kb_repo, repo_commit, kb_commit) as workspace:
        for task in tasks:
            if not isinstance(task, dict):
                raise ValueError(f"{tasks_path} contains a non-object task")
            task_id = str(task.get("id") or "")
            if not task_id:
                raise ValueError(f"{tasks_path} contains a task without id")
            prompt = task_prompt(task)
            for repetition in range(1, repetitions + 1):
                agent_result = agent_runner(
                    prompt,
                    workspace,
                    dry_run,
                    results_root,
                    raw_root,
                    task_id,
                    repetition,
                    config,
                    agent_provenance,
                )
                assertions, judge_result = assertion_rows(
                    task,
                    agent_result,
                    workspace,
                    judge_harness,
                    dry_run,
                    results_root,
                    raw_root,
                    task_id,
                    repetition,
                    config,
                    judge_provenance,
                )
                run = {
                    "task_id": task_id,
                    "repetition": repetition,
                    "harness": harness,
                    "agent": agent_summary(agent_result),
                    "assertions": assertions,
                }
                if judge_result is not None:
                    run["judge"] = {key: value for key, value in judge_result.items() if key != "assertions"}
                runs.append(
                    run
                )
    return {
        "bundle": bundle.get("name", bundle_dir.name),
        "repo_path": str(repo_root),
        "repo_commit": repo_commit,
        "kb_commit": kb_commit,
        "runner_commit": git_head(REPO_DIR),
        "tasks_file_sha256": sha256_file(tasks_path),
        "harness": harness,
        "dry_run": dry_run,
        "judge": judge_harness,
        "run_id": run_id,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "runs": runs,
    }


# Builds the command-line parser for the eval runner.
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an agent-kb C1 eval bundle.")
    parser.add_argument("--bundle", required=True, help="Bundle directory containing bundle.yaml and tasks.yaml.")
    parser.add_argument("--repo-root", default=".", help="Fallback git repository when bundle.yaml omits repo_path.")
    parser.add_argument("--kb-repo", default=None, help="Override nested KB git repository used for kb_commit archives.")
    parser.add_argument(
        "--harness",
        choices=["claude", "codex"],
        default=None,
        help="Harness runner to use; defaults to bundle runner.default_harness or claude.",
    )
    parser.add_argument("--results-dir", default="evals/results", help="Directory for summary result JSON files.")
    parser.add_argument("--dry-run", action="store_true", help="Validate the bundle without invoking the agent.")
    parser.add_argument(
        "--judge",
        nargs="?",
        const="claude",
        choices=["claude", "codex"],
        default=None,
        help="Run semantic assertions through the selected judge; omitted value defaults to claude.",
    )
    parser.add_argument("--calibrate-summary", default=None, help="Existing summary JSON whose raw answers should be rejudged.")
    return parser


# Parses args, runs the bundle, and writes one summary JSON result.
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bundle_dir = Path(args.bundle).expanduser().resolve()
    repo_root = Path(args.repo_root).expanduser().resolve()
    kb_repo = Path(args.kb_repo).expanduser().resolve() if args.kb_repo else None
    results_root = Path(args.results_dir).expanduser().resolve()
    run_id = result_timestamp()
    try:
        if args.calibrate_summary:
            summary = calibrate_summary(
                bundle_dir,
                repo_root,
                kb_repo,
                Path(args.calibrate_summary).expanduser().resolve(),
                args.judge or "codex",
                results_root,
                run_id,
            )
        else:
            summary = run_bundle(bundle_dir, repo_root, kb_repo, args.harness, args.dry_run, args.judge, results_root, run_id)
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    bundle_results = results_root / str(summary["bundle"])
    bundle_results.mkdir(parents=True, exist_ok=True)
    if args.calibrate_summary:
        suffix = f"{summary['judge']}-judge-calibration"
    else:
        suffix = "dry-run" if args.dry_run else summary["harness"]
    output_path = bundle_results / f"{run_id}-{suffix}.json"
    output_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
