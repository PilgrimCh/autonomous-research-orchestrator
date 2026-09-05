#!/usr/bin/env python3
"""Run or validate one bounded Luna worker result.

The runner has two execution transports:

* ``codex_cli_ephemeral`` starts one local CLI process; and
* ``native_codex_thread`` accepts a receipt produced by Brain's native thread
  tool. The latter path deliberately never starts a process itself.

Result validation is shared by both transports. Historical results keep their
old interpretation, while assignments validated by this runner require the
version-2 result classification fields.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping


MODEL = "gpt-5.6-luna"
EFFORT = "max"
ROLE = "luna_worker"
RESULT_SCHEMA_VERSION = 2
RECEIPT_SCHEMA_VERSION = 1

REQUIRED_ASSIGNMENT_KEYS = {
    "task_id", "stage_id", "route_id", "project_goal_link", "question",
    "decision_affected", "inputs", "scientific_contract", "allowed_actions",
    "forbidden_actions", "write_scope", "method", "result_path",
    "targeted_sanity_check", "resource_ceiling", "repair_authority", "stopping_rule",
}
REQUIRED_RESULT_KEYS = {
    "experiment_id", "status", "executed", "primary_results", "sanity_check",
    "deviations", "repairs", "debug", "resource_use", "artifacts", "claim_boundary",
    "limitations", "brain_decision_needed",
}
V2_RESULT_KEYS = REQUIRED_RESULT_KEYS | {
    "result_schema_version", "execution_status", "evidence_validity", "scientific_verdict",
}
RESULT_STATUS = {"success", "negative", "mixed", "inconclusive", "invalid", "blocked"}
V2_RESULT_STATUS = RESULT_STATUS | {"completed"}
EXECUTION_STATUS = {"completed", "blocked", "invalid"}
EVIDENCE_VALIDITY = {"valid", "invalid", "not_assessed"}
SCIENTIFIC_VERDICT = {"success", "negative", "mixed", "inconclusive", "not_assessable"}
NON_SCIENTIFIC_WORK_KINDS = {"infrastructure", "documentation", "engineering"}
FAILURE_PHASES = {
    "assignment", "discovery", "catalog", "dispatch", "identity", "execution", "result_contract",
}
REPAIR_ATTEMPT_KINDS = {
    "repair", "repair_attempt", "repair_cycle", "local_repair", "local_repair_cycle",
}
EXPECTED_REPAIR_SEQUENCE = (
    "reproduce", "minimize", "diagnose", "repair", "targeted_validate", "resume_original_experiment",
)


class RunnerError(RuntimeError):
    """An error with enough structure for Brain to choose a safe next action."""

    def __init__(
        self, message: str, *, code: str, failure_phase: str,
        dispatch_started: bool = False, side_effect_uncertain: bool = False,
        safe_next_action: str | None = None, details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        if failure_phase not in FAILURE_PHASES:
            raise ValueError(f"unknown failure phase: {failure_phase}")
        self.code = code
        self.failure_phase = failure_phase
        self.dispatch_started = dispatch_started
        self.side_effect_uncertain = side_effect_uncertain
        self.safe_next_action = safe_next_action or safe_next_action_for(
            failure_phase, dispatch_started, side_effect_uncertain
        )
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "failure_phase": self.failure_phase,
            "message": sanitize_text(str(self)),
            "dispatch_started": self.dispatch_started,
            "side_effect_uncertain": self.side_effect_uncertain,
            "safe_next_action": self.safe_next_action,
            **self.details,
        }


class ValidationReport(tuple):
    """Tuple-compatible ``(valid, errors)`` with separate compatibility warnings."""

    def __new__(
        cls, valid: bool, errors: Iterable[str] = (), warnings: Iterable[str] = (),
    ) -> "ValidationReport":
        obj = super().__new__(cls, (bool(valid), list(errors)))
        obj.warnings = list(warnings)  # type: ignore[attr-defined]
        return obj

    @property
    def valid(self) -> bool:
        return bool(self[0])

    @property
    def errors(self) -> list[str]:
        return list(self[1])

    @property
    def normalization_warnings(self) -> list[str]:
        return list(getattr(self, "warnings", []))


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid JSON: {path}: {exc}") from exc
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
    if not match:
        return (0,)
    return tuple(int(part or 0) for part in match.groups())


def sanitize_text(value: Any, *, max_chars: int = 12000) -> str:
    """Keep bounded diagnostics while removing common credential forms."""

    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = str(value)
    patterns = (
        (r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", r"\1[REDACTED]"),
        (r"(?i)(\btoken\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]"),
        (r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret)\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]"),
        (r"(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|xox[baprs]-[A-Za-z0-9-]{12,})\b", "[REDACTED]"),
        (r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b", "[REDACTED]"),
    )
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[truncated]"
    return text


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
        output = sanitize_text((proc.stdout or "") + (proc.stderr or ""), max_chars=4000).strip()
        if proc.returncode == 0:
            usable.append((parse_version(output), candidate.stat().st_mtime, candidate, output))
    if not usable:
        raise RuntimeError("No usable Codex CLI executable was found")
    _, _, path, version = max(usable, key=lambda item: (item[0], item[1]))
    return path, version


def _mapping_candidates(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _mapping_candidates(child)
    elif isinstance(value, list):
        for child in value:
            yield from _mapping_candidates(child)


def _extract_identity(value: Any) -> dict[str, Any]:
    """Extract only known identity fields from structured evidence."""

    result: dict[str, Any] = {}
    for mapping in _mapping_candidates(value):
        for key in ("model", "model_slug", "configured_model"):
            if key in mapping and isinstance(mapping[key], str) and "model" not in result:
                result["model"] = mapping[key]
        for key in ("reasoning_effort", "effort", "configured_effort"):
            if key in mapping and isinstance(mapping[key], str) and "reasoning_effort" not in result:
                result["reasoning_effort"] = mapping[key]
        for key in ("role", "configured_role", "worker_role"):
            if key in mapping and isinstance(mapping[key], str) and "role" not in result:
                result["role"] = mapping[key]
        for key in ("native_agent_id", "agent_id", "verified_agent_id", "native_thread_id", "thread_id"):
            if key in mapping and isinstance(mapping[key], str) and "agent_id" not in result:
                result["agent_id"] = mapping[key]
    return result


def _load_structured_runtime_evidence(run_dir: Path) -> tuple[dict[str, Any] | None, Path | None]:
    for name in ("runtime-evidence.json", "runtime_evidence.json", "runtime-receipt.json", "runtime_receipt.json", "runtime.json"):
        path = run_dir / name
        if not path.is_file():
            continue
        try:
            return load_json(path), path
        except (OSError, ValueError, json.JSONDecodeError):
            return None, path
    return None, None


def verify_model_catalog(cli: Path, runtime_evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Verify the requested model and effort without silently substituting either."""

    if runtime_evidence is not None:
        identity = _extract_identity(runtime_evidence)
        if "model" in identity or "reasoning_effort" in identity:
            if identity.get("model") != MODEL or identity.get("reasoning_effort") != EFFORT:
                raise RuntimeError(f"Structured runtime evidence does not verify {MODEL}/{EFFORT}")
    proc = subprocess.run(
        [str(cli), "debug", "models"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    stdout = proc.stdout or ""
    output = sanitize_text(stdout + (proc.stderr or ""))
    if proc.returncode != 0 or MODEL not in output:
        raise RuntimeError(f"Selected CLI does not advertise {MODEL}")
    try:
        catalog = json.loads(stdout)
        model = next(item for item in catalog["models"] if item.get("slug") == MODEL)
        efforts = [item.get("effort") for item in model.get("supported_reasoning_levels", [])]
        if EFFORT not in efforts:
            raise RuntimeError(f"Selected CLI does not advertise reasoning effort {EFFORT}")
        return {"model": MODEL, "reasoning_effort": EFFORT, "supported_reasoning_efforts": efforts, "catalog_source": "structured"}
    except (json.JSONDecodeError, KeyError, StopIteration, TypeError):
        if EFFORT not in output:
            raise RuntimeError(f"Selected CLI does not advertise reasoning effort {EFFORT}")
        return {"model": MODEL, "reasoning_effort": EFFORT, "catalog_format": "text-fallback"}


def _assignment_object(raw: Mapping[str, Any]) -> dict[str, Any]:
    assignment = raw.get("assignment", raw)
    if not isinstance(assignment, dict):
        raise ValueError("assignment must be a JSON object")
    return dict(assignment)


def _validate_repair_authority(authority: Any, *, strict: bool = False) -> list[str]:
    errors: list[str] = []
    if not isinstance(authority, dict):
        return ["repair_authority must be an object"]
    for key in ("allowed", "forbidden"):
        if key in authority and (not isinstance(authority[key], list) or not all(isinstance(item, str) for item in authority[key])):
            errors.append(f"repair_authority.{key} must be a list of strings")
    if "debug_cycle_limit" in authority:
        limit = authority["debug_cycle_limit"]
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            errors.append("repair_authority.debug_cycle_limit must be a nonnegative integer")
    elif strict:
        errors.append("repair_authority.debug_cycle_limit is required for result schema v2")
    sequence = authority.get("required_sequence")
    if sequence is not None:
        if not isinstance(sequence, list) or not all(isinstance(item, str) for item in sequence):
            errors.append("repair_authority.required_sequence must be a list of strings")
        else:
            position = -1
            for expected in EXPECTED_REPAIR_SEQUENCE:
                try:
                    position = sequence.index(expected, position + 1)
                except ValueError:
                    errors.append("repair_authority.required_sequence must include " + " -> ".join(EXPECTED_REPAIR_SEQUENCE))
                    break
    # The sequence is optional metadata. If supplied, it is checked above;
    # omission keeps compact assignments compatible while the debug cycle
    # limit remains enforceable when supplied.
    return errors


def validate_assignment(raw: dict[str, Any], workdir: Path) -> tuple[dict[str, Any], Path]:
    """Validate an assignment and mark runner-created assignments as strict v2."""

    assignment = _assignment_object(raw)
    missing = sorted(REQUIRED_ASSIGNMENT_KEYS - assignment.keys())
    if missing:
        raise ValueError(f"Assignment is missing required keys: {', '.join(missing)}")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", str(assignment["task_id"])):
        raise ValueError("task_id may contain only letters, numbers, dot, underscore, and hyphen")
    if assignment.get("result_schema_version", RESULT_SCHEMA_VERSION) != RESULT_SCHEMA_VERSION:
        raise ValueError("new assignments require result_schema_version 2")
    assignment["result_schema_version"] = RESULT_SCHEMA_VERSION
    assignment["result_schema_strict"] = True
    repair_errors = _validate_repair_authority(assignment.get("repair_authority"), strict=True)
    if repair_errors:
        raise ValueError("; ".join(repair_errors))
    scopes = assignment["write_scope"]
    if not isinstance(scopes, list) or not scopes:
        raise ValueError("write_scope must be a non-empty list")
    scope_paths = [
        (workdir / str(item)).resolve() if not Path(str(item)).is_absolute() else Path(str(item)).resolve()
        for item in scopes
    ]
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
- Treat an execution blocker as a debug task, not as negative evidence about the idea.
- For allowed execution failures, follow repair_authority through bounded reproduce, minimize,
  diagnose, repair, targeted-validation, and original-experiment-resume cycles. Do not stop at
  the first failed repair and do not bypass a broken decisive component by removing it.
- Write the final machine-readable result to: {result_path}
- This is a strict result_schema_version 2 assignment. result.json must contain the legacy
  contract keys plus result_schema_version=2, execution_status, evidence_validity, and
  scientific_verdict. Keep these three states consistent: unresolved execution blockers are
  blocked/not_assessed/not_assessable; invalid result contracts are invalid/invalid/not_assessable;
  a completed interpretable result is completed/valid with success, negative, mixed, or
  inconclusive.
- Infrastructure or documentation work that intentionally produces no scientific verdict may
  instead use explicit work_kind=infrastructure or documentation with
  execution_status=completed, evidence_validity=valid, scientific_verdict=not_assessable, and
  status=completed. Scientific work may not use that classification.
- Include assignment task_id and stage_id in the result (top-level or inside executed) when
  available; they must match the assignment.
- Record every encountered execution blocker in result.debug, including attempts, root cause,
  whether it was resolved, and whether the original experiment actually resumed.
- Each debug attempt must have an explicit kind. Use repair_cycle for a local repair cycle and
  diagnostic_read or probe for diagnostics; diagnostic reads do not count as repairs. Include
  local_repair_cycles and completed_repair_cycles when debug is used (both are zero when no
  blocker occurred). Never claim a scientific verdict before the original experiment resumes.
- If execution cannot proceed, still write a bounded blocked or invalid result when possible.

Task ID: {assignment['task_id']}
Question: {assignment['question']}
Decision affected: {assignment['decision_affected']}
"""


def _is_strict_assignment(assignment: Mapping[str, Any] | None) -> bool:
    return bool(assignment and (assignment.get("result_schema_version") == RESULT_SCHEMA_VERSION or assignment.get("result_schema_strict") is True))


def _load_assignment_for_validation(assignment: Any) -> dict[str, Any] | None:
    if assignment is None:
        return None
    if isinstance(assignment, (str, Path)):
        return _assignment_object(load_json(Path(assignment)))
    if isinstance(assignment, Mapping):
        return _assignment_object(assignment)
    raise ValueError("assignment must be a path or JSON object")


def _attempt_kind(attempt: Any) -> str | None:
    if not isinstance(attempt, Mapping):
        return None
    for key in ("kind", "attempt_kind", "attempt_type", "type"):
        value = attempt.get(key)
        if isinstance(value, str) and value:
            return value.strip().lower()
    return None


def _work_kind(value: Mapping[str, Any] | None) -> str | None:
    if not isinstance(value, Mapping):
        return None
    raw = value.get("work_kind")
    if raw is None:
        return None
    return raw.strip().lower() if isinstance(raw, str) else "__invalid__"


def _validate_result_assignment_identity(
    result: Mapping[str, Any], assignment: Mapping[str, Any] | None
) -> list[str]:
    """Reject a result carrying another task's identifiers."""

    if not assignment:
        return []
    errors: list[str] = []
    executed = result.get("executed")
    executed_map = executed if isinstance(executed, Mapping) else {}
    for key in ("task_id", "stage_id"):
        expected = assignment.get(key)
        if expected is None:
            continue
        supplied = []
        if key in result:
            supplied.append(("result", result.get(key)))
        if key in executed_map:
            supplied.append(("executed", executed_map.get(key)))
        if not supplied:
            continue
        if any(value != expected for _, value in supplied):
            observed = ", ".join(f"{source}={value!r}" for source, value in supplied)
            errors.append(
                f"result_identity_mismatch:{key}: expected assignment {expected!r}; observed {observed}"
            )
        if len({value for _, value in supplied}) > 1:
            errors.append(
                f"result_identity_mismatch:{key}: top-level and executed identifiers disagree"
            )
    return errors


def _debug_cycle_errors(debug: Mapping[str, Any], assignment: Mapping[str, Any] | None, *, strict: bool) -> list[str]:
    errors: list[str] = []
    attempts = debug.get("attempts", [])
    if not isinstance(attempts, list):
        return errors
    repair_count = 0
    for index, attempt in enumerate(attempts):
        kind = _attempt_kind(attempt)
        if strict and not kind:
            errors.append(f"contract_deviation:debug_attempt_kind_missing: attempts[{index}] needs explicit kind")
        if kind in REPAIR_ATTEMPT_KINDS:
            repair_count += 1
    declared = debug.get("local_repair_cycles")
    completed = debug.get("completed_repair_cycles", debug.get("completed_cycles"))
    if declared is not None and (not isinstance(declared, int) or isinstance(declared, bool) or declared < 0):
        errors.append("contract_deviation:debug_cycle_count_invalid: local_repair_cycles must be nonnegative integer")
    if completed is not None and (not isinstance(completed, int) or isinstance(completed, bool) or completed < 0):
        errors.append("contract_deviation:debug_cycle_count_invalid: completed_repair_cycles must be nonnegative integer")
    if isinstance(declared, int) and isinstance(completed, int):
        if declared != completed:
            errors.append("contract_deviation:debug_cycle_count_mismatch: " + f"local_repair_cycles={declared} differs from completed_repair_cycles={completed}; reconcile before acceptance")
        if declared != repair_count:
            errors.append("contract_deviation:debug_cycle_count_mismatch: " + f"declared local_repair_cycles={declared} differs from explicit repair_cycle attempts={repair_count}; reconcile before acceptance")
    elif strict and attempts:
        errors.append("contract_deviation:debug_cycle_count_missing: strict debug attempts require local_repair_cycles and completed_repair_cycles")
    limit = None
    if assignment and isinstance(assignment.get("repair_authority"), Mapping):
        raw_limit = assignment["repair_authority"].get("debug_cycle_limit")
        if isinstance(raw_limit, int) and not isinstance(raw_limit, bool):
            limit = raw_limit
    if limit is not None:
        actual = declared if isinstance(declared, int) else repair_count
        if actual > limit:
            errors.append("contract_deviation:debug_cycle_limit_exceeded: " + f"local_repair_cycles={actual} exceeds debug_cycle_limit={limit}; reconcile before acceptance")
    return errors


def _validate_debug(debug: Any, status: str, assignment: Mapping[str, Any] | None, *, strict: bool) -> list[str]:
    errors: list[str] = []
    if not isinstance(debug, dict):
        return ["debug must be an object"]
    encountered = debug.get("encountered")
    attempts = debug.get("attempts")
    if not isinstance(encountered, bool):
        errors.append("debug.encountered must be boolean")
    if not isinstance(attempts, list):
        errors.append("debug.attempts must be a list")
        attempts = []
    if encountered is False and attempts:
        errors.append("debug.attempts must be empty when no blocker was encountered")
    if encountered is True:
        if not attempts:
            errors.append("an encountered blocker requires at least one debug attempt")
        for key in ("failure_class", "root_cause"):
            if not isinstance(debug.get(key), str) or not debug.get(key):
                errors.append(f"an encountered blocker requires debug.{key}")
        for key in ("resolved", "original_experiment_resumed"):
            if not isinstance(debug.get(key), bool):
                errors.append(f"an encountered blocker requires boolean debug.{key}")
        if status in {"success", "negative", "mixed", "inconclusive"} and (debug.get("resolved") is not True or debug.get("original_experiment_resumed") is not True):
            errors.append("a scientific result is invalid until the blocker is resolved and the original experiment resumes")
    errors.extend(_debug_cycle_errors(debug, assignment, strict=strict))
    return errors


def _validate_legacy_result(result: dict[str, Any], assignment: Mapping[str, Any] | None) -> ValidationReport:
    missing = sorted(REQUIRED_RESULT_KEYS - result.keys())
    errors = [f"Missing result keys: {', '.join(missing)}"] if missing else []
    status = result.get("status")
    if status not in RESULT_STATUS:
        errors.append("Invalid result status")
    errors.extend(_validate_debug(result.get("debug"), status if isinstance(status, str) else "invalid", assignment, strict=False))
    warnings = ["legacy_normalization: historical result has no result_schema_version; its status remains historical and is not silently upgraded to a v2 scientific verdict"]
    return ValidationReport(not errors, errors, warnings)


def _validate_v2_result(result: dict[str, Any], assignment: Mapping[str, Any] | None) -> ValidationReport:
    missing = sorted(V2_RESULT_KEYS - result.keys())
    errors = [f"Missing result keys: {', '.join(missing)}"] if missing else []
    if result.get("result_schema_version") != RESULT_SCHEMA_VERSION:
        errors.append("result_schema_version must be 2 for strict validation")
    status = result.get("status")
    execution_status = result.get("execution_status")
    evidence_validity = result.get("evidence_validity")
    scientific_verdict = result.get("scientific_verdict")
    if status not in V2_RESULT_STATUS:
        errors.append("Invalid result status")
    if execution_status not in EXECUTION_STATUS:
        errors.append("execution_status must be completed, blocked, or invalid")
    if evidence_validity not in EVIDENCE_VALIDITY:
        errors.append("evidence_validity must be valid, invalid, or not_assessed")
    if scientific_verdict not in SCIENTIFIC_VERDICT:
        errors.append("scientific_verdict must be success, negative, mixed, inconclusive, or not_assessable")
    errors.extend(_validate_debug(result.get("debug"), status if isinstance(status, str) else "invalid", assignment, strict=True))
    errors.extend(_validate_result_assignment_identity(result, assignment))
    assignment_work_kind = _work_kind(assignment)
    result_work_kind = _work_kind(result)
    if assignment_work_kind == "__invalid__":
        errors.append("assignment.work_kind must be a string when supplied")
    if result_work_kind == "__invalid__":
        errors.append("result.work_kind must be a string when supplied")
    if assignment_work_kind not in {None, "__invalid__"} and result_work_kind not in {None, "__invalid__"} and assignment_work_kind != result_work_kind:
        errors.append(
            f"work_kind_mismatch: assignment={assignment_work_kind!r} result={result_work_kind!r}"
        )
    effective_work_kind = result_work_kind if result_work_kind not in {None, "__invalid__"} else assignment_work_kind
    non_scientific_completion = (
        execution_status == "completed"
        and evidence_validity == "valid"
        and scientific_verdict == "not_assessable"
    )
    if non_scientific_completion:
        if effective_work_kind not in NON_SCIENTIFIC_WORK_KINDS:
            errors.append(
                "completed/valid/not_assessable requires explicit work_kind=infrastructure or documentation"
            )
        if status != "completed":
            errors.append("non-scientific completed work must use status=completed")
    elif status == "completed":
        errors.append(
            "status=completed is reserved for explicit infrastructure or documentation work without a scientific verdict"
        )
    classification = result.get("classification")
    if classification is not None:
        if not isinstance(classification, Mapping):
            errors.append("classification must be an object when supplied")
        else:
            for key, expected in (("execution_status", execution_status), ("evidence_validity", evidence_validity), ("scientific_verdict", scientific_verdict)):
                if key in classification and classification.get(key) != expected:
                    errors.append(f"classification.{key} contradicts top-level {key}")
            if "work_kind" in classification and classification.get("work_kind") != effective_work_kind:
                errors.append("classification.work_kind contradicts assignment/result work_kind")
    debug = result.get("debug")
    unresolved_blocker = isinstance(debug, Mapping) and debug.get("encountered") is True and (debug.get("resolved") is not True or debug.get("original_experiment_resumed") is not True)
    if execution_status == "blocked":
        if evidence_validity != "not_assessed" or scientific_verdict != "not_assessable" or status != "blocked":
            errors.append("classification contradiction: blocked execution requires blocked/not_assessed/not_assessable")
        if not unresolved_blocker:
            errors.append("blocked execution requires an unresolved debug blocker")
    if execution_status == "invalid":
        if evidence_validity != "invalid" or scientific_verdict != "not_assessable" or status != "invalid":
            errors.append("classification contradiction: invalid execution requires invalid/invalid/not_assessable")
    if execution_status == "completed":
        if unresolved_blocker:
            errors.append("completed execution cannot contain an unresolved blocker")
        if evidence_validity == "valid":
            if scientific_verdict not in {"success", "negative", "mixed", "inconclusive", "not_assessable"}:
                errors.append("valid evidence requires an interpretable scientific verdict")
            elif scientific_verdict != "not_assessable" and status != scientific_verdict:
                errors.append("status must equal scientific_verdict for valid v2 evidence")
            sanity = result.get("sanity_check")
            if isinstance(sanity, Mapping) and sanity.get("status") == "fail":
                errors.append("valid evidence cannot have a failed targeted sanity check")
        elif scientific_verdict != "not_assessable" or status not in {"invalid"}:
            errors.append("completed evidence that is invalid or not assessed must be not_assessable with invalid or blocked status")
    if evidence_validity == "invalid" and scientific_verdict != "not_assessable":
        errors.append("invalid evidence cannot claim a scientific verdict")
    if evidence_validity == "not_assessed" and scientific_verdict != "not_assessable":
        errors.append("not_assessed evidence cannot claim a scientific verdict")
    if scientific_verdict == "not_assessable" and status not in {"invalid", "blocked", "completed"}:
        errors.append("not_assessable scientific verdict requires invalid or blocked status")
    return ValidationReport(not errors, errors)


def validate_result(path: Path | str, assignment: Any = None) -> ValidationReport:
    """Validate strict v2 or explicit historical result interpretation."""

    result_path = Path(path)
    if not result_path.is_file():
        return ValidationReport(False, [f"Missing result file: {result_path}"])
    try:
        result = load_json(result_path)
        assignment_object = _load_assignment_for_validation(assignment)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return ValidationReport(False, [f"Invalid result JSON: {exc}"])
    strict = result.get("result_schema_version") == RESULT_SCHEMA_VERSION or _is_strict_assignment(assignment_object)
    if strict:
        report = _validate_v2_result(result, assignment_object)
    else:
        report = _validate_legacy_result(result, assignment_object)
    if assignment_object and "repair_authority" in assignment_object:
        assignment_errors = _validate_repair_authority(assignment_object["repair_authority"], strict=strict)
        if assignment_errors:
            return ValidationReport(False, assignment_errors + report.errors, getattr(report, "warnings", []))
    return report


def validate_native_receipt(path_or_value: Path | str | Mapping[str, Any], assignment: Any = None) -> ValidationReport:
    """Validate Brain's attested native-worker receipt, never a cryptographic proof."""

    try:
        receipt = load_json(Path(path_or_value)) if isinstance(path_or_value, (str, Path)) else dict(path_or_value)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return ValidationReport(False, [f"Invalid native runtime receipt: {exc}"])
    errors: list[str] = []
    if receipt.get("receipt_schema_version", RECEIPT_SCHEMA_VERSION) != RECEIPT_SCHEMA_VERSION:
        errors.append("native receipt receipt_schema_version must be 1")
    if receipt.get("transport") not in {"native_codex_thread", "native", "native_codex"}:
        errors.append("native receipt transport must be native_codex_thread")
    agent_id = receipt.get("native_agent_id") or receipt.get("agent_id") or receipt.get("native_thread_id")
    if not isinstance(agent_id, str) or not agent_id.strip():
        errors.append("native receipt requires native_agent_id")
    configured = receipt.get("configured") if isinstance(receipt.get("configured"), Mapping) else {}
    configured_role = receipt.get("configured_role", receipt.get("role", configured.get("role")))
    configured_model = receipt.get("model", receipt.get("configured_model", configured.get("model")))
    configured_effort = receipt.get("reasoning_effort", receipt.get("configured_effort", receipt.get("effort", configured.get("reasoning_effort", configured.get("effort")))))
    if configured_role != ROLE:
        errors.append(f"native receipt configured_role must be {ROLE}")
    if configured_model != MODEL:
        errors.append(f"native receipt model must be {MODEL}")
    if configured_effort != EFFORT:
        errors.append(f"native receipt reasoning_effort must be {EFFORT}")
    if receipt.get("cryptographic_proof") is True or receipt.get("proof_type") in {"cryptographic", "cryptographic_proof"}:
        errors.append("native receipt must not claim cryptographic proof")
    evidence = receipt.get("platform_evidence")
    if not isinstance(evidence, Mapping):
        errors.append("native receipt requires independent platform_evidence; a self-reported worker name is insufficient")
    else:
        provenance = evidence.get("provenance")
        source = evidence.get("source") or (provenance.get("source") if isinstance(provenance, Mapping) else provenance)
        verified_by = evidence.get("verified_by") or evidence.get("attested_by") or evidence.get("verifier")
        if not isinstance(source, str) or not source or source in {"worker", "result", "self_report"}:
            errors.append("platform_evidence.source must identify an independent native platform tool")
        if evidence.get("verified") is not True:
            errors.append("platform_evidence.verified must be true")
        if not isinstance(verified_by, str) or not verified_by:
            errors.append("platform_evidence.verified_by must identify the trusted Brain caller")
        evidence_identity = _extract_identity(evidence)
        if evidence_identity.get("agent_id") != agent_id:
            errors.append("platform_evidence agent id does not match native_agent_id")
        if evidence_identity.get("role") != ROLE:
            errors.append("platform_evidence role does not verify luna_worker")
        if evidence_identity.get("model") != MODEL:
            errors.append("platform_evidence model does not verify gpt-5.6-luna")
        if evidence_identity.get("reasoning_effort") != EFFORT:
            errors.append("platform_evidence effort does not verify max")
    assignment_object = _load_assignment_for_validation(assignment) if assignment is not None else None
    if assignment_object:
        for key, expected in (("worker_role", ROLE), ("model", MODEL), ("reasoning_effort", EFFORT)):
            if key in assignment_object and assignment_object[key] != expected:
                errors.append(f"assignment {key} does not match configured native worker")
    return ValidationReport(not errors, errors)


validate_runtime_receipt = validate_native_receipt


def safe_next_action_for(failure_phase: str, dispatch_started: bool, side_effect_uncertain: bool) -> str:
    if side_effect_uncertain:
        return "Preserve the run and inspect the recorded partial output/process state; do not replay the assignment or fall back to another transport until Brain reconciles dispatch."
    if failure_phase == "assignment":
        return "Repair the assignment contract and validate it again before dispatch."
    if failure_phase == "discovery":
        return "No worker dispatch occurred. Brain may choose another already-authorized transport only after verifying its capability; do not treat this as scientific evidence."
    if failure_phase == "catalog":
        return "No worker dispatch occurred. Recheck the configured CLI/catalog; an alternate transport requires separate verified capability and authorization."
    if failure_phase == "dispatch":
        return "Inspect the dispatch error and preserve the unresolved task; do not automatically replay it."
    if failure_phase == "identity":
        return "Preserve the result and reconcile runtime identity; never weaken model or effort verification."
    if failure_phase == "result_contract":
        return "Reconcile the result contract in place; preserve scientific artifacts and do not auto-rerun them."
    return "Inspect the bounded execution failure and preserve the unresolved task for Brain."


def _looks_like_policy_denial(value: Any) -> bool:
    text = str(value).lower()
    return any(token in text for token in ("policy", "permission denied", "not authorized", "unauthorized", "approval denied", "forbidden"))


def _failure_from_validation(errors: list[str], *, dispatch_started: bool = False) -> RunnerError:
    code = "contract_deviation" if any(error.startswith("contract_deviation:") for error in errors) else "result_contract_invalid"
    return RunnerError("; ".join(errors) if errors else "result contract invalid", code=code, failure_phase="result_contract", dispatch_started=dispatch_started, details={"validation_errors": errors})


def _runtime_identity(runtime_text: str, structured: Mapping[str, Any] | None) -> dict[str, Any]:
    if structured is not None:
        identity = _extract_identity(structured)
        return {"source": "structured_runtime_evidence", "model_verified": identity.get("model") == MODEL, "reasoning_effort_verified": identity.get("reasoning_effort") == EFFORT, "role_verified": identity.get("role") in {None, ROLE}, "model": identity.get("model"), "reasoning_effort": identity.get("reasoning_effort")}
    return {
        "source": "sanitized_runtime_text",
        "model_verified": bool(re.search(rf"(?i)\bmodel\s*[:=]\s*{re.escape(MODEL)}\b", runtime_text)),
        "reasoning_effort_verified": bool(re.search(rf"(?i)\b(?:reasoning\s+effort|effort)\s*[:=]\s*{re.escape(EFFORT)}\b", runtime_text)),
        "role_verified": True,
    }


def _write_process_output(run_dir: Path, stdout: Any, stderr: Any) -> tuple[str, str]:
    safe_stdout = sanitize_text(stdout)
    safe_stderr = sanitize_text(stderr)
    (run_dir / "stdout.log").write_text(safe_stdout, encoding="utf-8")
    (run_dir / "stderr.log").write_text(safe_stderr, encoding="utf-8")
    return safe_stdout, safe_stderr


def _validate_inputs_for_mode(args: argparse.Namespace) -> tuple[dict[str, Any], Path, Path]:
    if not args.assignment:
        raise RunnerError("--assignment is required", code="assignment_missing", failure_phase="assignment")
    assignment_path = Path(args.assignment).resolve()
    if not assignment_path.is_file():
        raise RunnerError(f"assignment is not a file: {assignment_path}", code="assignment_missing", failure_phase="assignment")
    try:
        raw_assignment = load_json(assignment_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RunnerError(str(exc), code="assignment_invalid", failure_phase="assignment") from exc
    workdir = Path(args.workdir).resolve() if args.workdir else assignment_path.parent.resolve()
    if not workdir.is_dir():
        raise RunnerError(f"workdir must be a directory: {workdir}", code="assignment_invalid", failure_phase="assignment")
    try:
        assignment, result_path_from_assignment = validate_assignment(raw_assignment, workdir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RunnerError(str(exc), code="assignment_invalid", failure_phase="assignment") from exc
    result_path = Path(args.result_path).resolve() if args.result_path else result_path_from_assignment
    if not contained(result_path, workdir):
        raise RunnerError("result path must be inside workdir", code="assignment_invalid", failure_phase="assignment")
    scope_paths = [
        (workdir / str(item)).resolve() if not Path(str(item)).is_absolute() else Path(str(item)).resolve()
        for item in assignment["write_scope"]
    ]
    if not any(contained(result_path, scope) for scope in scope_paths):
        raise RunnerError(
            "result path must be inside a declared write_scope",
            code="assignment_invalid",
            failure_phase="assignment",
        )
    return assignment, assignment_path, result_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assignment")
    parser.add_argument("--workdir")
    parser.add_argument("--result", "--result-path", dest="result_path")
    parser.add_argument("--runtime-receipt", "--native-receipt", dest="runtime_receipt")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--validate-only", action="store_true")
    modes.add_argument("--accept-native", action="store_true")
    parser.add_argument("--run-dir")
    parser.add_argument("--codex-exe")
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--sandbox", choices=("workspace-write", "danger-full-access"), default="workspace-write")
    parser.add_argument("--allow-danger-full-access", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _run_validation_mode(args: argparse.Namespace) -> int:
    started = time.time()
    run_dir: Path | None = None
    run: dict[str, Any] = {"run_schema_version": 2, "status": "initializing", "transport": "native_codex_thread" if args.accept_native else "validation_only", "model": MODEL, "reasoning_effort": EFFORT, "phase": "assignment", "failure_phase": None, "dispatch_started": False, "side_effect_uncertain": False, "started_at_unix": started}
    try:
        assignment, assignment_path, result_path = _validate_inputs_for_mode(args)
        workdir = Path(args.workdir).resolve() if args.workdir else assignment_path.parent.resolve()
        run_dir = (
            Path(args.run_dir).resolve()
            if args.run_dir
            else workdir / "artifacts" / "orchestration" / "luna-validation-runs" / str(assignment["task_id"])
        )
        if not contained(run_dir, workdir):
            raise RunnerError(
                "run-dir must be inside workdir",
                code="assignment_invalid",
                failure_phase="assignment",
            )
        run_dir.mkdir(parents=True, exist_ok=True)
        run.update({"task_id": assignment["task_id"], "assignment_path": str(assignment_path), "result_path": str(result_path)})
        if args.accept_native:
            if not args.runtime_receipt:
                raise RunnerError("--accept-native requires --runtime-receipt", code="native_receipt_missing", failure_phase="identity")
            receipt_path = Path(args.runtime_receipt).resolve()
            receipt_report = validate_native_receipt(receipt_path, assignment)
            if not receipt_report.valid:
                raise RunnerError("; ".join(receipt_report.errors), code="native_receipt_invalid", failure_phase="identity", details={"validation_errors": receipt_report.errors})
            receipt = load_json(receipt_path)
            run.update({"native_receipt_path": str(receipt_path), "native_agent_id": receipt.get("native_agent_id") or receipt.get("agent_id") or receipt.get("native_thread_id"), "runtime_identity": {"source": "brain_native_platform_evidence", "model_verified": True, "reasoning_effort_verified": True, "role_verified": True, "cryptographic_proof": False}})
        elif args.runtime_receipt:
            receipt_path = Path(args.runtime_receipt).resolve()
            receipt_report = validate_native_receipt(receipt_path, assignment)
            if not receipt_report.valid:
                raise RunnerError("; ".join(receipt_report.errors), code="native_receipt_invalid", failure_phase="identity", details={"validation_errors": receipt_report.errors})
            run["native_receipt_path"] = str(receipt_path)
        report = validate_result(result_path, assignment)
        if not report.valid:
            raise _failure_from_validation(report.errors)
        run.update({"status": "accepted", "phase": "result_contract", "result_contract_valid": True, "validation_warnings": getattr(report, "warnings", []), "finished_at_unix": time.time(), "elapsed_seconds": round(time.time() - started, 3)})
        atomic_json(run_dir / "run.json", run)
        print(run_dir / "run.json")
        return 0
    except RunnerError as exc:
        run.update({"status": "failed", "phase": exc.failure_phase, "failure_phase": exc.failure_phase, "error": exc.as_dict(), "dispatch_started": exc.dispatch_started, "side_effect_uncertain": exc.side_effect_uncertain, "safe_next_action": exc.safe_next_action, "finished_at_unix": time.time()})
    except Exception as exc:
        failure = RunnerError(sanitize_text(exc), code="validation_internal_error", failure_phase="result_contract")
        run.update({"status": "failed", "phase": failure.failure_phase, "failure_phase": failure.failure_phase, "error": failure.as_dict(), "finished_at_unix": time.time()})
    if run_dir is not None:
        atomic_json(run_dir / "run.json", run)
        print(run_dir / "run.json", file=sys.stderr)
    else:
        print(run.get("error", {}).get("message", "validation failed"), file=sys.stderr)
    return 2


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    if args.validate_only or args.accept_native:
        if args.dry_run:
            parser.error("--dry-run cannot be combined with --validate-only or --accept-native")
        return _run_validation_mode(args)
    started = time.time()
    run: dict[str, Any] = {"run_schema_version": 2, "status": "initializing", "model": MODEL, "reasoning_effort": EFFORT, "transport": "codex_cli_ephemeral", "phase": "assignment", "failure_phase": None, "dispatch_started": False, "side_effect_uncertain": False, "started_at_unix": started}
    run_path: Path | None = None
    run_dir: Path | None = None
    assignment: dict[str, Any] | None = None
    result_path: Path | None = None
    try:
        if args.timeout_seconds < 30:
            raise RunnerError("timeout-seconds must be at least 30", code="assignment_invalid", failure_phase="assignment")
        if args.sandbox == "danger-full-access" and not args.allow_danger_full_access:
            raise RunnerError("danger-full-access requires --allow-danger-full-access and matching user authorization", code="policy_denied", failure_phase="assignment")
        if not args.assignment or not args.workdir:
            raise RunnerError("normal CLI execution requires --assignment and --workdir", code="assignment_missing", failure_phase="assignment")
        workdir = Path(args.workdir).resolve()
        assignment_path = Path(args.assignment).resolve()
        if not workdir.is_dir() or not assignment_path.is_file():
            raise RunnerError("workdir must be a directory and assignment must be a file", code="assignment_invalid", failure_phase="assignment")
        try:
            assignment, result_path = validate_assignment(load_json(assignment_path), workdir)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RunnerError(str(exc), code="assignment_invalid", failure_phase="assignment") from exc
        run_dir = Path(args.run_dir).resolve() if args.run_dir else workdir / "artifacts" / "orchestration" / "luna-cli-runs" / str(assignment["task_id"])
        if not contained(run_dir, workdir):
            raise RunnerError("run-dir must be inside workdir", code="assignment_invalid", failure_phase="assignment")
        run_dir.mkdir(parents=True, exist_ok=True)
        run_path = run_dir / "run.json"
        run.update({"task_id": assignment["task_id"], "assignment_path": str(assignment_path), "result_path": str(result_path), "sandbox": args.sandbox})
        run["phase"] = "discovery"
        try:
            cli, version = select_cli(args.codex_exe)
        except Exception as exc:
            raise RunnerError(sanitize_text(exc), code="cli_discovery_failed", failure_phase="discovery", details={"alternate_transport_suggestion": True}) from exc
        run.update({"cli_executable": str(cli), "cli_version": version})
        run["phase"] = "catalog"
        structured_evidence, structured_path = _load_structured_runtime_evidence(run_dir)
        if structured_path is not None and structured_evidence is None:
            raise RunnerError(
                "Structured runtime evidence is invalid",
                code="runtime_evidence_invalid",
                failure_phase="identity",
                details={"structured_runtime_evidence": str(structured_path)},
            )
        try:
            catalog = verify_model_catalog(cli, structured_evidence)
        except Exception as exc:
            raise RunnerError(
                sanitize_text(exc),
                code="policy_denied" if _looks_like_policy_denial(exc) else "model_catalog_failed",
                failure_phase="catalog",
                safe_next_action=(
                    "Do not bypass a policy denial; preserve the unresolved task and report the exact policy boundary."
                    if _looks_like_policy_denial(exc)
                    else None
                ),
                details={
                    "structured_runtime_evidence": str(structured_path) if structured_path else None,
                    "alternate_transport_suggestion": not _looks_like_policy_denial(exc),
                },
            ) from exc
        run.update({"catalog": catalog, "structured_runtime_evidence": str(structured_path) if structured_path else None})
        atomic_json(run_path, {**run, "status": "validated"})
        if args.dry_run:
            run.update({"status": "planned", "phase": "dispatch", "finished_at_unix": time.time(), "elapsed_seconds": round(time.time() - started, 3)})
            atomic_json(run_path, run)
            print(run_path)
            return 0
        last_message = run_dir / "last-message.txt"
        command = [str(cli), "exec", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check", "--model", MODEL]
        if args.sandbox == "workspace-write":
            command.append("--approve-for-me")
        else:
            command.extend(["--sandbox", args.sandbox])
        command.extend(["--cd", str(workdir), "--color", "never", "-c", f'model_reasoning_effort="{EFFORT}"', "--output-last-message", str(last_message), "-"])
        run["phase"] = "dispatch"
        run["dispatch_started"] = True
        atomic_json(run_path, run)
        try:
            proc = subprocess.run(command, input=worker_prompt(assignment_path, assignment, result_path), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=args.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            partial_stdout, partial_stderr = _write_process_output(run_dir, exc.stdout, exc.stderr)
            raise RunnerError(f"Worker timed out after {args.timeout_seconds} seconds", code="worker_timeout", failure_phase="execution", dispatch_started=True, side_effect_uncertain=True, details={"partial_stdout": partial_stdout, "partial_stderr": partial_stderr, "no_automatic_replay": True}) from exc
        except OSError as exc:
            raise RunnerError(sanitize_text(exc), code="dispatch_failed", failure_phase="dispatch", dispatch_started=True, side_effect_uncertain=True, details={"no_automatic_replay": True}) from exc
        stdout, stderr = _write_process_output(run_dir, proc.stdout, proc.stderr)
        runtime_text = stdout + "\n" + stderr
        structured_after, structured_after_path = _load_structured_runtime_evidence(run_dir)
        if structured_after is None:
            structured_after, structured_after_path = structured_evidence, structured_path
        if structured_after_path is not None and structured_after is None:
            structured_after = {}
        run["phase"] = "identity"
        identity = _runtime_identity(runtime_text, structured_after)
        if not identity["model_verified"] or not identity["reasoning_effort_verified"] or not identity["role_verified"]:
            raise RunnerError("CLI runtime identity did not verify configured role/model/effort", code="runtime_identity_failed", failure_phase="identity", dispatch_started=True, details={"runtime_identity": identity, "structured_runtime_evidence": str(structured_after_path) if structured_after_path else None})
        run["runtime_identity"] = identity
        run["phase"] = "result_contract"
        report = validate_result(result_path, assignment)
        if not report.valid:
            raise _failure_from_validation(report.errors, dispatch_started=True)
        if proc.returncode != 0:
            raise RunnerError(f"Worker exited with code {proc.returncode}", code="worker_exit_nonzero", failure_phase="execution", dispatch_started=True, details={"exit_code": proc.returncode})
        run.update({"status": "accepted", "exit_code": proc.returncode, "result_contract_valid": True, "validation_warnings": getattr(report, "warnings", []), "finished_at_unix": time.time(), "elapsed_seconds": round(time.time() - started, 3)})
        atomic_json(run_path, run)
        print(run_path)
        return 0
    except RunnerError as exc:
        run.update({"status": "failed", "phase": exc.failure_phase, "failure_phase": exc.failure_phase, "error": exc.as_dict(), "dispatch_started": exc.dispatch_started or run.get("dispatch_started", False), "side_effect_uncertain": exc.side_effect_uncertain, "safe_next_action": exc.safe_next_action, "finished_at_unix": time.time(), "elapsed_seconds": round(time.time() - started, 3)})
    except Exception as exc:
        failure = RunnerError(sanitize_text(exc), code="runner_internal_error", failure_phase="execution" if run.get("dispatch_started") else "assignment", dispatch_started=bool(run.get("dispatch_started")), side_effect_uncertain=bool(run.get("dispatch_started")))
        run.update({"status": "failed", "phase": failure.failure_phase, "failure_phase": failure.failure_phase, "error": failure.as_dict(), "finished_at_unix": time.time()})
    if run_path is not None:
        atomic_json(run_path, run)
        print(run_path, file=sys.stderr)
    else:
        print(run.get("error", {}).get("message", "runner failed"), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
