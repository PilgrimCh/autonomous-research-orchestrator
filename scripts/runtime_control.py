from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


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
    value = brain.get("context_compaction_count", 0)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeErrorWithCode(
            "brain_runtime.context_compaction_count must be a nonnegative integer"
        )
    return value


def context_health_floor(compactions: int) -> str:
    if compactions >= 2:
        return "rollover_required"
    if compactions == 1:
        return "rollover_recommended"
    return "healthy"


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
    root = Path(root_raw).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeErrorWithCode(f"research root is not a directory: {root}")
    state_path = state_path_for(root)
    state = load_object(state_path)
    if state.get("schema_version") != 4:
        raise RuntimeErrorWithCode("runtime_control requires schema version 4")
    return root, state_path, state


def command_assert(args: argparse.Namespace) -> dict[str, Any]:
    _, _, state = load_runtime(args.root)
    brain = assert_active(state, args.generation, args.task_id)
    return {
        "status": "active",
        "generation": brain["active_brain_generation"],
        "task_id": brain.get("active_brain_task_id"),
    }


def command_set_context(args: argparse.Namespace) -> dict[str, Any]:
    _, state_path, state = load_runtime(args.root)
    brain = assert_active(state, args.generation, args.task_id)
    current = brain.get("context_health", "healthy")
    if current not in CONTEXT_HEALTH:
        raise RuntimeErrorWithCode("brain_runtime.context_health is invalid")
    floor = context_health_floor(context_compaction_count(brain))
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
    _, state_path, state = load_runtime(args.root)
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
    floor = context_health_floor(count)
    current = brain.get("context_health", "healthy")
    if current not in CONTEXT_HEALTH:
        raise RuntimeErrorWithCode("brain_runtime.context_health is invalid")
    if CONTEXT_HEALTH_RANK[current] < CONTEXT_HEALTH_RANK[floor]:
        brain["context_health"] = floor
    if count >= 2:
        brain["rollover_reason"] = "second_context_compaction"
        research = state.get("research_runtime")
        if isinstance(research, dict):
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
        "rollover_required": count >= 2,
        "rollover_reason": brain.get("rollover_reason"),
    }


def command_prepare(args: argparse.Namespace) -> dict[str, Any]:
    root, state_path, state = load_runtime(args.root)
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
    active_luna = research.get("active_luna_tasks", []) if isinstance(research, dict) else []
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
        raise RuntimeErrorWithCode("write the compact brain handoff before preparing rollover")
    if brain.get("handoff_status") == "prepared":
        raise RuntimeErrorWithCode("a successor Brain is already pending")

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
    }


def command_transfer(args: argparse.Namespace) -> dict[str, Any]:
    _, state_path, state = load_runtime(args.root)
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
    }


def command_platform_blocked(args: argparse.Namespace) -> dict[str, Any]:
    _, state_path, state = load_runtime(args.root)
    brain = assert_active(state, args.generation, args.task_id)
    if brain.get("handoff_status") != "prepared":
        raise RuntimeErrorWithCode("prepare rollover before marking platform blocked")
    brain["handoff_status"] = "platform_blocked"
    brain["pending_successor_generation"] = None
    research = state["research_runtime"]
    research["state"] = "ROLLOVER_PLATFORM_BLOCKED"
    research["next_action"] = "report_native_thread_creation_limitation"
    atomic_write(state_path, state)
    return {"status": "platform_blocked", "generation": args.generation}


def command_record_budget(args: argparse.Namespace) -> dict[str, Any]:
    _, state_path, state = load_runtime(args.root)
    assert_active(state, args.generation, args.task_id)
    increments: dict[str, int | float] = {
        "wall_minutes": args.wall_minutes,
        "worker_assignments": args.worker_assignments,
        "external_calls": args.external_calls,
        "external_cost_usd": args.external_cost_usd,
    }
    if any(value < 0 for value in increments.values()):
        raise RuntimeErrorWithCode("budget increments must be nonnegative")
    session = state["autonomy_session"]
    limits = session["global_limits"]
    used = session["global_budget_used"]
    projected = {key: used[key] + increments[key] for key in BUDGET_KEYS}
    exceeded = [key for key in BUDGET_KEYS if projected[key] > limits[key]]
    if exceeded:
        raise RuntimeErrorWithCode(
            "budget update would exceed session limits: " + ", ".join(exceeded), code=8
        )
    session["global_budget_used"] = projected
    session["global_budget_remaining"] = {
        key: limits[key] - projected[key] for key in BUDGET_KEYS
    }
    atomic_write(state_path, state)
    return {
        "status": "recorded",
        "used": projected,
        "remaining": session["global_budget_remaining"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Atomic ownership and budget operations for schema-v4 research runtime."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def ownership(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--root", required=True)
        subparser.add_argument("--generation", type=int, required=True)
        subparser.add_argument("--task-id")

    check = subparsers.add_parser("assert-active")
    ownership(check)
    check.set_defaults(handler=command_assert)

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
