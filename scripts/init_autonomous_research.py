from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parent.parent
ASSETS = SKILL_ROOT / "assets"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initialize or migrate a minimal schema-v4 autonomous research runtime."
    )
    parser.add_argument("--root", required=True, help="Existing research project root.")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--goal", help="Final project-level research goal.")
    parser.add_argument("--enable-autonomy", action="store_true")
    parser.add_argument("--migrate", action="store_true", help="Migrate schema v2/v3 state.")
    parser.add_argument("--brain-task-id")
    parser.add_argument("--wall-minutes", type=int, default=240)
    parser.add_argument("--worker-assignments", type=int, default=8)
    parser.add_argument("--external-calls", type=int, default=0)
    parser.add_argument("--external-cost-usd", type=float, default=0.0)
    parser.add_argument("--public-read-only-web", action="store_true")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def nonnegative(name: str, value: int | float) -> None:
    if value < 0:
        raise ValueError(f"{name} must be nonnegative")


def legacy_used(old_state: dict[str, Any]) -> dict[str, int | float]:
    old = old_state.get("budget_used", {})
    if not isinstance(old, dict):
        old = {}
    return {
        "wall_minutes": max(0, int(old.get("wall_minutes", 0) or 0)),
        "worker_assignments": max(0, int(old.get("worker_assignments", 0) or 0)),
        "external_calls": max(0, int(old.get("external_calls", 0) or 0)),
        "external_cost_usd": max(0.0, float(old.get("external_cost_usd", 0.0) or 0.0)),
    }


def limits_and_remaining(
    args: argparse.Namespace, used: dict[str, int | float]
) -> tuple[dict[str, int | float], dict[str, int | float]]:
    requested: dict[str, int | float] = {
        "wall_minutes": args.wall_minutes,
        "worker_assignments": args.worker_assignments,
        "external_calls": args.external_calls,
        "external_cost_usd": args.external_cost_usd,
    }
    for key, value in requested.items():
        nonnegative(key, value)
    limits = {key: max(requested[key], used[key]) for key in requested}
    remaining = {key: limits[key] - used[key] for key in limits}
    return limits, remaining


def map_legacy_routes(old_state: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    active = old_state.get("active_route_ids", [])
    routes = old_state.get("route_states", {})
    return (
        list(active) if isinstance(active, list) else [],
        dict(routes) if isinstance(routes, dict) else {},
    )


def make_adapter(
    args: argparse.Namespace, root: Path, old_adapter: dict[str, Any]
) -> dict[str, Any]:
    adapter = load_object(ASSETS / "research-pipeline-template.yaml")
    adapter["project_id"] = args.project_id
    adapter["project_title"] = args.title
    adapter["research_root"] = str(root)
    if isinstance(old_adapter.get("modality"), str):
        adapter["modality"] = old_adapter["modality"]
    return adapter


def make_state(
    args: argparse.Namespace,
    goal: str,
    old_adapter: dict[str, Any],
    old_state: dict[str, Any],
) -> dict[str, Any]:
    state = load_object(ASSETS / "pipeline-state-template.json")
    state["project_id"] = args.project_id

    used = legacy_used(old_state)
    limits, remaining = limits_and_remaining(args, used)
    session = state["autonomy_session"]
    session["session_id"] = f"{args.project_id}-session-001"
    session["enabled"] = bool(args.enable_autonomy)
    session["control_mode"] = "RUNNING" if args.enable_autonomy else "PAUSED_USER"
    session["project_goal"] = goal
    session["global_limits"] = limits
    session["global_budget_used"] = used
    session["global_budget_remaining"] = remaining
    session["allowed"]["public_read_only_web"] = bool(args.public_read_only_web)

    old_authorization = old_adapter.get("authorization", {})
    if isinstance(old_authorization, dict):
        standing = old_authorization.get("standing_external_authorization")
        if standing:
            session["standing_external_authorization"] = standing
            session["allowed"]["experimental_api"] = args.external_calls > 0

    old_network = old_adapter.get("network", {})
    if isinstance(old_network, dict) and old_network.get("public_read_only_allowed") is True:
        session["allowed"]["public_read_only_web"] = True

    old_project_state = old_state.get("state") or old_adapter.get("state")
    if old_project_state == "PROJECT_CLOSED":
        session["goal_status"] = "complete"
        session["control_mode"] = "TERMINAL"
    elif old_project_state in {"STOPPED_BY_USER", "PAUSED_USER"}:
        session["control_mode"] = "PAUSED_USER"

    brain = state["brain_runtime"]
    brain["active_brain_task_id"] = args.brain_task_id
    if args.brain_task_id:
        brain["brain_task_lineage"] = [
            {
                "generation": 1,
                "task_id": args.brain_task_id,
                "status": "active",
                "predecessor_task_id": None,
                "successor_task_id": None,
            }
        ]

    active_routes, route_states = map_legacy_routes(old_state)
    research = state["research_runtime"]
    research["active_stage_id"] = old_state.get("active_stage_id")
    research["stage_count"] = max(0, int(old_state.get("stage_count", 0) or 0))
    research["active_route_ids"] = active_routes
    research["route_states"] = route_states
    active_experiments = old_state.get("active_experiment_ids", [])
    research["active_experiment_ids"] = (
        list(active_experiments) if isinstance(active_experiments, list) else []
    )
    old_workers = old_state.get("active_luna_tasks", old_state.get("active_subagents", []))
    research["active_luna_tasks"] = list(old_workers) if isinstance(old_workers, list) else []
    if session["goal_status"] == "complete":
        research["state"] = "PROJECT_COMPLETE"
        research["next_action"] = "deliver_final_result"
    elif session["control_mode"] == "PAUSED_USER":
        research["state"] = "READY"
        research["next_action"] = "await_explicit_user_resume"
    elif args.enable_autonomy:
        research["state"] = "RUNNING" if research["active_luna_tasks"] else "READY"
        research["next_action"] = "inspect_active_work" if research["active_luna_tasks"] else "inspect_goal_gap"

    invalidated = old_state.get("invalidated_artifacts", [])
    anomalies = old_state.get("anomalies", [])
    integrity = state["integrity_runtime"]
    integrity["invalidated_artifacts"] = list(invalidated) if isinstance(invalidated, list) else []
    integrity["unresolved_issues"] = list(anomalies) if isinstance(anomalies, list) else []
    state["last_transition"] = old_state.get("last_transition")
    return state


def main_documents(goal: str, title: str) -> dict[str, str]:
    return {
        "task_plan.md": (
            f"# Research Plan: {title}\n\n"
            f"## Project goal\n\n{goal}\n\n"
            "## Stable formulation\n\nTo be refined from accepted evidence.\n\n"
            "## Important constraints\n\nUse the recorded autonomy-session boundaries.\n\n"
            "## Previous → current/next stage\n\nNo previous stage. Identify the highest-value goal gap.\n\n"
            "Keep only adjacent-stage context; full process and contributions belong in research_history.md.\n"
        ),
        "findings.md": (
            "# Research Findings\n\n"
            "## Accepted evidence and bounded conclusions\n\nNone recorded yet.\n\n"
            "## Evidence needed for the next stage\n\nDerive from the project goal.\n\n"
            "Older facts stay only when needed for the next decision; link full history.\n"
        ),
        "progress.md": (
            "# Research Progress\n\n"
            "## Current work\n\nInitialize the autonomous outer loop.\n\n"
            "## Previous stage and next action\n\nNone recorded yet.\n\n"
            "## Active route and resources\n\nSee `pipeline_state.json`.\n\n"
            "## True blockers\n\nNone.\n"
        ),
        "research_history.md": (
            f"# Research history: {title}\n\n"
            "No experimental work recorded yet. Existing work, if any, must be linked during adoption.\n\n"
            "After each work item, publish the actual question, motivation, methods, actions, outcomes, "
            "debug attempts, evidence boundaries, next decision, and user/agent/shared contribution attribution. "
            "This record supports traceability and evidence-backed CV descriptions; infrastructure success "
            "does not establish scientific impact. Do not load full history on ordinary resume.\n"
        ),
    }


def main() -> int:
    args = parse_args()
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"ERROR: research root is not an existing directory: {root}", file=sys.stderr)
        return 2

    adapter_path = root / "research_pipeline.yaml"
    state_path = root / "artifacts" / "orchestration" / "pipeline_state.json"
    adapter_exists = adapter_path.is_file()
    state_exists = state_path.is_file()

    old_adapter: dict[str, Any] = {}
    old_state: dict[str, Any] = {}
    if adapter_exists:
        try:
            old_adapter = load_object(adapter_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"ERROR: cannot read existing adapter: {exc}", file=sys.stderr)
            return 3
    if state_exists:
        try:
            old_state = load_object(state_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"ERROR: cannot read existing state: {exc}", file=sys.stderr)
            return 3

    if (adapter_exists or state_exists) and not args.migrate:
        print("ERROR: existing pipeline files require --migrate; nothing was changed.", file=sys.stderr)
        return 4
    if args.migrate and old_adapter.get("schema_version") == 4 and old_state.get("schema_version") == 4:
        print(json.dumps({"root": str(root), "status": "already-schema-v4", "apply": args.apply}, indent=2))
        return 0

    goal = args.goal or old_adapter.get("research_question")
    if not isinstance(goal, str) or not goal.strip() or goal == "unknown":
        print("ERROR: a concrete project-level --goal is required.", file=sys.stderr)
        return 5
    goal = goal.strip()

    docs = main_documents(goal, args.title)
    create_docs = [str(root / name) for name in docs if not (root / name).exists()]
    backups: list[str] = []
    if args.migrate:
        for path in (adapter_path, state_path):
            if path.is_file():
                backup = path.with_name(path.name + ".pre-autonomous-v4.bak")
                if backup.exists():
                    print(f"ERROR: migration backup already exists: {backup}", file=sys.stderr)
                    return 6
                backups.append(str(backup))

    plan = {
        "root": str(root),
        "mode": "migrate" if args.migrate else "initialize",
        "schema_version": 4,
        "create_documents": create_docs,
        "write_control_files": [str(adapter_path), str(state_path)],
        "backups": backups,
        "apply": bool(args.apply),
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if not args.apply:
        return 0

    if args.migrate:
        for path in (adapter_path, state_path):
            if path.is_file():
                shutil.copy2(path, path.with_name(path.name + ".pre-autonomous-v4.bak"))

    for name, content in docs.items():
        path = root / name
        if not path.exists():
            path.write_text(content, encoding="utf-8")

    adapter = make_adapter(args, root, old_adapter)
    state = make_state(args, goal, old_adapter, old_state)
    write_json(adapter_path, adapter)
    write_json(state_path, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
