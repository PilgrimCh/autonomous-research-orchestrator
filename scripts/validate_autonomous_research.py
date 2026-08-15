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
    for key in BUDGET_KEYS:
        values = (limits.get(key), used.get(key), remaining.get(key))
        if not all(is_nonnegative_number(value) for value in values):
            errors.append(f"budget dimension {key} must contain nonnegative numbers")
            continue
        if used[key] > limits[key]:
            errors.append(f"budget used exceeds global limit for {key}")
        expected = limits[key] - used[key]
        if abs(float(remaining[key]) - float(expected)) > 1e-9:
            errors.append(f"budget remaining is inconsistent for {key}")


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


def validate_state(root: Path, adapter: dict[str, Any], state: dict[str, Any], errors: list[str]) -> None:
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
    validate_budget(session, errors)

    brain = require_object(state.get("brain_runtime"), "brain_runtime", errors)
    generation = brain.get("active_brain_generation")
    if not isinstance(generation, int) or generation < 1:
        errors.append("brain_runtime.active_brain_generation must be a positive integer")
    if brain.get("context_health") not in CONTEXT_HEALTH:
        errors.append("brain_runtime.context_health is invalid")
    if brain.get("handoff_status") not in HANDOFF_STATUS:
        errors.append("brain_runtime.handoff_status is invalid")
    if not isinstance(brain.get("rollover_count"), int) or brain.get("rollover_count", -1) < 0:
        errors.append("brain_runtime.rollover_count must be nonnegative")
    validate_brain_lineage(brain, errors)
    pending = brain.get("pending_successor_generation")
    if brain.get("handoff_status") == "prepared":
        if pending != generation + 1:
            errors.append("prepared rollover must target exactly the next generation")
    elif pending is not None:
        errors.append("pending successor generation is allowed only while handoff is prepared")

    research = require_object(state.get("research_runtime"), "research_runtime", errors)
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
        validate_state(root, adapter, state, errors)

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
