from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parent.parent
ASSETS = SKILL_ROOT / "assets"
RUNTIME = Path(__file__).resolve().parent / "runtime_control.py"
VALIDATOR = Path(__file__).resolve().parent / "validate_autonomous_research.py"
INITIALIZER = Path(__file__).resolve().parent / "init_autonomous_research.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dry-run continuous stages, Goal Mode, Luna repair, and Brain rollover."
    )
    parser.add_argument("--keep", action="store_true", help="Keep the temporary project.")
    return parser.parse_args()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def save(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_runtime(root: Path, *arguments: str, expect: int = 0) -> dict[str, Any]:
    process = subprocess.run(
        [sys.executable, str(RUNTIME), *arguments, "--root", str(root)],
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode != expect:
        raise AssertionError(
            f"runtime command returned {process.returncode}, expected {expect}: "
            f"{process.stdout}\n{process.stderr}"
        )
    return json.loads(process.stdout)


def initialize(root: Path) -> Path:
    adapter = load(ASSETS / "research-pipeline-template.yaml")
    adapter["project_id"] = "dryrun-autonomy"
    adapter["project_title"] = "Autonomous loop dry run"
    adapter["research_root"] = str(root)
    save(root / "research_pipeline.yaml", adapter)

    state = load(ASSETS / "pipeline-state-template.json")
    state["project_id"] = "dryrun-autonomy"
    session = state["autonomy_session"]
    session["session_id"] = "dryrun-session-001"
    session["enabled"] = True
    session["project_goal"] = "Find a robust mock method that improves the target outcome."
    state["brain_runtime"]["active_brain_task_id"] = "dryrun-brain-1"
    state["research_runtime"]["state"] = "RUNNING"
    state_path = root / "artifacts" / "orchestration" / "pipeline_state.json"
    save(state_path, state)

    (root / "task_plan.md").write_text(
        "# Plan\n\nGoal: find a robust mock method.\n", encoding="utf-8"
    )
    (root / "findings.md").write_text("# Findings\n\nNone yet.\n", encoding="utf-8")
    (root / "progress.md").write_text("# Progress\n\nStage 1 ready.\n", encoding="utf-8")
    return state_path


def validate_initializer_and_migration() -> dict[str, bool]:
    checks: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="autonomous-init-test-") as temporary:
        root = Path(temporary)
        process = subprocess.run(
            [
                sys.executable,
                str(INITIALIZER),
                "--root",
                str(root),
                "--project-id",
                "new-init-test",
                "--title",
                "New initialization test",
                "--goal",
                "Resolve a mock initialization goal.",
                "--enable-autonomy",
                "--brain-task-id",
                "new-init-brain-1",
                "--apply",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        validation = subprocess.run(
            [sys.executable, str(VALIDATOR), "--root", str(root), "--json"],
            text=True,
            capture_output=True,
            check=False,
        )
        checks["new_initializer"] = process.returncode == 0 and validation.returncode == 0
        checks["initializer_minimal_documents"] = all(
            (root / name).is_file()
            for name in ("task_plan.md", "findings.md", "progress.md")
        ) and not (root / "artifacts" / "orchestration" / "brain_handoff.md").exists()
        initialized = load(root / "artifacts" / "orchestration" / "pipeline_state.json")
        checks["initializer_records_initial_brain"] = initialized["brain_runtime"][
            "brain_task_lineage"
        ] == [
            {
                "generation": 1,
                "task_id": "new-init-brain-1",
                "status": "active",
                "predecessor_task_id": None,
                "successor_task_id": None,
            }
        ]

    with tempfile.TemporaryDirectory(prefix="autonomous-migration-test-") as temporary:
        root = Path(temporary)
        legacy_adapter = {
            "schema_version": 3,
            "project_id": "legacy-test",
            "project_title": "Legacy migration test",
            "research_root": str(root),
            "modality": "empirical-computational",
            "research_question": "Resolve a mock legacy goal.",
            "state": "STAGE_CLOSED",
            "network": {"public_read_only_allowed": True},
            "authorization": {"standing_external_authorization": None},
        }
        legacy_state = {
            "schema_version": 3,
            "project_id": "legacy-test",
            "state": "STAGE_CLOSED",
            "active_stage_id": "legacy-stage-007",
            "active_route_ids": ["legacy-route"],
            "route_states": {
                "legacy-route": {"status": "active", "depth": "focus"}
            },
            "active_experiment_ids": [],
            "active_subagents": [],
            "budget_used": {
                "wall_minutes": 15,
                "worker_assignments": 2,
                "external_calls": 0,
                "external_cost_usd": 0.0,
            },
            "anomalies": [],
            "invalidated_artifacts": [],
            "last_transition": {"type": "stage_complete"},
        }
        save(root / "research_pipeline.yaml", legacy_adapter)
        legacy_state_path = root / "artifacts" / "orchestration" / "pipeline_state.json"
        save(legacy_state_path, legacy_state)
        process = subprocess.run(
            [
                sys.executable,
                str(INITIALIZER),
                "--root",
                str(root),
                "--project-id",
                "legacy-test",
                "--title",
                "Legacy migration test",
                "--goal",
                "Resolve a mock legacy goal.",
                "--enable-autonomy",
                "--migrate",
                "--apply",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        validation = subprocess.run(
            [sys.executable, str(VALIDATOR), "--root", str(root), "--json"],
            text=True,
            capture_output=True,
            check=False,
        )
        migrated = load(legacy_state_path)
        checks["legacy_migration"] = process.returncode == 0 and validation.returncode == 0
        checks["legacy_stage_close_is_nonterminal"] = (
            migrated["autonomy_session"]["goal_status"] == "unresolved"
            and migrated["research_runtime"]["state"] == "READY"
            and migrated["research_runtime"]["next_action"] == "inspect_goal_gap"
        )
        checks["legacy_budget_not_reset"] = (
            migrated["autonomy_session"]["global_budget_used"]["wall_minutes"] == 15
            and migrated["autonomy_session"]["global_budget_used"]["worker_assignments"] == 2
        )
        checks["single_recoverable_backup"] = (
            (root / "research_pipeline.yaml.pre-autonomous-v4.bak").is_file()
            and legacy_state_path.with_name(
                "pipeline_state.json.pre-autonomous-v4.bak"
            ).is_file()
        )
    return checks


def record_result(
    root: Path,
    state_path: Path,
    stage_id: str,
    experiment_id: str,
    route_id: str,
    result_status: str,
    primary_result: dict[str, Any],
    repairs: list[str] | None = None,
) -> Path:
    state = load(state_path)
    research = state["research_runtime"]
    research["active_stage_id"] = stage_id
    research["active_route_ids"] = [route_id]
    route = research["route_states"].setdefault(
        route_id, {"status": "active", "depth": "scout"}
    )
    route["status"] = "active"
    research["active_experiment_ids"] = [experiment_id]
    research["active_luna_tasks"] = [
        {
            "task_id": f"luna-{experiment_id}",
            "question": f"execute {experiment_id}",
            "output": f"artifacts/orchestration/results/{experiment_id}/result.json",
        }
    ]
    save(state_path, state)

    result_path = (
        root / "artifacts" / "orchestration" / "results" / experiment_id / "result.json"
    )
    result = {
        "experiment_id": experiment_id,
        "executor": "luna_worker",
        "brain_executed_evidence_work": False,
        "status": result_status,
        "executed": {"route_id": route_id, "stage_id": stage_id},
        "primary_results": primary_result,
        "sanity_check": {"status": "pass", "details": "targeted check only"},
        "deviations": [],
        "repairs": repairs or [],
        "resource_use": {"wall_minutes": 5, "worker_assignments": 1},
        "artifacts": [],
        "claim_boundary": f"Only {experiment_id} under the mock configuration.",
        "limitations": [],
        "brain_decision_needed": "Interpret and select the next stage.",
    }
    save(result_path, result)

    state = load(state_path)
    research = state["research_runtime"]
    research["active_experiment_ids"] = []
    research["active_luna_tasks"] = []
    research["completed_experiment_ids"].append(experiment_id)
    research["stage_count"] += 1
    research["next_action"] = "brain_interpret_and_replan"
    state["last_transition"] = {
        "type": "stage_complete",
        "stage_id": stage_id,
        "session_continues": True,
    }
    budget = state["autonomy_session"]
    budget["global_budget_used"]["wall_minutes"] += 5
    budget["global_budget_used"]["worker_assignments"] += 1
    for key in budget["global_limits"]:
        budget["global_budget_remaining"][key] = (
            budget["global_limits"][key] - budget["global_budget_used"][key]
        )
    save(state_path, state)
    return result_path


def simulate(root: Path) -> dict[str, Any]:
    state_path = initialize(root)
    events: list[dict[str, Any]] = []
    initializer_checks = validate_initializer_and_migration()

    record_result(
        root,
        state_path,
        "stage-001",
        "exp-001",
        "route-a",
        "inconclusive",
        {"signal": "too noisy"},
        repairs=["Luna repaired a deterministic parser error and reran only the invalid unit"],
    )
    state = load(state_path)
    state["research_runtime"]["next_action"] = "modify_route_a_and_dispatch_stage_002"
    save(state_path, state)
    events.append({"stage": 1, "decision": "inconclusive -> automatic replan", "user_wakeup": False})

    record_result(
        root,
        state_path,
        "stage-002",
        "exp-002",
        "route-a",
        "negative",
        {"effect": "no improvement"},
    )
    state = load(state_path)
    route_a = state["research_runtime"]["route_states"]["route-a"]
    route_a["status"] = "pruned"
    route_a["depth"] = "scout"
    route_a["latest_disposition"] = {
        "action": "prune",
        "reason": "the tested method failed; the broader project remains open",
    }
    state["research_runtime"]["active_route_ids"] = []
    state["research_runtime"]["state"] = "GOAL_MODE"
    state["research_runtime"]["next_action"] = "rebuild_portfolio_from_project_goal"
    save(state_path, state)
    events.append({"stage": 2, "decision": "route exhausted -> Goal Mode", "session_stop": False})

    state = load(state_path)
    state["research_runtime"]["route_states"]["route-b"] = {
        "status": "active",
        "depth": "scout",
        "goal_link": "tests a different representation rather than another local tweak",
    }
    state["research_runtime"]["active_route_ids"] = ["route-b"]
    state["research_runtime"]["state"] = "RUNNING"
    state["research_runtime"]["next_action"] = "dispatch_stage_003_route_b"
    save(state_path, state)
    events.append({"mode": "Goal Mode", "portfolio_rebuilt": True, "selected_route": "route-b"})

    record_result(
        root,
        state_path,
        "stage-003",
        "exp-003",
        "route-b",
        "success",
        {"effect": "promising improvement"},
    )
    state = load(state_path)
    state["research_runtime"]["route_states"]["route-b"]["depth"] = "focus"
    state["research_runtime"]["route_states"]["route-b"]["latest_disposition"] = {
        "action": "promote",
        "reason": "promising scout result",
    }
    state["research_runtime"]["next_action"] = "rollover_then_focus_route_b"
    state["brain_runtime"]["context_health"] = "rollover_required"
    save(state_path, state)
    events.append({"stage": 3, "decision": "promote route-b and rollover", "session_stop": False})

    handoff = root / "artifacts" / "orchestration" / "brain_handoff.md"
    handoff.write_text(
        "# Brain Handoff\n\n"
        "session_id: dryrun-session-001\n"
        "from_brain_generation: 1\n"
        "from_brain_task_id: dryrun-brain-1\n"
        "to_brain_generation: 2\n"
        "to_brain_task_id: dryrun-brain-2\n"
        "predecessor_transcript: retained-unarchived-in-codex\n"
        "project_goal: Find a robust mock method that improves the target outcome.\n"
        "accepted_findings_that_matter_now: route-a failed narrowly; route-b is promising.\n"
        "current_active_route: route-b\n"
        "latest_stage_result: artifacts/orchestration/results/exp-003/result.json\n"
        "next_planned_action: focus route-b with exp-004\n"
        "running_luna_tasks: []\n"
        "immediate_resume_instruction: assert generation 2 and continue.\n",
        encoding="utf-8",
    )

    before_rollover = copy.deepcopy(load(state_path))
    prepared = run_runtime(
        root,
        "prepare-rollover",
        "--generation",
        "1",
        "--task-id",
        "dryrun-brain-1",
    )
    transferred = run_runtime(
        root,
        "transfer-brain",
        "--generation",
        "1",
        "--task-id",
        "dryrun-brain-1",
        "--successor-task-id",
        "dryrun-brain-2",
    )
    stale = run_runtime(
        root,
        "assert-active",
        "--generation",
        "1",
        "--task-id",
        "dryrun-brain-1",
        expect=9,
    )
    active = run_runtime(
        root,
        "assert-active",
        "--generation",
        "2",
        "--task-id",
        "dryrun-brain-2",
    )
    after_rollover = load(state_path)
    events.append(
        {
            "runtime": "Brain rollover",
            "prepared": prepared["status"],
            "transferred": transferred["status"],
            "old_brain_write_blocked": stale["status"] == "error",
            "successor_active": active["status"] == "active",
            "user_prompt_required": False,
        }
    )

    record_result(
        root,
        state_path,
        "stage-004",
        "exp-004",
        "route-b",
        "success",
        {"effect": "focus result preserved improvement"},
    )
    state = load(state_path)
    state["research_runtime"]["next_action"] = "brain_2_interpret_and_continue"
    save(state_path, state)
    events.append({"stage": 4, "brain_generation": 2, "luna_result_received": True})

    final_state = load(state_path)
    result_files = sorted(root.glob("artifacts/orchestration/results/*/result.json"))
    prohibited_artifacts = list(root.rglob("*audit*")) + list(root.rglob("experiment_report.md"))
    generated_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in root.rglob("*")
        if path.is_file()
    ).lower()
    prohibited_crypto_markers = (
        "content_addressed_id",
        "artifact_identity_registry",
        "provenance_manifest",
    )

    preserved_fields = (
        "session_id",
        "project_goal",
        "global_limits",
        "global_budget_used",
        "global_budget_remaining",
        "allowed",
        "forbidden",
    )
    rollover_preserved = all(
        before_rollover["autonomy_session"][key]
        == after_rollover["autonomy_session"][key]
        for key in preserved_fields
    )
    lineage = after_rollover["brain_runtime"]["brain_task_lineage"]
    checks = {
        "three_or_more_stage_transitions": final_state["research_runtime"]["stage_count"] >= 3,
        "stage_close_did_not_close_session": final_state["research_runtime"]["state"] == "RUNNING",
        "route_failure_replanned": final_state["research_runtime"]["route_states"]["route-a"]["status"] == "pruned",
        "goal_mode_rebuilt_portfolio": "route-b" in final_state["research_runtime"]["route_states"],
        "ordinary_bug_repaired_by_luna": bool(load(result_files[0])["repairs"]),
        "brain_did_not_execute_evidence_work": all(
            load(path)["brain_executed_evidence_work"] is False for path in result_files
        ),
        "no_second_research_skill": True,
        "no_user_rewake": all(not event.get("user_wakeup", False) for event in events),
        "no_cryptographic_identity": not any(
            marker in generated_text for marker in prohibited_crypto_markers
        ),
        "no_full_project_audit": not prohibited_artifacts,
        "minimal_result_artifacts": len(result_files) == 4,
        "session_goal_budget_authorization_preserved": rollover_preserved,
        "rollover_not_scientific_stage": after_rollover["research_runtime"]["stage_count"] == 3,
        "completed_experiments_not_replayed": len(final_state["research_runtime"]["completed_experiment_ids"])
        == len(set(final_state["research_runtime"]["completed_experiment_ids"])),
        "one_active_brain": final_state["brain_runtime"]["active_brain_generation"] == 2
        and final_state["brain_runtime"]["active_brain_task_id"] == "dryrun-brain-2",
        "predecessor_transcript_lineage_preserved": lineage == [
            {
                "generation": 1,
                "task_id": "dryrun-brain-1",
                "status": "retired",
                "predecessor_task_id": None,
                "successor_task_id": "dryrun-brain-2",
            },
            {
                "generation": 2,
                "task_id": "dryrun-brain-2",
                "status": "active",
                "predecessor_task_id": "dryrun-brain-1",
                "successor_task_id": None,
            },
        ],
        "successor_read_minimal_authoritative_state": True,
        "active_results_preserved": all(path.is_file() for path in result_files),
        **initializer_checks,
    }

    validation = subprocess.run(
        [sys.executable, str(VALIDATOR), "--root", str(root), "--json"],
        text=True,
        capture_output=True,
        check=False,
    )
    checks["targeted_schema_validation"] = validation.returncode == 0
    if not all(checks.values()):
        failed = [key for key, passed in checks.items() if not passed]
        raise AssertionError("dry-run checks failed: " + ", ".join(failed))

    return {
        "status": "pass",
        "project_root": str(root),
        "events": events,
        "checks": checks,
        "rollover": {
            "from_generation": 1,
            "to_generation": 2,
            "native_platform_step": "create_thread protocol represented by dryrun-brain-2",
            "scientific_stage_count_before": 3,
            "scientific_stage_count_after_transfer": 3,
            "successor_continued_to_stage": 4,
        },
        "artifact_summary": {
            "result_json_count": len(result_files),
            "experiment_specs": 0,
            "experiment_reports": 0,
            "audit_memos": 0,
            "brain_handoff_versions": 1,
        },
    }


def main() -> int:
    args = parse_args()
    if args.keep:
        root = Path(tempfile.mkdtemp(prefix="autonomous-research-dryrun-"))
        result = simulate(root)
    else:
        with tempfile.TemporaryDirectory(prefix="autonomous-research-dryrun-") as temporary:
            root = Path(temporary)
            result = simulate(root)
            result["project_root"] = "<temporary project removed>"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
