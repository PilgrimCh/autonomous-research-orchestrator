from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterator


BUDGET_KEYS = (
    "wall_minutes",
    "worker_assignments",
    "external_calls",
    "external_cost_usd",
)
CONTEXT_HEALTH = {"healthy", "rollover_recommended", "rollover_required"}
CONTEXT_HEALTH_RANK = {
    "healthy": 0,
    "rollover_recommended": 1,
    "rollover_required": 2,
}
LINEAGE_STATUS = {"active", "retired"}
CONTROL_MODES = {
    "RUNNING",
    "PAUSED_USER",
    "PAUSED_PLATFORM",
    "PAUSED_REVIEW",
    "TERMINAL",
}
PAUSED_CONTROL_MODES = {"PAUSED_USER", "PAUSED_PLATFORM", "PAUSED_REVIEW"}
DEFAULT_ROLLOVER_THRESHOLD = 2
KNOWN_NATIVE_LUNA_AMENDMENT = "user_native_luna_transport_override_20260828"
NATIVE_LUNA_TRANSPORTS = {
    "native",
    "native_luna",
    "native_luna_subagent",
    "codex_native_luna_worker",
    "codex_native_subagent",
}
CLI_LUNA_TRANSPORTS = {
    "cli",
    "codex_cli",
    "codex_cli_ephemeral",
    "luna_cli",
}
LEGACY_USER_PAUSE_MARKERS = {
    "STOPPED_BY_USER",
    "AWAIT_USER_RESUME",
    "AWAITING_USER_RESUME",
    "PAUSED_USER",
}


class RuntimeErrorWithCode(RuntimeError):
    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeErrorWithCode(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeErrorWithCode(f"expected object in {path}")
    return value


def state_path_for(root: Path) -> Path:
    adapter = load_object(root / "research_pipeline.yaml")
    raw = adapter.get("artifact_system", {}).get("pipeline_state")
    if not isinstance(raw, str) or not raw:
        raise RuntimeErrorWithCode("adapter lacks artifact_system.pipeline_state")
    candidate = Path(raw)
    path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RuntimeErrorWithCode(f"pipeline state escapes research root: {path}") from exc
    return path


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


@contextmanager
def state_lock(path: Path) -> Iterator[None]:
    """Take a small cross-process lock beside the state file."""
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def resolve_root(root_raw: str) -> Path:
    root = Path(root_raw).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeErrorWithCode(f"research root is not a directory: {root}")
    return root


def _marker_is_user_pause(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    marker = value.strip().upper()
    if marker in LEGACY_USER_PAUSE_MARKERS:
        return True
    # Existing state uses concrete markers such as
    # await_explicit_user_resume_after_task188.  Require all three semantic
    # parts; a random prose mention of resume is not a pause signal.
    return "AWAIT" in marker and "USER" in marker and "RESUME" in marker


def infer_control_mode(state: dict[str, Any]) -> str:
    """Infer only the narrow legacy user-pause form when no typed mode exists."""
    session = state.get("autonomy_session")
    if isinstance(session, dict):
        explicit = session.get("control_mode")
        if isinstance(explicit, str) and explicit in CONTROL_MODES:
            return explicit
        if session.get("goal_status") in {"complete", "infeasible"}:
            return "TERMINAL"
        if session.get("enabled") is False:
            return "PAUSED_USER"
    research = state.get("research_runtime")
    if isinstance(research, dict):
        if research.get("state") in {"PROJECT_CLOSED", "TERMINAL"}:
            return "TERMINAL"
        candidates = (
            research.get("state"),
            research.get("next_action"),
            research.get("pause_marker"),
            research.get("stop_reason"),
            research.get("status"),
            state.get("state"),
        )
        if any(_marker_is_user_pause(value) for value in candidates):
            return "PAUSED_USER"
    return "RUNNING"


def current_control_mode(state: dict[str, Any]) -> str:
    session = state.get("autonomy_session")
    if not isinstance(session, dict):
        return "RUNNING"
    raw = session.get("control_mode")
    if session.get("user_pause_latch") and raw != "TERMINAL":
        return "PAUSED_USER"
    if raw is None:
        return infer_control_mode(state)
    if raw not in CONTROL_MODES:
        raise RuntimeErrorWithCode(
            "autonomy_session.control_mode must be one of "
            + ", ".join(sorted(CONTROL_MODES))
        )
    return raw


def rollover_threshold(brain: dict[str, Any]) -> int:
    # Only the explicit current-Brain field is authoritative.  Historical
    # amendments such as rollover_after_observable_compactions must not become
    # a process-global or successor-Brain threshold.
    raw = brain.get("rollover_threshold", DEFAULT_ROLLOVER_THRESHOLD)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise RuntimeErrorWithCode(
            "brain_runtime.rollover_threshold must be a positive integer"
        )
    return raw


def _policy_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _policy_transport(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in NATIVE_LUNA_TRANSPORTS:
        return "codex_native_luna_worker"
    if normalized in CLI_LUNA_TRANSPORTS:
        return "codex_cli_ephemeral"
    return None


def effective_policy(state: dict[str, Any]) -> dict[str, Any]:
    """Return typed current transport policy with provenance.

    Arbitrary authorization prose is intentionally ignored.  The one known
    historical amendment that predates typed policy is recognized only by its
    exact state key.
    """
    session = state.get("autonomy_session")
    session = session if isinstance(session, dict) else {}
    allowed = session.get("allowed")
    allowed = allowed if isinstance(allowed, dict) else {}
    typed = session.get("transport_policy")
    typed = typed if isinstance(typed, dict) else {}
    if not typed and isinstance(state.get("transport_policy"), dict):
        typed = state["transport_policy"]

    result: dict[str, Any] = {
        "luna_transport": "codex_cli_ephemeral",
        "native_luna_subagents": False,
        "external_network": False,
        "public_read_only_web": False,
        "provenance": {},
    }

    def assign(name: str, value: Any, source: str, **metadata: Any) -> None:
        result[name] = value
        provenance: dict[str, Any] = {"source": source}
        provenance.update(metadata)
        result["provenance"][name] = provenance

    transport_value = None
    for key in ("luna_transport", "luna_worker_transport", "transport"):
        transport_value = _policy_transport(typed.get(key))
        if transport_value:
            break
    nested_luna = typed.get("luna")
    if not isinstance(nested_luna, dict):
        nested_luna = typed.get("luna_worker")
    if not isinstance(nested_luna, dict):
        nested_luna = typed.get("native_luna")
    if isinstance(nested_luna, dict) and transport_value is None:
        for key in ("transport", "worker_transport", "preferred_transport"):
            transport_value = _policy_transport(nested_luna.get(key))
            if transport_value:
                break
    if transport_value:
        assign("luna_transport", transport_value, "typed_transport_policy")

    native_value: bool | None = None
    native_source = ""
    for key in (
        "native_luna_subagents",
        "native_subagents",
        "allow_native_luna",
        "native_override",
        "native_luna_transport_override",
    ):
        candidate = _policy_bool(typed.get(key))
        if candidate is not None:
            native_value = candidate
            native_source = f"typed_transport_policy.{key}"
            break
        if key in {"native_override", "native_luna_transport_override"} and isinstance(
            typed.get(key), dict
        ):
            candidate = _policy_bool(typed[key].get("enabled"))
            if candidate is not None:
                native_value = candidate
                native_source = f"typed_transport_policy.{key}.enabled"
                break
    if native_value is None and isinstance(nested_luna, dict):
        for key in (
            "native_luna_subagents",
            "native_subagents",
            "allow_native",
            "enabled",
        ):
            candidate = _policy_bool(nested_luna.get(key))
            if candidate is not None:
                native_value = candidate
                native_source = f"typed_transport_policy.luna.{key}"
                break
    if native_value is None and transport_value is not None:
        native_value = transport_value == "codex_native_luna_worker"
        native_source = "typed_transport_policy.transport"
    if native_value is not None:
        assign("native_luna_subagents", native_value, native_source)
        if native_value:
            assign("luna_transport", "codex_native_luna_worker", native_source)
    elif KNOWN_NATIVE_LUNA_AMENDMENT in session:
        amendment = session.get(KNOWN_NATIVE_LUNA_AMENDMENT)
        valid_amendment = amendment is True
        scope = None
        if isinstance(amendment, dict):
            valid_amendment = amendment.get("enabled") is True or (
                isinstance(amendment.get("instruction"), str)
                and bool(amendment.get("instruction").strip())
                and isinstance(amendment.get("transport_scope"), str)
                and bool(amendment.get("transport_scope").strip())
            )
            if isinstance(amendment.get("transport_scope"), str):
                scope = amendment["transport_scope"].strip()[:240]
        if valid_amendment:
            metadata: dict[str, Any] = {
                "amendment_id": KNOWN_NATIVE_LUNA_AMENDMENT,
                "source_pointer": KNOWN_NATIVE_LUNA_AMENDMENT,
            }
            if scope:
                metadata["scope"] = scope
            assign(
                "native_luna_subagents",
                True,
                "known_user_amendment",
                **metadata,
            )
            assign(
                "luna_transport",
                "codex_native_luna_worker",
                "known_user_amendment",
                **metadata,
            )
    elif isinstance(allowed.get("native_luna_subagents"), bool):
        assign("native_luna_subagents", allowed["native_luna_subagents"], "typed_allowed")
        if allowed["native_luna_subagents"]:
            assign("luna_transport", "codex_native_luna_worker", "typed_allowed")
    else:
        result["provenance"]["native_luna_subagents"] = {"source": "default"}
        result["provenance"]["luna_transport"] = {
            "source": result["provenance"].get("luna_transport", {}).get(
                "source", "default"
            )
        }

    external_value: bool | None = None
    external_source = ""
    for key in (
        "external_network",
        "authenticated_external_requests",
        "allow_external_requests",
        "normal_network_operations",
    ):
        candidate = _policy_bool(typed.get(key))
        if candidate is not None:
            external_value = candidate
            external_source = f"typed_transport_policy.{key}"
            break
    if external_value is None:
        external = typed.get("external")
        if isinstance(external, dict):
            candidate = _policy_bool(external.get("allowed"))
            if candidate is not None:
                external_value = candidate
                external_source = "typed_transport_policy.external.allowed"
    if external_value is None:
        for key in (
            "authenticated_external_requests",
            "normal_network_operations",
            "external_network",
        ):
            candidate = _policy_bool(allowed.get(key))
            if candidate is not None:
                external_value = candidate
                external_source = f"typed_allowed.{key}"
                break
    if external_value is None:
        result["provenance"]["external_network"] = {"source": "default"}
    else:
        assign("external_network", external_value, external_source)
    if isinstance(allowed.get("public_read_only_web"), bool):
        assign("public_read_only_web", allowed["public_read_only_web"], "typed_allowed")
    else:
        result["provenance"]["public_read_only_web"] = {"source": "default"}
    result["luna"] = {
        "transport": result["luna_transport"],
        "native_luna_subagents": result["native_luna_subagents"],
    }
    result["external_requests_allowed"] = result["external_network"]
    return result


def _compact_list(value: Any, limit: int = 32) -> list[Any]:
    if not isinstance(value, list):
        return []
    compact: list[Any] = []
    for item in value[:limit]:
        if isinstance(item, (str, int, float, bool)) or item is None:
            compact.append(item)
        elif isinstance(item, dict):
            selected = next(
                (
                    item[key]
                    for key in (
                        "task_id",
                        "experiment_id",
                        "route_id",
                        "stage_id",
                        "id",
                    )
                    if isinstance(item.get(key), (str, int))
                ),
                None,
            )
            compact.append(selected if selected is not None else "<object>")
        else:
            compact.append("<value>")
    return compact


def _records_snapshot(root: Path | None) -> dict[str, Any] | None:
    if root is None:
        return None
    index_path = root / "artifacts" / "orchestration" / "records_index.json"
    if not index_path.is_file():
        return None
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "unreadable"}
    if not isinstance(index, dict):
        return {"status": "invalid"}
    output: dict[str, Any] = {}
    for key in (
        "status",
        "current_event",
        "current_experiment_id",
        "current_work_id",
        "current_stage_id",
        "current_route_id",
        "scientific_stage_count",
        "work_count",
        "no_information_streak",
        "review_recommended",
        "history_pointer",
        "history_path",
        "latest_result_id",
    ):
        if key in index and isinstance(index[key], (str, int, float, bool, type(None))):
            output[key] = index[key]
        elif key == "current_event" and isinstance(index.get(key), dict):
            event = index[key]
            output[key] = {
                field: event.get(field)
                for field in ("event_id", "kind", "status", "work_id")
                if isinstance(event.get(field), (str, int, float, bool, type(None)))
            }
    return output


def snapshot_state(state: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    """Project a compact current runtime view, excluding historical transcripts."""
    session = state.get("autonomy_session")
    session = session if isinstance(session, dict) else {}
    brain = state.get("brain_runtime")
    brain = brain if isinstance(brain, dict) else {}
    research = state.get("research_runtime")
    research = research if isinstance(research, dict) else {}
    mode = current_control_mode(state)
    threshold = rollover_threshold(brain)
    reservations = session.get("budget_reservations", {})
    reserved = budget_reserved_totals(session)
    result: dict[str, Any] = {
        "schema_version": state.get("schema_version"),
        "project_id": state.get("project_id"),
        "autonomy_session": {
            "session_id": session.get("session_id"),
            "enabled": session.get("enabled"),
            "goal_status": session.get("goal_status"),
            "control_mode": mode,
            "active_worker_count": len(research.get("active_luna_tasks", []))
            if isinstance(research.get("active_luna_tasks"), list)
            else 0,
        },
        "brain_runtime": {
            "active_brain_generation": brain.get("active_brain_generation"),
            "active_brain_task_id": brain.get("active_brain_task_id"),
            "context_health": brain.get("context_health"),
            "context_compaction_count": context_compaction_count(brain),
            "rollover_threshold": threshold,
            "handoff_status": brain.get("handoff_status"),
        },
        "research_runtime": {
            "state": research.get("state"),
            "active_stage_id": research.get("active_stage_id"),
            "stage_count": research.get("stage_count"),
            "active_route_ids": _compact_list(research.get("active_route_ids")),
            "active_experiment_ids": _compact_list(
                research.get("active_experiment_ids")
            ),
            "active_luna_tasks": _compact_list(research.get("active_luna_tasks")),
            "next_action": research.get("next_action"),
        },
        "budget": {
            "limits": dict(session.get("global_limits", {}))
            if isinstance(session.get("global_limits"), dict)
            else {},
            "used": dict(session.get("global_budget_used", {}))
            if isinstance(session.get("global_budget_used"), dict)
            else {},
            "remaining": dict(session.get("global_budget_remaining", {}))
            if isinstance(session.get("global_budget_remaining"), dict)
            else {},
            "reserved": reserved,
            "active_reservation_count": sum(
                1
                for value in (
                    reservations.values() if isinstance(reservations, dict) else []
                )
                if isinstance(value, dict)
                and value.get("status", "reserved") == "reserved"
            ),
        },
        "effective_policy": effective_policy(state),
    }
    records = _records_snapshot(root)
    if records is not None:
        result["records"] = records
    return result


def runtime_view(state: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    """Compatibility alias for record tooling that calls this a runtime view."""
    return snapshot_state(state, root)


def assert_active(
    state: dict[str, Any], generation: int, task_id: str | None
) -> dict[str, Any]:
    brain = state.get("brain_runtime")
    if not isinstance(brain, dict):
        raise RuntimeErrorWithCode("state lacks brain_runtime")
    actual_generation = brain.get("active_brain_generation")
    if actual_generation != generation:
        raise RuntimeErrorWithCode(
            f"stale Brain generation: expected active {generation}, found {actual_generation}",
            code=9,
        )
    active_task_id = brain.get("active_brain_task_id")
    if task_id and active_task_id and task_id != active_task_id:
        raise RuntimeErrorWithCode(
            f"task {task_id!r} is not the active Brain task {active_task_id!r}", code=9
        )
    return brain


def context_compaction_count(brain: dict[str, Any]) -> int:
    # Do not promote an older aggregate/legacy field into the current
    # generation counter: that would infer historical compactions.
    value = brain.get("context_compaction_count", 0)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeErrorWithCode(
            "brain_runtime.context_compaction_count must be a nonnegative integer"
        )
    return value


def context_health_floor(
    compactions: int, threshold: int = DEFAULT_ROLLOVER_THRESHOLD
) -> str:
    if compactions >= threshold:
        return "rollover_required"
    if compactions == 1:
        return "rollover_recommended"
    return "healthy"


def _zero_budget() -> dict[str, int | float]:
    return {
        "wall_minutes": 0,
        "worker_assignments": 0,
        "external_calls": 0,
        "external_cost_usd": 0.0,
    }


def budget_reserved_totals(session: dict[str, Any]) -> dict[str, int | float]:
    totals = _zero_budget()
    raw = session.get("budget_reservations", {})
    values = raw.values() if isinstance(raw, dict) else []
    for reservation in values:
        if not isinstance(reservation, dict):
            continue
        if reservation.get("status", "reserved") != "reserved":
            continue
        amounts = reservation.get("reserved", {})
        if not isinstance(amounts, dict):
            continue
        for key in BUDGET_KEYS:
            value = amounts.get(key, 0)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[key] += value
    return totals


def _budget_amounts(args: argparse.Namespace) -> dict[str, int | float]:
    amounts: dict[str, int | float] = {
        key: getattr(args, key, 0) for key in BUDGET_KEYS
    }
    if any(
        not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0
        for value in amounts.values()
    ):
        raise RuntimeErrorWithCode("budget amounts must be nonnegative numbers")
    return amounts


def _budget_key(args: argparse.Namespace) -> str:
    for name in ("idempotency_key", "reservation_key", "budget_task_id"):
        value = getattr(args, name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    # A convenient compatibility form is reserve/finalize with --task-id and
    # no Brain owner.  When --generation is supplied, --task-id remains the
    # owner and an explicit idempotency key is required.
    value = getattr(args, "task_id", None)
    if getattr(args, "generation", None) is None and isinstance(value, str) and value.strip():
        return value.strip()
    raise RuntimeErrorWithCode(
        "budget reservation requires a non-empty --idempotency-key (task key)"
    )


def _budget_state(session: dict[str, Any]) -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any]
]:
    limits = session.get("global_limits")
    used = session.get("global_budget_used")
    remaining = session.get("global_budget_remaining")
    if not all(isinstance(value, dict) for value in (limits, used, remaining)):
        raise RuntimeErrorWithCode("autonomy session budget fields are missing")
    return limits, used, remaining


def _check_budget_dimensions(
    limits: dict[str, Any],
    used: dict[str, Any],
    reserved: dict[str, Any],
    additions: dict[str, int | float],
) -> tuple[dict[str, int | float], list[str]]:
    projected: dict[str, int | float] = {}
    exceeded: list[str] = []
    for key in BUDGET_KEYS:
        limit = limits.get(key)
        current = used.get(key)
        held = reserved.get(key, 0)
        if not all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in (limit, current, held)
        ):
            raise RuntimeErrorWithCode(f"budget dimension {key} is invalid")
        projected[key] = current + held + additions[key]
        if projected[key] > limit:
            exceeded.append(key)
    return projected, exceeded


def _set_budget_derived(
    session: dict[str, Any],
    limits: dict[str, Any],
    used: dict[str, Any],
) -> None:
    session["global_budget_remaining"] = {
        key: limits[key] - used[key] for key in BUDGET_KEYS
    }
    reserved = budget_reserved_totals(session)
    session["global_budget_reserved"] = reserved
    session["global_budget_available"] = {
        key: limits[key] - used[key] - reserved[key] for key in BUDGET_KEYS
    }


def transfer_lineage(
    brain: dict[str, Any],
    generation: int,
    current_task_id: str | None,
    successor_task_id: str,
) -> list[dict[str, Any]]:
    raw = brain.get("brain_task_lineage", [])
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise RuntimeErrorWithCode("brain_task_lineage must be a list of objects")
    lineage = [dict(item) for item in raw]

    generations = [item.get("generation") for item in lineage]
    if len(generations) != len(set(generations)):
        raise RuntimeErrorWithCode("brain_task_lineage contains duplicate generations")
    if not lineage:
        previous_generation = brain.get("previous_brain_generation")
        previous_task_id = brain.get("previous_brain_task_id")
        if (
            isinstance(previous_generation, int)
            and previous_generation < generation
            and isinstance(previous_task_id, str)
            and previous_task_id
        ):
            lineage.append(
                {
                    "generation": previous_generation,
                    "task_id": previous_task_id,
                    "status": "retired",
                    "predecessor_task_id": None,
                    "successor_task_id": current_task_id,
                }
            )

    known_task_ids = {
        item.get("task_id")
        for item in lineage
        if isinstance(item.get("task_id"), str) and item.get("task_id")
    }
    if successor_task_id in known_task_ids:
        raise RuntimeErrorWithCode("successor task already exists in brain_task_lineage")

    current = next(
        (item for item in lineage if item.get("generation") == generation), None
    )
    if current is None:
        if not current_task_id:
            raise RuntimeErrorWithCode(
                "current Brain task ID is required to preserve transcript lineage"
            )
        current = {
            "generation": generation,
            "task_id": current_task_id,
            "status": "active",
            "predecessor_task_id": brain.get("previous_brain_task_id"),
            "successor_task_id": None,
        }
        lineage.append(current)
    else:
        recorded_task_id = current.get("task_id")
        if current_task_id and recorded_task_id and recorded_task_id != current_task_id:
            raise RuntimeErrorWithCode(
                "current Brain task ID conflicts with brain_task_lineage"
            )
        if not recorded_task_id:
            if not current_task_id:
                raise RuntimeErrorWithCode(
                    "current Brain task ID is required to preserve transcript lineage"
                )
            current["task_id"] = current_task_id
        if current.get("status") not in LINEAGE_STATUS:
            raise RuntimeErrorWithCode("current Brain has invalid lineage status")
        if current.get("status") == "retired":
            raise RuntimeErrorWithCode("current Brain is already retired in lineage")

    resolved_current_task_id = current.get("task_id")
    current["status"] = "retired"
    current["successor_task_id"] = successor_task_id
    lineage.append(
        {
            "generation": generation + 1,
            "task_id": successor_task_id,
            "status": "active",
            "predecessor_task_id": resolved_current_task_id,
            "successor_task_id": None,
        }
    )
    lineage.sort(key=lambda item: item["generation"])
    brain["brain_task_lineage"] = lineage
    return lineage


def load_runtime(root_raw: str) -> tuple[Path, Path, dict[str, Any]]:
    root = resolve_root(root_raw)
    state_path = state_path_for(root)
    state = load_object(state_path)
    if state.get("schema_version") != 4:
        raise RuntimeErrorWithCode("runtime_control requires schema version 4")
    return root, state_path, state


@contextmanager
def locked_runtime(
    root_raw: str,
) -> Iterator[tuple[Path, Path, dict[str, Any]]]:
    root = resolve_root(root_raw)
    state_path = state_path_for(root)
    with state_lock(state_path):
        state = load_object(state_path)
        if state.get("schema_version") != 4:
            raise RuntimeErrorWithCode("runtime_control requires schema version 4")
        yield root, state_path, state


def command_assert(args: argparse.Namespace) -> dict[str, Any]:
    _, _, state = load_runtime(args.root)
    brain = assert_active(state, args.generation, args.task_id)
    return {
        "status": "active",
        "generation": brain["active_brain_generation"],
        "task_id": brain.get("active_brain_task_id"),
        "control_mode": current_control_mode(state),
        "context_health": brain.get("context_health", "healthy"),
        "context_compaction_count": context_compaction_count(brain),
        "rollover_threshold": rollover_threshold(brain),
    }


def _check_current_records(root: Path, state: dict[str, Any]) -> None:
    index_path = root / "artifacts" / "orchestration" / "records_index.json"
    if not index_path.is_file():
        return
    try:
        import research_records
    except ImportError:
        return
    checker = getattr(research_records, "check_records", None)
    if not callable(checker):
        return
    try:
        result = checker(root, state)
    except TypeError:
        result = checker(root)
    if isinstance(result, dict) and result.get("status") not in (None, "pass"):
        raise RuntimeErrorWithCode(
            "current research records require refresh before dispatch: "
            + ", ".join(str(item) for item in result.get("issues", [])),
            code=10,
        )


def command_assert_dispatch(args: argparse.Namespace) -> dict[str, Any]:
    root, _, state = load_runtime(args.root)
    if not args.task_id:
        raise RuntimeErrorWithCode(
            "assert-dispatch requires the active owner --task-id", code=9
        )
    brain = assert_active(state, args.generation, args.task_id)
    mode = current_control_mode(state)
    if mode != "RUNNING":
        raise RuntimeErrorWithCode(
            f"dispatch blocked while autonomy control_mode is {mode}", code=10
        )
    session = state.get("autonomy_session")
    if not isinstance(session, dict) or session.get("enabled") is not True:
        raise RuntimeErrorWithCode("dispatch requires an enabled autonomy session", code=10)
    if isinstance(session, dict) and session.get("budget_overrun"):
        raise RuntimeErrorWithCode(
            "dispatch blocked after a conservative budget overrun; review the session",
            code=10,
        )
    health = brain.get("context_health", "healthy")
    if health not in CONTEXT_HEALTH:
        raise RuntimeErrorWithCode("brain_runtime.context_health is invalid")
    threshold = rollover_threshold(brain)
    count = context_compaction_count(brain)
    if health == "rollover_required" or count >= threshold:
        raise RuntimeErrorWithCode(
            "dispatch blocked until Brain rollover at context compaction threshold",
            code=10,
        )
    _check_current_records(root, state)
    return {
        "status": "dispatchable",
        "generation": brain["active_brain_generation"],
        "task_id": brain.get("active_brain_task_id"),
        "control_mode": mode,
        "context_health": health,
        "context_compaction_count": count,
        "rollover_threshold": threshold,
    }


def command_set_context(args: argparse.Namespace) -> dict[str, Any]:
    with locked_runtime(args.root) as (_, state_path, state):
        brain = assert_active(state, args.generation, args.task_id)
        current = brain.get("context_health", "healthy")
        if current not in CONTEXT_HEALTH:
            raise RuntimeErrorWithCode("brain_runtime.context_health is invalid")
        floor = context_health_floor(
            context_compaction_count(brain), rollover_threshold(brain)
        )
        if CONTEXT_HEALTH_RANK[args.health] < CONTEXT_HEALTH_RANK[floor]:
            raise RuntimeErrorWithCode(
                f"context health cannot be lower than {floor} after recorded compaction"
            )
        if CONTEXT_HEALTH_RANK[args.health] < CONTEXT_HEALTH_RANK[current]:
            raise RuntimeErrorWithCode(
                "context health is monotonic within one Brain generation"
            )
        brain["context_health"] = args.health
        if args.health == "rollover_required" and not brain.get("rollover_reason"):
            brain["rollover_reason"] = args.reason or "observable_context_degradation"
        atomic_write(state_path, state)
        return {
            "status": "updated",
            "context_health": args.health,
            "rollover_reason": brain.get("rollover_reason"),
        }


def command_record_compaction(args: argparse.Namespace) -> dict[str, Any]:
    with locked_runtime(args.root) as (_, state_path, state):
        brain = assert_active(state, args.generation, args.task_id)
        raw_events = brain.get("context_compaction_event_ids", [])
        if not isinstance(raw_events, list) or not all(
            isinstance(value, str) and value for value in raw_events
        ):
            raise RuntimeErrorWithCode(
                "brain_runtime.context_compaction_event_ids must be a list of non-empty strings"
            )
        events = list(raw_events)
        event_id = args.event_id.strip() if args.event_id else None
        if event_id and event_id in events:
            return {
                "status": "duplicate",
                "event_id": event_id,
                "context_compaction_count": context_compaction_count(brain),
                "context_health": brain.get("context_health", "healthy"),
                "rollover_required": brain.get("context_health") == "rollover_required",
            }

        count = context_compaction_count(brain) + 1
        if event_id:
            events.append(event_id)
        brain["context_compaction_count"] = count
        brain["context_compaction_event_ids"] = events
        threshold = rollover_threshold(brain)
        floor = context_health_floor(count, threshold)
        current = brain.get("context_health", "healthy")
        if current not in CONTEXT_HEALTH:
            raise RuntimeErrorWithCode("brain_runtime.context_health is invalid")
        if CONTEXT_HEALTH_RANK[current] < CONTEXT_HEALTH_RANK[floor]:
            brain["context_health"] = floor
        if count >= threshold:
            if threshold == 2:
                brain["rollover_reason"] = "second_context_compaction"
            elif not brain.get("rollover_reason"):
                brain["rollover_reason"] = "rollover_threshold_context_compaction"
            research = state.get("research_runtime")
            if isinstance(research, dict) and current_control_mode(state) == "RUNNING":
                active_luna = research.get("active_luna_tasks", [])
                research["next_action"] = (
                    "finish_active_luna_then_prepare_rollover"
                    if active_luna
                    else "write_handoff_and_prepare_rollover"
                )
        atomic_write(state_path, state)
        return {
            "status": "recorded",
            "event_id": event_id,
            "context_compaction_count": count,
            "context_health": brain["context_health"],
            "rollover_required": count >= threshold,
            "rollover_reason": brain.get("rollover_reason"),
            "rollover_threshold": threshold,
        }


def _pause_marker(mode: str) -> str:
    return {
        "PAUSED_USER": "await_user_resume",
        "PAUSED_PLATFORM": "await_platform_resume",
        "PAUSED_REVIEW": "await_review_resume",
    }.get(mode, "await_resume")


def _prepare_pause_latch(
    session: dict[str, Any], research: dict[str, Any], mode: str
) -> None:
    existing_saved = session.get("saved_next_action")
    if not isinstance(existing_saved, str) or not existing_saved:
        current_action = research.get("next_action")
        if isinstance(current_action, str) and current_action != _pause_marker(mode):
            session["saved_next_action"] = current_action
    if "paused_research_state" not in session:
        session["paused_research_state"] = research.get("state")
    session["control_mode"] = mode
    session["pause_reason"] = session.get("pause_reason")
    # A later platform/review pause must not silently clear an earlier user
    # stop request.  Only command_resume clears this latch.
    session["user_pause_latch"] = bool(session.get("user_pause_latch")) or mode == "PAUSED_USER"
    if mode in PAUSED_CONTROL_MODES:
        research["next_action"] = _pause_marker(mode)


def _owner_if_supplied(state: dict[str, Any], args: argparse.Namespace) -> None:
    generation = getattr(args, "generation", None)
    task_id = getattr(args, "task_id", None)
    if generation is None and task_id:
        raise RuntimeErrorWithCode(
            "an owner --generation is required when --task-id is supplied", code=9
        )
    if generation is not None:
        assert_active(state, generation, task_id)


def command_pause(args: argparse.Namespace) -> dict[str, Any]:
    with locked_runtime(args.root) as (_, state_path, state):
        _owner_if_supplied(state, args)
        mode = args.mode
        if mode not in PAUSED_CONTROL_MODES:
            raise RuntimeErrorWithCode("pause mode must be a PAUSED_* control mode")
        if current_control_mode(state) == "TERMINAL":
            raise RuntimeErrorWithCode("terminal autonomy session cannot be paused")
        session = state.get("autonomy_session")
        research = state.get("research_runtime")
        if not isinstance(session, dict) or not isinstance(research, dict):
            raise RuntimeErrorWithCode("state lacks autonomy session or research runtime")
        _prepare_pause_latch(session, research, mode)
        reason = getattr(args, "reason", None)
        if reason:
            session["pause_reason"] = reason.strip()
        atomic_write(state_path, state)
        return {
            "status": "paused",
            "control_mode": mode,
            "saved_next_action": session.get("saved_next_action"),
            "active_worker_count": len(research.get("active_luna_tasks", []))
            if isinstance(research.get("active_luna_tasks"), list)
            else 0,
        }


def command_resume(args: argparse.Namespace) -> dict[str, Any]:
    with locked_runtime(args.root) as (_, state_path, state):
        _owner_if_supplied(state, args)
        mode = current_control_mode(state)
        if mode == "TERMINAL":
            raise RuntimeErrorWithCode("terminal autonomy session cannot resume")
        session = state.get("autonomy_session")
        research = state.get("research_runtime")
        if not isinstance(session, dict) or not isinstance(research, dict):
            raise RuntimeErrorWithCode("state lacks autonomy session or research runtime")
        saved = session.get("saved_next_action")
        if isinstance(saved, str) and saved and not _marker_is_user_pause(saved):
            research["next_action"] = saved
        elif _marker_is_user_pause(research.get("next_action")):
            research["next_action"] = "inspect_goal_gap"
        previous_state = session.get("paused_research_state")
        if isinstance(previous_state, str) and previous_state:
            research["state"] = previous_state
        session["control_mode"] = "RUNNING"
        # Only this explicit command clears the user latch.  Rollover and
        # maintenance operations intentionally leave it untouched.
        session["user_pause_latch"] = False
        session.pop("pause_reason", None)
        session.pop("saved_next_action", None)
        session.pop("paused_research_state", None)
        atomic_write(state_path, state)
        return {
            "status": "resumed",
            "previous_control_mode": mode,
            "control_mode": "RUNNING",
            "next_action": research.get("next_action"),
        }


def command_prepare(args: argparse.Namespace) -> dict[str, Any]:
    with locked_runtime(args.root) as (root, state_path, state):
        brain = assert_active(state, args.generation, args.task_id)
        session = state.get("autonomy_session", {})
        if not isinstance(session, dict) or not session.get("enabled"):
            raise RuntimeErrorWithCode("autonomy session is not enabled")
        allowed = session.get("allowed", {})
        if not isinstance(allowed, dict) or allowed.get("brain_rollover") is not True:
            raise RuntimeErrorWithCode("standing authorization does not allow Brain rollover")
        if brain.get("context_health") != "rollover_required":
            raise RuntimeErrorWithCode(
                "set context health to rollover_required before preparing rollover"
            )
        research = state.get("research_runtime", {})
        active_luna = (
            research.get("active_luna_tasks", []) if isinstance(research, dict) else []
        )
        if active_luna:
            raise RuntimeErrorWithCode(
                "reach a safe boundary and persist active Luna results before rollover"
            )
        handoff_raw = brain.get("handoff_path")
        if not isinstance(handoff_raw, str) or not handoff_raw:
            raise RuntimeErrorWithCode("brain_runtime.handoff_path is missing")
        handoff_path = (root / handoff_raw).resolve()
        try:
            handoff_path.relative_to(root)
        except ValueError as exc:
            raise RuntimeErrorWithCode("handoff path escapes the research root") from exc
        if not handoff_path.is_file():
            raise RuntimeErrorWithCode(
                "write the compact brain handoff before preparing rollover"
            )
        if brain.get("handoff_status") == "prepared":
            raise RuntimeErrorWithCode("a successor Brain is already pending")

        mode = current_control_mode(state)
        if mode in PAUSED_CONTROL_MODES and isinstance(research, dict):
            _prepare_pause_latch(session, research, mode)
        if not brain.get("rollover_reason"):
            brain["rollover_reason"] = "observable_context_degradation"
        brain["pending_successor_generation"] = args.generation + 1
        brain["handoff_status"] = "prepared"
        research["state"] = "ROLLOVER_PREPARING"
        research["next_action"] = "create_exactly_one_successor_brain"
        atomic_write(state_path, state)
        return {
            "status": "prepared",
            "from_generation": args.generation,
            "pending_generation": args.generation + 1,
            "handoff_path": str(handoff_path),
            "control_mode": mode,
            "saved_next_action": session.get("saved_next_action"),
        }


def command_transfer(args: argparse.Namespace) -> dict[str, Any]:
    with locked_runtime(args.root) as (_, state_path, state):
        brain = assert_active(state, args.generation, args.task_id)
        if brain.get("handoff_status") != "prepared":
            raise RuntimeErrorWithCode("rollover is not in prepared state")
        pending = brain.get("pending_successor_generation")
        if pending != args.generation + 1:
            raise RuntimeErrorWithCode("pending successor generation is inconsistent")
        if not args.successor_task_id.strip():
            raise RuntimeErrorWithCode("successor task ID must be non-empty")
        if args.successor_task_id == brain.get("active_brain_task_id"):
            raise RuntimeErrorWithCode("successor task must differ from the old Brain task")

        rollover_reason = brain.get("rollover_reason") or "observable_context_degradation"
        source_context_compactions = context_compaction_count(brain)
        previous_task_id = brain.get("active_brain_task_id") or args.task_id
        lineage = transfer_lineage(
            brain,
            args.generation,
            previous_task_id,
            args.successor_task_id,
        )
        session = state.get("autonomy_session")
        session = session if isinstance(session, dict) else {}
        mode = current_control_mode(state)
        saved_next_action = session.get("saved_next_action")
        paused_state = session.get("paused_research_state")
        brain["previous_brain_generation"] = args.generation
        brain["previous_brain_task_id"] = previous_task_id
        brain["active_brain_generation"] = pending
        brain["active_brain_task_id"] = args.successor_task_id
        brain["pending_successor_generation"] = None
        brain["context_health"] = "healthy"
        brain["context_compaction_count"] = 0
        brain["context_compaction_event_ids"] = []
        brain["rollover_reason"] = None
        brain["rollover_count"] = int(brain.get("rollover_count", 0)) + 1
        brain["handoff_status"] = "transferred"
        research = state["research_runtime"]
        if mode in PAUSED_CONTROL_MODES:
            # Keep the pause latch and saved action across transfer.  A new
            # Brain must not accidentally become dispatchable.
            session["control_mode"] = mode
            if isinstance(saved_next_action, str) and saved_next_action:
                session["saved_next_action"] = saved_next_action
            if paused_state is not None:
                session["paused_research_state"] = paused_state
            research["state"] = (
                paused_state if isinstance(paused_state, str) else "READY"
            )
            research["next_action"] = _pause_marker(mode)
        else:
            research["state"] = "RUNNING"
            research["next_action"] = "successor_resume_from_compact_handoff"
        atomic_write(state_path, state)
        return {
            "status": "transferred",
            "previous_generation": args.generation,
            "active_generation": pending,
            "previous_task_id": previous_task_id,
            "active_task_id": args.successor_task_id,
            "rollover_reason": rollover_reason,
            "source_context_compactions": source_context_compactions,
            "brain_task_lineage": lineage,
            "control_mode": mode,
            "saved_next_action": session.get("saved_next_action"),
        }


def command_platform_blocked(args: argparse.Namespace) -> dict[str, Any]:
    with locked_runtime(args.root) as (_, state_path, state):
        brain = assert_active(state, args.generation, args.task_id)
        if brain.get("handoff_status") != "prepared":
            raise RuntimeErrorWithCode("prepare rollover before marking platform blocked")
        session = state.get("autonomy_session")
        session = session if isinstance(session, dict) else {}
        research = state["research_runtime"]
        mode = current_control_mode(state)
        if mode == "RUNNING":
            _prepare_pause_latch(session, research, "PAUSED_PLATFORM")
        brain["handoff_status"] = "platform_blocked"
        brain["pending_successor_generation"] = None
        research["state"] = "ROLLOVER_PLATFORM_BLOCKED"
        research["next_action"] = "report_native_thread_creation_limitation"
        atomic_write(state_path, state)
        return {
            "status": "platform_blocked",
            "generation": args.generation,
            "control_mode": current_control_mode(state),
            "saved_next_action": session.get("saved_next_action"),
        }


def command_record_budget(args: argparse.Namespace) -> dict[str, Any]:
    with locked_runtime(args.root) as (_, state_path, state):
        assert_active(state, args.generation, args.task_id)
        increments = _budget_amounts(args)
        session = state.get("autonomy_session")
        if not isinstance(session, dict):
            raise RuntimeErrorWithCode("state lacks autonomy_session")
        if session.get("budget_overrun"):
            raise RuntimeErrorWithCode(
                "budget session is overrun; no further budget may be recorded",
                code=8,
            )
        limits, used, _ = _budget_state(session)
        reserved = budget_reserved_totals(session)
        projected = {
            key: used[key] + increments[key] for key in BUDGET_KEYS
        }
        exceeded = [
            key
            for key in BUDGET_KEYS
            if projected[key] + reserved[key] > limits[key]
        ]
        if exceeded:
            raise RuntimeErrorWithCode(
                "budget update would overlap active reservations or exceed session limits: "
                + ", ".join(exceeded),
                code=8,
            )
        session["global_budget_used"] = projected
        _set_budget_derived(session, limits, projected)
        atomic_write(state_path, state)
        return {
            "status": "recorded",
            "used": projected,
            "remaining": session["global_budget_remaining"],
            "reserved": session["global_budget_reserved"],
        }


def command_reserve_budget(args: argparse.Namespace) -> dict[str, Any]:
    with locked_runtime(args.root) as (_, state_path, state):
        if getattr(args, "generation", None) is not None:
            assert_active(state, args.generation, args.task_id)
        amounts = _budget_amounts(args)
        key = _budget_key(args)
        session = state.get("autonomy_session")
        if not isinstance(session, dict):
            raise RuntimeErrorWithCode("state lacks autonomy_session")
        limits, used, _ = _budget_state(session)
        reservations = session.get("budget_reservations")
        if reservations is None:
            reservations = {}
            session["budget_reservations"] = reservations
        if not isinstance(reservations, dict):
            raise RuntimeErrorWithCode("autonomy_session.budget_reservations must be an object")
        existing = reservations.get(key)
        uncertain = bool(getattr(args, "uncertain", False))
        if existing is not None:
            if not isinstance(existing, dict):
                raise RuntimeErrorWithCode("existing budget reservation is invalid")
            if (
                existing.get("reserved") == amounts
                and bool(existing.get("uncertain", False)) == uncertain
            ):
                return {
                    "status": "duplicate",
                    "idempotency_key": key,
                    "reservation": existing,
                    "reserved": budget_reserved_totals(session),
                }
            raise RuntimeErrorWithCode(
                "idempotency key already has a different budget reservation", code=8
            )
        if session.get("budget_overrun"):
            raise RuntimeErrorWithCode(
                "budget session is overrun; no new reservation may be created",
                code=8,
            )
        held = budget_reserved_totals(session)
        _, exceeded = _check_budget_dimensions(limits, used, held, amounts)
        if exceeded:
            raise RuntimeErrorWithCode(
                "budget reservation would exceed available session limits: "
                + ", ".join(exceeded),
                code=8,
            )
        reservation = {
            "task_id": key,
            "owner_task_id": getattr(args, "task_id", None),
            "reserved": amounts,
            "uncertain": uncertain,
            "status": "reserved",
        }
        reservations[key] = reservation
        _set_budget_derived(session, limits, used)
        atomic_write(state_path, state)
        return {
            "status": "reserved",
            "idempotency_key": key,
            "reservation": reservation,
            "reserved": session["global_budget_reserved"],
            "available": session["global_budget_available"],
        }


def command_finalize_budget(args: argparse.Namespace) -> dict[str, Any]:
    with locked_runtime(args.root) as (_, state_path, state):
        if getattr(args, "generation", None) is not None:
            assert_active(state, args.generation, args.task_id)
        key = _budget_key(args)
        amounts = _budget_amounts(args)
        session = state.get("autonomy_session")
        if not isinstance(session, dict):
            raise RuntimeErrorWithCode("state lacks autonomy_session")
        limits, used, _ = _budget_state(session)
        reservations = session.get("budget_reservations")
        if not isinstance(reservations, dict) or key not in reservations:
            raise RuntimeErrorWithCode(
                f"no active budget reservation for idempotency key {key!r}", code=8
            )
        reservation = reservations[key]
        if not isinstance(reservation, dict):
            raise RuntimeErrorWithCode("budget reservation is invalid")
        if reservation.get("status") == "finalized":
            return {
                "status": "duplicate",
                "idempotency_key": key,
                "reservation": reservation,
                "used": used,
            }
        reserved = reservation.get("reserved")
        if not isinstance(reserved, dict):
            raise RuntimeErrorWithCode("budget reservation amounts are invalid")
        settled: dict[str, int | float] = {}
        uncertain = bool(reservation.get("uncertain", False)) or bool(
            getattr(args, "uncertain", False)
        )
        for key_name in BUDGET_KEYS:
            actual = amounts[key_name]
            held = reserved.get(key_name, 0)
            if not isinstance(held, (int, float)) or isinstance(held, bool):
                raise RuntimeErrorWithCode("budget reservation amounts are invalid")
            settled[key_name] = max(actual, held) if uncertain else actual
        projected = {key_name: used[key_name] + settled[key_name] for key_name in BUDGET_KEYS}
        exceeded = [
            key_name for key_name in BUDGET_KEYS if projected[key_name] > limits[key_name]
        ]
        session["global_budget_used"] = projected
        reservation["status"] = "finalized"
        reservation["uncertain"] = uncertain
        reservation["actual"] = amounts
        reservation["settled"] = settled
        reservation["settlement"] = (
            "conservative_reserved" if uncertain else "actual"
        )
        if exceeded:
            session["budget_overrun"] = {
                "status": "overrun",
                "idempotency_key": key,
                "dimensions": exceeded,
                "limits": {name: limits[name] for name in exceeded},
                "used": {name: projected[name] for name in exceeded},
                "reason": "actual incurred usage exceeded the reserved/session budget",
            }
        _set_budget_derived(session, limits, projected)
        atomic_write(state_path, state)
        return {
            "status": "finalized",
            "idempotency_key": key,
            "settled": settled,
            "used": projected,
            "remaining": session["global_budget_remaining"],
            "reserved": session["global_budget_reserved"],
            "overrun": bool(exceeded),
            "budget_overrun": session.get("budget_overrun"),
        }


def command_reconcile_budget(args: argparse.Namespace) -> dict[str, Any]:
    """Reconcile a conservative uncertain settlement exactly once."""
    with locked_runtime(args.root) as (_, state_path, state):
        if getattr(args, "generation", None) is not None:
            assert_active(state, args.generation, args.task_id)
        key = _budget_key(args)
        actual = _budget_amounts(args)
        session = state.get("autonomy_session")
        if not isinstance(session, dict):
            raise RuntimeErrorWithCode("state lacks autonomy_session")
        limits, used, _ = _budget_state(session)
        reservations = session.get("budget_reservations")
        if not isinstance(reservations, dict) or key not in reservations:
            raise RuntimeErrorWithCode(
                f"no budget reservation for idempotency key {key!r}", code=8
            )
        reservation = reservations[key]
        if not isinstance(reservation, dict):
            raise RuntimeErrorWithCode("budget reservation is invalid")
        if not reservation.get("uncertain"):
            raise RuntimeErrorWithCode(
                "only uncertain reservations can be reconciled", code=8
            )
        prior = reservation.get("reconciled_actual")
        if prior is not None:
            if prior == actual:
                return {
                    "status": "duplicate",
                    "idempotency_key": key,
                    "actual": prior,
                    "used": used,
                }
            raise RuntimeErrorWithCode(
                "idempotency key already has a different reconciliation", code=8
            )
        if reservation.get("status") != "finalized":
            raise RuntimeErrorWithCode(
                "finalize the uncertain reservation before reconciliation", code=8
            )
        settled = reservation.get("settled")
        if not isinstance(settled, dict):
            raise RuntimeErrorWithCode("finalized reservation lacks settled amounts")
        projected = {
            name: used[name] - settled.get(name, 0) + actual[name]
            for name in BUDGET_KEYS
        }
        exceeded = [
            name for name in BUDGET_KEYS if projected[name] > limits[name]
        ]
        reservation["reconciled_actual"] = actual
        reservation["settled"] = actual
        reservation["settlement"] = "reconciled_actual"
        session["global_budget_used"] = projected
        if exceeded:
            session["budget_overrun"] = {
                "status": "overrun",
                "idempotency_key": key,
                "dimensions": exceeded,
                "limits": {name: limits[name] for name in exceeded},
                "used": {name: projected[name] for name in exceeded},
                "reason": "reconciled actual usage exceeded the reserved/session budget",
            }
        elif (
            isinstance(session.get("budget_overrun"), dict)
            and session["budget_overrun"].get("idempotency_key") == key
        ):
            session.pop("budget_overrun", None)
        _set_budget_derived(session, limits, projected)
        atomic_write(state_path, state)
        return {
            "status": "reconciled",
            "idempotency_key": key,
            "actual": actual,
            "used": projected,
            "remaining": session["global_budget_remaining"],
            "overrun": session.get("budget_overrun"),
        }


def command_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    # Snapshot is strictly read-only; in particular it must not create a lock
    # file or update derived budget fields.
    root, _, state = load_runtime(args.root)
    return snapshot_state(state, root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Atomic ownership and budget operations for schema-v4 research runtime."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def ownership(
        subparser: argparse.ArgumentParser,
        *,
        generation_required: bool = True,
        task_required: bool = False,
    ) -> None:
        subparser.add_argument("--root", required=True)
        subparser.add_argument("--generation", type=int, required=generation_required)
        subparser.add_argument("--task-id", required=task_required)

    check = subparsers.add_parser("assert-active")
    ownership(check)
    check.set_defaults(handler=command_assert)

    dispatch = subparsers.add_parser("assert-dispatch")
    ownership(dispatch, task_required=True)
    dispatch.set_defaults(handler=command_assert_dispatch)

    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("--root", required=True)
    snapshot.set_defaults(handler=command_snapshot)

    pause = subparsers.add_parser("pause")
    ownership(pause, task_required=True)
    pause.add_argument(
        "--mode",
        "--control-mode",
        dest="mode",
        choices=sorted(PAUSED_CONTROL_MODES),
        default="PAUSED_USER",
    )
    pause.add_argument("--reason")
    pause.set_defaults(handler=command_pause)

    resume = subparsers.add_parser("resume")
    ownership(resume, task_required=True)
    resume.set_defaults(handler=command_resume)

    context = subparsers.add_parser("set-context-health")
    ownership(context)
    context.add_argument("--health", choices=sorted(CONTEXT_HEALTH), required=True)
    context.add_argument("--reason")
    context.set_defaults(handler=command_set_context)

    compaction = subparsers.add_parser("record-context-compaction")
    ownership(compaction)
    compaction.add_argument("--event-id")
    compaction.set_defaults(handler=command_record_compaction)

    prepare = subparsers.add_parser("prepare-rollover")
    ownership(prepare)
    prepare.set_defaults(handler=command_prepare)

    transfer = subparsers.add_parser("transfer-brain")
    ownership(transfer)
    transfer.add_argument("--successor-task-id", required=True)
    transfer.set_defaults(handler=command_transfer)

    blocked = subparsers.add_parser("mark-platform-blocked")
    ownership(blocked)
    blocked.set_defaults(handler=command_platform_blocked)

    budget = subparsers.add_parser("record-budget")
    ownership(budget)
    budget.add_argument("--wall-minutes", type=int, default=0)
    budget.add_argument("--worker-assignments", type=int, default=0)
    budget.add_argument("--external-calls", type=int, default=0)
    budget.add_argument("--external-cost-usd", type=float, default=0.0)
    budget.set_defaults(handler=command_record_budget)

    def budget_amounts(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--wall-minutes", type=int, default=0)
        subparser.add_argument("--worker-assignments", type=int, default=0)
        subparser.add_argument("--external-calls", type=int, default=0)
        subparser.add_argument("--external-cost-usd", type=float, default=0.0)

    reserve = subparsers.add_parser("reserve-budget")
    ownership(reserve, task_required=True)
    reserve.add_argument(
        "--idempotency-key",
        "--reservation-key",
        "--budget-task-id",
        "--task-key",
        "--task-idempotency-key",
        "--reservation-id",
        dest="idempotency_key",
    )
    budget_amounts(reserve)
    reserve.add_argument("--uncertain", action="store_true")
    reserve.set_defaults(handler=command_reserve_budget)

    finalize = subparsers.add_parser("finalize-budget")
    ownership(finalize, task_required=True)
    finalize.add_argument(
        "--idempotency-key",
        "--reservation-key",
        "--budget-task-id",
        "--task-key",
        "--task-idempotency-key",
        "--reservation-id",
        dest="idempotency_key",
    )
    finalize.add_argument(
        "--wall-minutes",
        "--actual-wall-minutes",
        dest="wall_minutes",
        type=int,
        default=0,
    )
    finalize.add_argument(
        "--worker-assignments",
        "--actual-worker-assignments",
        dest="worker_assignments",
        type=int,
        default=0,
    )
    finalize.add_argument(
        "--external-calls",
        "--actual-external-calls",
        dest="external_calls",
        type=int,
        default=0,
    )
    finalize.add_argument(
        "--external-cost-usd",
        "--actual-external-cost-usd",
        dest="external_cost_usd",
        type=float,
        default=0.0,
    )
    finalize.add_argument("--uncertain", action="store_true")
    finalize.set_defaults(handler=command_finalize_budget)

    reconcile = subparsers.add_parser("reconcile-budget")
    ownership(reconcile, task_required=True)
    reconcile.add_argument(
        "--idempotency-key",
        "--reservation-key",
        "--budget-task-id",
        "--task-key",
        "--task-idempotency-key",
        "--reservation-id",
        dest="idempotency_key",
    )
    budget_amounts(reconcile)
    reconcile.set_defaults(handler=command_reconcile_budget)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = args.handler(args)
    except RuntimeErrorWithCode as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return exc.code
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
