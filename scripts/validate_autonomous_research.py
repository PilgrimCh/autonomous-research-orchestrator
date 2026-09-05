from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


STATES = {
    "READY",
    "RUNNING",
    "GOAL_MODE",
    "ROLLOVER_PREPARING",
    "WAITING_AUTHORIZATION",
    "GLOBAL_RESOURCE_EXHAUSTED",
    "PROJECT_COMPLETE",
    "PROJECT_INFEASIBLE",
    "INTEGRITY_STOP",
    "ROLLOVER_PLATFORM_BLOCKED",
    "WORKER_PLATFORM_BLOCKED",
}
CONTEXT_HEALTH = {"healthy", "rollover_recommended", "rollover_required"}
CONTEXT_HEALTH_RANK = {
    "healthy": 0,
    "rollover_recommended": 1,
    "rollover_required": 2,
}
HANDOFF_STATUS = {"none", "prepared", "transferred", "platform_blocked"}
GOAL_STATUS = {"unresolved", "complete", "infeasible"}
ROUTE_STATUS = {"candidate", "active", "parked", "pruned", "completed"}
ROUTE_DEPTH = {"scout", "focus", "confirm"}
LINEAGE_STATUS = {"active", "retired"}
BUDGET_KEYS = (
    "wall_minutes",
    "worker_assignments",
    "external_calls",
    "external_cost_usd",
)
CONTROL_MODES = {
    "RUNNING",
    "PAUSED_USER",
    "PAUSED_PLATFORM",
    "PAUSED_REVIEW",
    "TERMINAL",
}
PAUSED_CONTROL_MODES = {"PAUSED_USER", "PAUSED_PLATFORM", "PAUSED_REVIEW"}
DEFAULT_ROLLOVER_THRESHOLD = 2
LEGACY_USER_PAUSE_MARKERS = {
    "STOPPED_BY_USER",
    "AWAIT_USER_RESUME",
    "AWAITING_USER_RESUME",
    "PAUSED_USER",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate decision-critical schema-v4 autonomous research state."
    )
    parser.add_argument("--root", required=True)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args()


def load_object(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"missing file: {path}")
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"invalid JSON/YAML-JSON file {path}: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"expected object: {path}")
        return {}
    return value


def resolve_inside(root: Path, raw: Any, label: str, errors: list[str]) -> Path | None:
    if not isinstance(raw, str) or not raw:
        errors.append(f"{label} must be a non-empty path string")
        return None
    candidate = Path(raw)
    path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        errors.append(f"{label} escapes research root: {path}")
    return path


def is_nonnegative_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def require_object(value: Any, label: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return {}
    return value


def infer_control_mode(state: dict[str, Any]) -> str:
    session = state.get("autonomy_session")
    if isinstance(session, dict):
        explicit = session.get("control_mode")
        if isinstance(explicit, str) and explicit in CONTROL_MODES:
            return explicit
    research = state.get("research_runtime")
    if isinstance(research, dict) and research.get("state") == "READY":
        values = (
            research.get("next_action"),
            research.get("pause_marker"),
            research.get("stop_reason"),
            research.get("status"),
            state.get("state"),
        )
        for value in values:
            if not isinstance(value, str):
                continue
            marker = value.strip().upper()
            if marker in LEGACY_USER_PAUSE_MARKERS or (
                "AWAIT" in marker and "USER" in marker and "RESUME" in marker
            ):
                return "PAUSED_USER"
    return "RUNNING"


def validate_control(
    state: dict[str, Any],
    session: dict[str, Any],
    research: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> str:
    raw = session.get("control_mode")
    if raw is None:
        mode = infer_control_mode(state)
        if mode == "PAUSED_USER":
            warnings.append(
                "legacy READY/user-resume marker inferred control_mode=PAUSED_USER"
            )
    elif raw not in CONTROL_MODES:
        errors.append("autonomy_session.control_mode is invalid")
        mode = "RUNNING"
    else:
        mode = raw

    latch = session.get("user_pause_latch")
    if latch is not None and not isinstance(latch, bool):
        errors.append("autonomy_session.user_pause_latch must be boolean")
    if mode == "RUNNING" and latch is True:
        errors.append("RUNNING control_mode cannot retain user_pause_latch")
    if mode == "PAUSED_USER" and latch is False and raw is not None:
        warnings.append("PAUSED_USER control_mode has a cleared user_pause_latch")

    saved = session.get("saved_next_action")
    if saved is not None and (not isinstance(saved, str) or not saved):
        errors.append("autonomy_session.saved_next_action must be null or non-empty")
    if mode in PAUSED_CONTROL_MODES:
        active = research.get("active_luna_tasks")
        if not isinstance(active, list):
            errors.append("paused runtime must expose active_luna_tasks for draining")
        marker = research.get("next_action")
        if saved is None and isinstance(marker, str) and marker.lower().startswith("await_"):
            warnings.append(
                "paused runtime has no saved_next_action; resume will use the safe default"
            )
    if mode == "TERMINAL":
        if research.get("active_luna_tasks"):
            errors.append("TERMINAL control_mode cannot retain active Luna tasks")
        if research.get("state") not in {
            "PROJECT_COMPLETE",
            "PROJECT_INFEASIBLE",
            "INTEGRITY_STOP",
            "GLOBAL_RESOURCE_EXHAUSTED",
        }:
            errors.append(
                "TERMINAL control_mode requires a terminal research_runtime.state"
            )
    if raw in CONTROL_MODES and mode == "RUNNING":
        marker = research.get("next_action")
        if isinstance(marker, str) and (
            marker.upper() in LEGACY_USER_PAUSE_MARKERS
            or ("AWAIT" in marker.upper() and "USER" in marker.upper() and "RESUME" in marker.upper())
        ):
            errors.append("RUNNING control_mode conflicts with a user-resume marker")
    return mode


def rollover_threshold(brain: dict[str, Any], warnings: list[str]) -> int:
    raw = brain.get("rollover_threshold")
    if "rollover_after_observable_compactions" in brain:
        warnings.append(
            "legacy rollover_after_observable_compactions is ignored; "
            "set explicit brain_runtime.rollover_threshold"
        )
    if raw is None:
        return DEFAULT_ROLLOVER_THRESHOLD
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return DEFAULT_ROLLOVER_THRESHOLD
    return raw


def validate_transport_policy(session: dict[str, Any], errors: list[str]) -> None:
    policy = session.get("transport_policy")
    if policy is not None and not isinstance(policy, dict):
        errors.append("autonomy_session.transport_policy must be an object")
        return
    if not isinstance(policy, dict):
        return
    for key in (
        "native_luna_subagents",
        "native_subagents",
        "allow_native_luna",
        "external_network",
        "authenticated_external_requests",
        "allow_external_requests",
        "normal_network_operations",
    ):
        if key in policy and not isinstance(policy[key], bool):
            errors.append(f"autonomy_session.transport_policy.{key} must be boolean")
    for key in ("native_override", "native_luna_transport_override"):
        if key in policy:
            value = policy[key]
            if isinstance(value, dict):
                if "enabled" in value and not isinstance(value["enabled"], bool):
                    errors.append(
                        f"autonomy_session.transport_policy.{key}.enabled must be boolean"
                    )
            elif not isinstance(value, bool):
                errors.append(f"autonomy_session.transport_policy.{key} must be boolean or object")
    nested = policy.get("luna")
    if nested is None:
        nested = policy.get("luna_worker")
    if nested is None or isinstance(nested, bool):
        candidate = policy.get("native_luna")
        if isinstance(candidate, dict):
            nested = candidate
    if nested is not None and not isinstance(nested, dict):
        errors.append("autonomy_session.transport_policy.luna must be an object")
    elif isinstance(nested, dict):
        for key in (
            "native_luna_subagents",
            "native_subagents",
            "allow_native",
            "enabled",
        ):
            if key in nested and not isinstance(nested[key], bool):
                errors.append(
                    f"autonomy_session.transport_policy.luna.{key} must be boolean"
                )


def validate_budget(session: dict[str, Any], errors: list[str]) -> None:
    limits = require_object(session.get("global_limits"), "autonomy_session.global_limits", errors)
    used = require_object(
        session.get("global_budget_used"), "autonomy_session.global_budget_used", errors
    )
    remaining = require_object(
        session.get("global_budget_remaining"),
        "autonomy_session.global_budget_remaining",
        errors,
    )
    raw_overrun = session.get("budget_overrun")
    overrun = raw_overrun is not None
    if overrun:
        if not isinstance(raw_overrun, dict) or raw_overrun.get("status") != "overrun":
            errors.append("autonomy_session.budget_overrun must be an overrun object")
            overrun = False
        elif not isinstance(raw_overrun.get("dimensions"), list) or not raw_overrun.get(
            "dimensions"
        ):
            errors.append("budget_overrun.dimensions must be a non-empty list")
    for key in BUDGET_KEYS:
        limit = limits.get(key)
        current = used.get(key)
        rest = remaining.get(key)
        if not is_nonnegative_number(limit) or not is_nonnegative_number(current):
            errors.append(f"budget dimension {key} must contain nonnegative limits/use")
            continue
        if not isinstance(rest, (int, float)) or isinstance(rest, bool) or (
            rest < 0 and not overrun
        ):
            errors.append(f"budget dimension {key} remaining is invalid")
            continue
        if used[key] > limits[key]:
            if not overrun:
                errors.append(f"budget used exceeds global limit for {key}")
        expected = limits[key] - used[key]
        if abs(float(remaining[key]) - float(expected)) > 1e-9:
            errors.append(f"budget remaining is inconsistent for {key}")

    reservations = session.get("budget_reservations")
    reserved_totals = {key: 0 for key in BUDGET_KEYS}
    if reservations is not None and not isinstance(reservations, dict):
        errors.append("autonomy_session.budget_reservations must be an object")
        reservations = {}
    if isinstance(reservations, dict):
        for reservation_id, reservation in reservations.items():
            if not isinstance(reservation, dict):
                errors.append(f"budget reservation {reservation_id} must be an object")
                continue
            status = reservation.get("status", "reserved")
            if status not in {"reserved", "finalized"}:
                errors.append(f"budget reservation {reservation_id} has invalid status")
                continue
            amounts = reservation.get("reserved")
            if not isinstance(amounts, dict):
                errors.append(f"budget reservation {reservation_id}.reserved must be an object")
                continue
            for key in BUDGET_KEYS:
                value = amounts.get(key)
                if not is_nonnegative_number(value):
                    errors.append(
                        f"budget reservation {reservation_id}.{key} must be nonnegative"
                    )
                elif status == "reserved":
                    reserved_totals[key] += value
            if status == "finalized":
                settled = reservation.get("settled")
                if settled is not None and not isinstance(settled, dict):
                    errors.append(
                        f"budget reservation {reservation_id}.settled must be an object"
                    )
    declared_reserved = session.get("global_budget_reserved")
    if declared_reserved is not None:
        if not isinstance(declared_reserved, dict):
            errors.append("autonomy_session.global_budget_reserved must be an object")
        else:
            for key in BUDGET_KEYS:
                if not is_nonnegative_number(declared_reserved.get(key)):
                    errors.append(
                        f"global_budget_reserved.{key} must be nonnegative"
                    )
                elif abs(float(declared_reserved[key]) - float(reserved_totals[key])) > 1e-9:
                    errors.append(f"global_budget_reserved is inconsistent for {key}")
    available = session.get("global_budget_available")
    if available is not None:
        if not isinstance(available, dict):
            errors.append("autonomy_session.global_budget_available must be an object")
        else:
            for key in BUDGET_KEYS:
                if (
                    not isinstance(available.get(key), (int, float))
                    or isinstance(available.get(key), bool)
                    or (available.get(key) < 0 and not overrun)
                ):
                    errors.append(
                        f"global_budget_available.{key} is invalid"
                    )
                elif is_nonnegative_number(limits.get(key)) and is_nonnegative_number(
                    used.get(key)
                ):
                    expected = limits[key] - used[key] - reserved_totals[key]
                    if abs(float(available[key]) - float(expected)) > 1e-9:
                        errors.append(f"global_budget_available is inconsistent for {key}")
    for key in BUDGET_KEYS:
        if (
            is_nonnegative_number(limits.get(key))
            and is_nonnegative_number(used.get(key))
            and used[key] + reserved_totals[key] > limits[key]
            and not overrun
        ):
            errors.append(f"used plus active reservations exceeds global limit for {key}")


def validate_routes(research: dict[str, Any], adapter: dict[str, Any], errors: list[str]) -> None:
    active = research.get("active_route_ids")
    routes = research.get("route_states")
    if not isinstance(active, list) or not all(isinstance(item, str) for item in active):
        errors.append("research_runtime.active_route_ids must be a list of strings")
        active = []
    if len(active) != len(set(active)):
        errors.append("research_runtime.active_route_ids contains duplicates")
    if not isinstance(routes, dict):
        errors.append("research_runtime.route_states must be an object")
        routes = {}
    maximum = adapter.get("portfolio_policy", {}).get("max_active_routes")
    if isinstance(maximum, int) and len(active) > maximum:
        errors.append("active route count exceeds adapter maximum")
    for route_id, route in routes.items():
        if not isinstance(route, dict):
            errors.append(f"route {route_id} must be an object")
            continue
        if route.get("status") not in ROUTE_STATUS:
            errors.append(f"route {route_id} has invalid status")
        if route.get("depth") not in ROUTE_DEPTH:
            errors.append(f"route {route_id} has invalid depth")
    for route_id in active:
        route = routes.get(route_id)
        if not isinstance(route, dict) or route.get("status") != "active":
            errors.append(f"active route {route_id} lacks matching active state")


def validate_brain_lineage(brain: dict[str, Any], errors: list[str]) -> None:
    raw = brain.get("brain_task_lineage")
    if raw is None:
        return
    if not isinstance(raw, list):
        errors.append("brain_runtime.brain_task_lineage must be a list")
        return
    if not raw:
        return

    entries: list[dict[str, Any]] = []
    generations: list[int] = []
    task_ids: list[str] = []
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            errors.append(f"brain_task_lineage[{index}] must be an object")
            continue
        entries.append(value)
        generation = value.get("generation")
        if not isinstance(generation, int) or generation < 1:
            errors.append(f"brain_task_lineage[{index}].generation is invalid")
        else:
            generations.append(generation)
        task_id = value.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            errors.append(f"brain_task_lineage[{index}].task_id must be set")
        else:
            task_ids.append(task_id)
        if value.get("status") not in LINEAGE_STATUS:
            errors.append(f"brain_task_lineage[{index}].status is invalid")
        for key in ("predecessor_task_id", "successor_task_id"):
            linked = value.get(key)
            if linked is not None and (not isinstance(linked, str) or not linked):
                errors.append(f"brain_task_lineage[{index}].{key} is invalid")

    if len(generations) != len(set(generations)):
        errors.append("brain_task_lineage contains duplicate generations")
    if len(task_ids) != len(set(task_ids)):
        errors.append("brain_task_lineage contains duplicate task IDs")

    active = [entry for entry in entries if entry.get("status") == "active"]
    if len(active) != 1:
        errors.append("brain_task_lineage must contain exactly one active Brain")
    elif (
        active[0].get("generation") != brain.get("active_brain_generation")
        or active[0].get("task_id") != brain.get("active_brain_task_id")
    ):
        errors.append("active brain_task_lineage entry does not match brain_runtime")

    known_tasks = set(task_ids)
    for index, entry in enumerate(entries):
        predecessor = entry.get("predecessor_task_id")
        successor = entry.get("successor_task_id")
        if predecessor is not None and predecessor not in known_tasks:
            errors.append(f"brain_task_lineage[{index}] has unknown predecessor")
        if successor is not None and successor not in known_tasks:
            errors.append(f"brain_task_lineage[{index}] has unknown successor")
        if entry.get("status") == "retired" and not successor:
            errors.append(f"brain_task_lineage[{index}] retired without successor")
        if entry.get("status") == "active" and successor is not None:
            errors.append(f"brain_task_lineage[{index}] active with successor")


def validate_state(
    root: Path,
    adapter: dict[str, Any],
    state: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> None:
    if state.get("schema_version") != 4:
        errors.append("pipeline_state schema_version must be 4")
    if state.get("project_id") != adapter.get("project_id"):
        errors.append("pipeline_state project_id does not match adapter")

    session = require_object(state.get("autonomy_session"), "autonomy_session", errors)
    if not isinstance(session.get("session_id"), str) or not session.get("session_id"):
        errors.append("autonomy_session.session_id must be set")
    if not isinstance(session.get("enabled"), bool):
        errors.append("autonomy_session.enabled must be boolean")
    if not isinstance(session.get("project_goal"), str) or not session.get("project_goal"):
        errors.append("autonomy_session.project_goal must be set")
    if session.get("goal_status") not in GOAL_STATUS:
        errors.append("autonomy_session.goal_status is invalid")
    allowed = require_object(session.get("allowed"), "autonomy_session.allowed", errors)
    forbidden = require_object(session.get("forbidden"), "autonomy_session.forbidden", errors)
    for label, values in (("allowed", allowed), ("forbidden", forbidden)):
        for key, value in values.items():
            if not isinstance(value, bool):
                errors.append(f"autonomy_session.{label}.{key} must be boolean")
    validate_transport_policy(session, errors)
    validate_budget(session, errors)

    brain = require_object(state.get("brain_runtime"), "brain_runtime", errors)
    generation = brain.get("active_brain_generation")
    if not isinstance(generation, int) or generation < 1:
        errors.append("brain_runtime.active_brain_generation must be a positive integer")
    if brain.get("context_health") not in CONTEXT_HEALTH:
        errors.append("brain_runtime.context_health is invalid")
    threshold = rollover_threshold(brain, warnings)
    for threshold_key in ("rollover_threshold",):
        if threshold_key in brain and (
            isinstance(brain[threshold_key], bool)
            or not isinstance(brain[threshold_key], int)
            or brain[threshold_key] < 1
        ):
            errors.append(f"brain_runtime.{threshold_key} must be a positive integer")
    raw_compaction_count = brain.get("context_compaction_count", 0)
    if "observable_context_compactions" in brain and "context_compaction_count" not in brain:
        warnings.append(
            "legacy observable_context_compactions is not promoted into the current "
            "Brain generation counter"
        )
    compaction_count = raw_compaction_count
    if (
        not isinstance(compaction_count, int)
        or isinstance(compaction_count, bool)
        or compaction_count < 0
    ):
        errors.append("brain_runtime.context_compaction_count must be nonnegative")
        compaction_count = 0
    compaction_events = brain.get("context_compaction_event_ids", [])
    if not isinstance(compaction_events, list) or not all(
        isinstance(value, str) and value for value in compaction_events
    ):
        errors.append(
            "brain_runtime.context_compaction_event_ids must be a list of non-empty strings"
        )
        compaction_events = []
    if len(compaction_events) != len(set(compaction_events)):
        errors.append("brain_runtime.context_compaction_event_ids contains duplicates")
    if len(compaction_events) > compaction_count:
        errors.append("recorded compaction event IDs exceed compaction count")
    compaction_floor = (
        "rollover_required"
        if compaction_count >= threshold
        else "rollover_recommended"
        if compaction_count == 1
        else "healthy"
    )
    context_health = brain.get("context_health")
    if (
        context_health in CONTEXT_HEALTH
        and CONTEXT_HEALTH_RANK[context_health] < CONTEXT_HEALTH_RANK[compaction_floor]
    ):
        errors.append(
            f"context health must be at least {compaction_floor} after recorded compaction"
        )
    rollover_reason = brain.get("rollover_reason")
    if rollover_reason is not None and (
        not isinstance(rollover_reason, str) or not rollover_reason
    ):
        errors.append("brain_runtime.rollover_reason must be null or a non-empty string")
    if (
        compaction_count >= threshold
        and not isinstance(rollover_reason, str)
        and brain.get("handoff_status") not in {"transferred", "platform_blocked"}
    ):
        errors.append("rollover threshold reached without a rollover reason")
    if brain.get("handoff_status") not in HANDOFF_STATUS:
        errors.append("brain_runtime.handoff_status is invalid")
    if not isinstance(brain.get("rollover_count"), int) or brain.get("rollover_count", -1) < 0:
        errors.append("brain_runtime.rollover_count must be nonnegative")
    validate_brain_lineage(brain, errors)
    pending = brain.get("pending_successor_generation")
    if brain.get("handoff_status") == "prepared":
        if pending != generation + 1:
            errors.append("prepared rollover must target exactly the next generation")
        if brain.get("context_health") != "rollover_required":
            errors.append("prepared rollover requires rollover_required context health")
    elif pending is not None:
        errors.append("pending successor generation is allowed only while handoff is prepared")

    research = require_object(state.get("research_runtime"), "research_runtime", errors)
    validate_control(state, session, research, errors, warnings)
    if research.get("state") not in STATES:
        errors.append("research_runtime.state is invalid")
    if not isinstance(research.get("stage_count"), int) or research.get("stage_count", -1) < 0:
        errors.append("research_runtime.stage_count must be nonnegative")
    validate_routes(research, adapter, errors)
    active_experiments = research.get("active_experiment_ids")
    completed_experiments = research.get("completed_experiment_ids")
    if not isinstance(active_experiments, list):
        errors.append("research_runtime.active_experiment_ids must be a list")
        active_experiments = []
    if not isinstance(completed_experiments, list):
        errors.append("research_runtime.completed_experiment_ids must be a list")
        completed_experiments = []
    if set(active_experiments).intersection(completed_experiments):
        errors.append("an experiment cannot be active and completed simultaneously")
    if not isinstance(research.get("active_luna_tasks"), list):
        errors.append("research_runtime.active_luna_tasks must be a list")

    handoff_path = resolve_inside(root, brain.get("handoff_path"), "brain_runtime.handoff_path", errors)
    if brain.get("handoff_status") in {"prepared", "transferred"} and handoff_path:
        if not handoff_path.is_file():
            errors.append("rollover state requires the compact brain handoff file")


def main() -> int:
    args = parse_args()
    root = Path(args.root).expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    if not root.is_dir():
        errors.append(f"research root is not an existing directory: {root}")

    adapter_path = root / "research_pipeline.yaml"
    adapter = load_object(adapter_path, errors)
    if adapter.get("schema_version") != 4:
        errors.append("research_pipeline schema_version must be 4")
    configured_root = adapter.get("research_root")
    if isinstance(configured_root, str) and Path(configured_root).resolve() != root:
        errors.append("research_pipeline research_root does not match --root")

    required_adapter = {
        "project_id",
        "project_title",
        "research_root",
        "source_precedence",
        "artifact_system",
        "coordination",
        "portfolio_policy",
        "verification",
        "models",
    }
    missing = sorted(required_adapter.difference(adapter))
    if missing:
        errors.append(f"research_pipeline missing keys: {missing}")

    verification = adapter.get("verification", {})
    if not isinstance(verification, dict):
        errors.append("verification must be an object")
    else:
        if verification.get("cryptographic_provenance") != "user_explicit_only":
            errors.append("cryptographic provenance must be user-explicit only")
        if verification.get("global_audit_in_normal_loop") is not False:
            errors.append("normal-loop global audit must be disabled")

    state_path = resolve_inside(
        root,
        adapter.get("artifact_system", {}).get("pipeline_state")
        if isinstance(adapter.get("artifact_system"), dict)
        else None,
        "artifact_system.pipeline_state",
        errors,
    )
    state = load_object(state_path, errors) if state_path else {}
    if state:
        validate_state(root, adapter, state, errors, warnings)

    for key in ("normative_plan", "evidence_record", "status_record"):
        sources = adapter.get("source_precedence", {})
        path = resolve_inside(
            root,
            sources.get(key) if isinstance(sources, dict) else None,
            f"source_precedence.{key}",
            errors,
        )
        if path and not path.is_file():
            warnings.append(f"authoritative record is missing: {path}")

    result = {
        "root": str(root),
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "warnings": warnings,
    }
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"status: {result['status']}")
        for warning in warnings:
            print(f"WARNING: {warning}")
        for error in errors:
            print(f"ERROR: {error}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
