#!/usr/bin/env python3
"""Run one bounded Luna worker through an ephemeral Codex CLI process."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

MODEL = "gpt-5.6-luna"
EFFORT = "max"
REQUIRED_ASSIGNMENT_KEYS = {
    "task_id", "stage_id", "route_id", "project_goal_link", "question",
    "decision_affected", "inputs", "scientific_contract", "allowed_actions",
    "forbidden_actions", "write_scope", "method", "result_path",
    "targeted_sanity_check", "resource_ceiling", "repair_authority", "stopping_rule",
}
REQUIRED_RESULT_KEYS = {
    "experiment_id", "status", "executed", "primary_results", "sanity_check",
    "deviations", "repairs", "resource_use", "artifacts", "claim_boundary",
    "limitations", "brain_decision_needed",
}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def contained(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def parse_version(text: str) -> tuple[int, ...]:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)(?:[-.]alpha\.(\d+))?", text)
    return tuple(int(part or 0) for part in match.groups()) if match else (0,)


def cli_candidates(explicit: str | None) -> list[Path]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("CODEX_LUNA_CLI_EXE"):
        candidates.append(Path(os.environ["CODEX_LUNA_CLI_EXE"]))
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    if str(local):
        candidates.extend((local / "OpenAI" / "Codex" / "bin").glob("*/codex.exe"))
        candidates.append(local / "OpenAI" / "Codex" / "bin" / "codex.exe")
    from shutil import which
    found = which("codex")
    if found:
        candidates.append(Path(found))
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.resolve()) if candidate.exists() else str(candidate)
        if candidate.is_file() and key not in seen:
            seen.add(key)
            unique.append(candidate.resolve())
    return unique


def select_cli(explicit: str | None) -> tuple[Path, str]:
    usable: list[tuple[tuple[int, ...], float, Path, str]] = []
    for candidate in cli_candidates(explicit):
        try:
            proc = subprocess.run(
                [str(candidate), "--version"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        output = (proc.stdout + proc.stderr).strip()
        if proc.returncode == 0:
            usable.append((parse_version(output), candidate.stat().st_mtime, candidate, output))
    if not usable:
        raise RuntimeError("No usable Codex CLI executable was found")
    _, _, path, version = max(usable, key=lambda item: (item[0], item[1]))
    return path, version


def verify_model_catalog(cli: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [str(cli), "debug", "models"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    output = proc.stdout + proc.stderr
    if proc.returncode != 0 or MODEL not in output:
        raise RuntimeError(f"Selected CLI does not advertise {MODEL}")
    try:
        catalog = json.loads(proc.stdout)
        model = next(item for item in catalog["models"] if item.get("slug") == MODEL)
        efforts = [item.get("effort") for item in model.get("supported_reasoning_levels", [])]
        if EFFORT not in efforts:
            raise RuntimeError(f"Selected CLI does not advertise reasoning effort {EFFORT}")
        return {"model": MODEL, "reasoning_effort": EFFORT, "supported_reasoning_efforts": efforts}
    except (json.JSONDecodeError, KeyError, StopIteration, TypeError):
        if EFFORT not in output:
            raise RuntimeError(f"Selected CLI does not advertise reasoning effort {EFFORT}")
        return {"model": MODEL, "reasoning_effort": EFFORT, "catalog_format": "text-fallback"}


def validate_assignment(raw: dict[str, Any], workdir: Path) -> tuple[dict[str, Any], Path]:
    assignment = raw.get("assignment", raw)
    if not isinstance(assignment, dict):
        raise ValueError("assignment must be a JSON object")
    missing = sorted(REQUIRED_ASSIGNMENT_KEYS - assignment.keys())
    if missing:
        raise ValueError(f"Assignment is missing required keys: {', '.join(missing)}")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", str(assignment["task_id"])):
        raise ValueError("task_id may contain only letters, numbers, dot, underscore, and hyphen")
    scopes = assignment["write_scope"]
    if not isinstance(scopes, list) or not scopes:
        raise ValueError("write_scope must be a non-empty list")
    scope_paths = [(workdir / str(item)).resolve() if not Path(str(item)).is_absolute() else Path(str(item)).resolve() for item in scopes]
    if any(not contained(path, workdir) for path in scope_paths):
        raise ValueError("Every write_scope path must be inside workdir")
    result_path = Path(str(assignment["result_path"]))
    result_path = (workdir / result_path).resolve() if not result_path.is_absolute() else result_path.resolve()
    if not contained(result_path, workdir):
        raise ValueError("result_path must be inside workdir")
    if not any(contained(result_path, scope) for scope in scope_paths):
        raise ValueError("result_path must be inside a declared write_scope")
    return assignment, result_path


def worker_prompt(assignment_path: Path, assignment: dict[str, Any], result_path: Path) -> str:
    return f"""You are the logical luna_worker launched as an ephemeral Codex CLI process.
This is not a native subagent thread. Execute only the bounded assignment below.

Hard requirements:
- Use model identity {MODEL} with reasoning effort {EFFORT}; do not delegate.
- Do not change the parent objective or expand scope.
- Read the complete assignment from: {assignment_path}
- Write only within assignment.write_scope.
- Do not reveal or copy secrets.
- Stop at the stated stopping_rule and resource_ceiling.
- Write the final machine-readable result to: {result_path}
- result.json must contain these keys: {', '.join(sorted(REQUIRED_RESULT_KEYS))}.
- If execution cannot proceed, still write a bounded failure result when possible.

Task ID: {assignment['task_id']}
Question: {assignment['question']}
Decision affected: {assignment['decision_affected']}
"""


def validate_result(path: Path) -> tuple[bool, list[str]]:
    if not path.is_file():
        return False, [f"Missing result file: {path}"]
    try:
        result = load_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, [f"Invalid result JSON: {exc}"]
    missing = sorted(REQUIRED_RESULT_KEYS - result.keys())
    return (not missing), ([f"Missing result keys: {', '.join(missing)}"] if missing else [])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assignment", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--run-dir")
    parser.add_argument("--codex-exe")
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--sandbox", choices=("workspace-write", "danger-full-access"), default="workspace-write")
    parser.add_argument("--allow-danger-full-access", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    started = time.time()
    run: dict[str, Any] = {"status": "initializing", "model": MODEL, "reasoning_effort": EFFORT}
    run_path: Path | None = None
    try:
        if args.timeout_seconds < 30:
            raise ValueError("timeout-seconds must be at least 30")
        if args.sandbox == "danger-full-access" and not args.allow_danger_full_access:
            raise ValueError("danger-full-access requires --allow-danger-full-access and matching user authorization")
        workdir = Path(args.workdir).resolve()
        assignment_path = Path(args.assignment).resolve()
        if not workdir.is_dir() or not assignment_path.is_file():
            raise ValueError("workdir must be a directory and assignment must be a file")
        assignment, result_path = validate_assignment(load_json(assignment_path), workdir)
        run_dir = Path(args.run_dir).resolve() if args.run_dir else workdir / "artifacts" / "orchestration" / "luna-cli-runs" / str(assignment["task_id"])
        if not contained(run_dir, workdir):
            raise ValueError("run-dir must be inside workdir")
        run_dir.mkdir(parents=True, exist_ok=True)
        run_path = run_dir / "run.json"

        cli, version = select_cli(args.codex_exe)
        catalog = verify_model_catalog(cli)
        run.update({
            "task_id": assignment["task_id"], "status": "validated", "transport": "codex_cli_ephemeral",
            "cli_executable": str(cli), "cli_version": version, "catalog": catalog,
            "assignment_path": str(assignment_path), "result_path": str(result_path),
            "sandbox": args.sandbox, "started_at_unix": started,
        })
        atomic_json(run_path, run)
        if args.dry_run:
            run.update({"status": "planned", "finished_at_unix": time.time()})
            atomic_json(run_path, run)
            print(run_path)
            return 0

        last_message = run_dir / "last-message.txt"
        command = [
            str(cli), "exec", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
            "--model", MODEL,
        ]
        if args.sandbox == "workspace-write":
            command.append("--approve-for-me")
        else:
            command.extend(["--sandbox", args.sandbox])
        command.extend([
            "--cd", str(workdir), "--color", "never",
            "-c", f'model_reasoning_effort="{EFFORT}"',
            "--output-last-message", str(last_message), "-",
        ])
        proc = subprocess.run(
            command, input=worker_prompt(assignment_path, assignment, result_path),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=args.timeout_seconds,
        )
        (run_dir / "stdout.log").write_text(proc.stdout, encoding="utf-8")
        (run_dir / "stderr.log").write_text(proc.stderr, encoding="utf-8")
        runtime_text = proc.stdout + "\n" + proc.stderr
        model_verified = f"model: {MODEL}" in runtime_text
        effort_verified = f"reasoning effort: {EFFORT}" in runtime_text
        if args.sandbox == "workspace-write":
            write_policy_verified = (
                "approval: on-request" in runtime_text
                and ("sandbox: read-only" in runtime_text or "sandbox: workspace-write" in runtime_text)
            )
        else:
            write_policy_verified = f"sandbox: {args.sandbox}" in runtime_text
        result_valid, result_errors = validate_result(result_path)
        accepted = proc.returncode == 0 and model_verified and effort_verified and write_policy_verified and result_valid
        run.update({
            "status": "accepted" if accepted else "failed", "exit_code": proc.returncode,
            "runtime_identity": {
                "model_verified": model_verified,
                "reasoning_effort_verified": effort_verified,
                "write_policy_verified": write_policy_verified,
            },
            "result_contract_valid": result_valid, "errors": result_errors,
            "finished_at_unix": time.time(), "elapsed_seconds": round(time.time() - started, 3),
        })
        atomic_json(run_path, run)
        print(run_path)
        return 0 if accepted else 2
    except subprocess.TimeoutExpired as exc:
        run.update({"status": "failed", "errors": [f"Worker timed out: {exc}"], "finished_at_unix": time.time()})
    except Exception as exc:
        run.update({"status": "failed", "errors": [str(exc)], "finished_at_unix": time.time()})
    if run_path is not None:
        atomic_json(run_path, run)
        print(run_path, file=sys.stderr)
    else:
        print(run["errors"][0], file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
