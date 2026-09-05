from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime_control
import validate_autonomous_research


SCRIPT = Path(__file__).resolve().parent / "runtime_control.py"


def make_project(
    root: Path,
    *,
    enabled: bool = True,
    threshold: int | None = 2,
    control_mode: str | None = "RUNNING",
    next_action: str = "dispatch_next_stage",
    active_workers: list[str] | None = None,
) -> dict:
    (root / "artifacts" / "orchestration").mkdir(parents=True)
    adapter = {
        "schema_version": 4,
        "project_id": "test-project",
        "project_title": "Runtime test",
        "research_root": str(root),
        "source_precedence": {
            "normative_plan": "task_plan.md",
            "evidence_record": "findings.md",
            "status_record": "progress.md",
        },
        "artifact_system": {
            "pipeline_state": "artifacts/orchestration/pipeline_state.json",
            "brain_handoff": "artifacts/orchestration/brain_handoff.md",
            "results_root": "artifacts/orchestration/results",
        },
        "coordination": {},
        "portfolio_policy": {"max_active_routes": 3},
        "verification": {
            "cryptographic_provenance": "user_explicit_only",
            "global_audit_in_normal_loop": False,
        },
        "models": {},
    }
    limits = {
        "wall_minutes": 20,
        "worker_assignments": 4,
        "external_calls": 4,
        "external_cost_usd": 4.0,
    }
    zero = {key: 0 for key in limits}
    zero["external_cost_usd"] = 0.0
    session = {
        "session_id": "session-1",
        "enabled": enabled,
        "project_goal": "test goal",
        "goal_status": "unresolved",
        "global_limits": limits,
        "global_budget_used": copy.deepcopy(zero),
        "global_budget_remaining": copy.deepcopy(limits),
        "allowed": {
            "brain_rollover": True,
            "native_luna_subagents": False,
            "public_read_only_web": False,
        },
        "forbidden": {"destructive_actions": True},
    }
    if control_mode is not None:
        session["control_mode"] = control_mode
    state = {
        "schema_version": 4,
        "project_id": "test-project",
        "autonomy_session": session,
        "brain_runtime": {
            "active_brain_generation": 1,
            "active_brain_task_id": "brain-1",
            "previous_brain_generation": None,
            "previous_brain_task_id": None,
            "brain_task_lineage": [
                {
                    "generation": 1,
                    "task_id": "brain-1",
                    "status": "active",
                    "predecessor_task_id": None,
                    "successor_task_id": None,
                }
            ],
            "context_health": "healthy",
            "context_compaction_count": 0,
            "context_compaction_event_ids": [],
            "rollover_reason": None,
            "rollover_count": 0,
            "pending_successor_generation": None,
            "handoff_path": "artifacts/orchestration/brain_handoff.md",
            "handoff_status": "none",
        },
        "research_runtime": {
            "state": "RUNNING",
            "active_stage_id": "stage-1",
            "stage_count": 1,
            "active_route_ids": [],
            "route_states": {},
            "active_experiment_ids": [],
            "completed_experiment_ids": [],
            "active_luna_tasks": active_workers or [],
            "next_action": next_action,
        },
        "integrity_runtime": {"invalidated_artifacts": [], "unresolved_issues": []},
        "last_transition": None,
    }
    if threshold is not None:
        state["brain_runtime"]["rollover_threshold"] = threshold
    (root / "research_pipeline.yaml").write_text(
        json.dumps(adapter), encoding="utf-8"
    )
    state_path = root / "artifacts" / "orchestration" / "pipeline_state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    (root / "artifacts" / "orchestration" / "brain_handoff.md").write_text(
        "compact handoff", encoding="utf-8"
    )
    return state


def cli(root: Path, *args: str) -> tuple[int, dict]:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"command output was not JSON: {completed.stdout!r}; stderr={completed.stderr!r}"
        ) from exc
    return completed.returncode, value


def read_state(root: Path) -> dict:
    return json.loads(
        (root / "artifacts" / "orchestration" / "pipeline_state.json").read_text(
            encoding="utf-8"
        )
    )


class RuntimeControlTests(unittest.TestCase):
    def test_pause_drains_and_paused_rollover_survives_transfer(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            make_project(root, active_workers=["worker-1"])
            code, result = cli(
                root,
                "pause",
                "--generation",
                "1",
                "--task-id",
                "brain-1",
                "--mode",
                "PAUSED_USER",
                "--reason",
                "user requested stop",
            )
            self.assertEqual(code, 0, result)
            self.assertEqual(result["active_worker_count"], 1)
            code, result = cli(
                root,
                "assert-dispatch",
                "--generation",
                "1",
                "--task-id",
                "brain-1",
            )
            self.assertNotEqual(code, 0)
            self.assertEqual(result["status"], "error")

            state = read_state(root)
            self.assertEqual(state["research_runtime"]["active_luna_tasks"], ["worker-1"])
            state["research_runtime"]["active_luna_tasks"] = []
            state["brain_runtime"]["context_health"] = "rollover_required"
            state["brain_runtime"]["rollover_reason"] = "test"
            (root / "artifacts" / "orchestration" / "pipeline_state.json").write_text(
                json.dumps(state), encoding="utf-8"
            )
            code, result = cli(
                root,
                "prepare-rollover",
                "--generation",
                "1",
                "--task-id",
                "brain-1",
            )
            self.assertEqual(code, 0, result)
            self.assertEqual(result["control_mode"], "PAUSED_USER")
            self.assertEqual(result["saved_next_action"], "dispatch_next_stage")
            code, result = cli(
                root,
                "transfer-brain",
                "--generation",
                "1",
                "--task-id",
                "brain-1",
                "--successor-task-id",
                "brain-2",
            )
            self.assertEqual(code, 0, result)
            after = read_state(root)
            self.assertEqual(after["autonomy_session"]["control_mode"], "PAUSED_USER")
            self.assertTrue(after["autonomy_session"]["user_pause_latch"])
            self.assertEqual(
                after["autonomy_session"]["saved_next_action"], "dispatch_next_stage"
            )
            self.assertEqual(after["research_runtime"]["next_action"], "await_user_resume")
            code, result = cli(root, "resume", "--generation", "2", "--task-id", "brain-2")
            self.assertEqual(code, 0, result)
            after = read_state(root)
            self.assertEqual(after["autonomy_session"]["control_mode"], "RUNNING")
            self.assertFalse(after["autonomy_session"]["user_pause_latch"])
            self.assertEqual(after["research_runtime"]["next_action"], "dispatch_next_stage")

    def test_legacy_pause_inference_and_stale_owner(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            make_project(
                root,
                control_mode=None,
                next_action="await_explicit_user_resume_after_task188",
            )
            state = read_state(root)
            state["research_runtime"]["state"] = "READY"
            (root / "artifacts" / "orchestration" / "pipeline_state.json").write_text(
                json.dumps(state), encoding="utf-8"
            )
            code, result = cli(
                root,
                "assert-active",
                "--generation",
                "1",
                "--task-id",
                "brain-1",
            )
            self.assertEqual(code, 0, result)
            self.assertEqual(result["control_mode"], "PAUSED_USER")
            code, result = cli(
                root,
                "assert-dispatch",
                "--generation",
                "1",
                "--task-id",
                "stale-brain",
            )
            self.assertEqual(code, 9)
            self.assertEqual(result["status"], "error")

    def test_default_and_configured_compaction_thresholds_and_duplicate_events(self) -> None:
        for threshold, expected_required_at in ((2, 2), (3, 3)):
            with self.subTest(threshold=threshold):
                with tempfile.TemporaryDirectory() as raw:
                    root = Path(raw)
                    make_project(root, threshold=threshold)
                    for count in range(1, expected_required_at + 1):
                        code, result = cli(
                            root,
                            "record-context-compaction",
                            "--generation",
                            "1",
                            "--task-id",
                            "brain-1",
                            "--event-id",
                            f"event-{count}",
                        )
                        self.assertEqual(code, 0, result)
                    state = read_state(root)
                    self.assertEqual(
                        state["brain_runtime"]["context_compaction_count"],
                        expected_required_at,
                    )
                    self.assertEqual(
                        state["brain_runtime"]["context_health"],
                        "rollover_required",
                    )
                    code, result = cli(
                        root,
                        "record-context-compaction",
                        "--generation",
                        "1",
                        "--task-id",
                        "brain-1",
                        "--event-id",
                        f"event-{expected_required_at}",
                    )
                    self.assertEqual(code, 0, result)
                    self.assertEqual(result["status"], "duplicate")
                    if threshold == 3:
                        code, _ = cli(
                            root,
                            "assert-dispatch",
                            "--generation",
                            "1",
                            "--task-id",
                            "brain-1",
                        )
                        self.assertNotEqual(code, 0)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            make_project(root, threshold=None)
            state = read_state(root)
            state["brain_runtime"]["rollover_after_observable_compactions"] = 3
            (root / "artifacts" / "orchestration" / "pipeline_state.json").write_text(
                json.dumps(state), encoding="utf-8"
            )
            code, result = cli(
                root,
                "record-context-compaction",
                "--generation",
                "1",
                "--task-id",
                "brain-1",
                "--event-id",
                "legacy-threshold-check",
            )
            self.assertEqual(code, 0, result)
            self.assertEqual(result["rollover_threshold"], 2)

    def test_snapshot_policy_precedence_and_history_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            make_project(root)
            state = read_state(root)
            state["autonomy_session"]["allowed"]["native_luna_subagents"] = False
            state["autonomy_session"]["user_native_luna_transport_override_20260828"] = {
                "instruction": "native worker",
                "transport_scope": "worker transport only",
                "api_key": "SECRET",
            }
            state["autonomy_session"]["standing_external_authorization"] = {
                "instruction": "allow external",
                "api_key": "SECRET",
            }
            state["brain_runtime"]["brain_task_lineage"] = [
                {"generation": i, "task_id": f"old-{i}"} for i in range(100)
            ]
            (root / "artifacts" / "orchestration" / "pipeline_state.json").write_text(
                json.dumps(state), encoding="utf-8"
            )
            (root / "artifacts" / "orchestration" / "records_index.json").write_text(
                json.dumps(
                    {
                        "current_event": {"event_id": "e-1", "status": "accepted", "secret": "SECRET"},
                        "current_work_id": "work-1",
                        "scientific_stage_count": 3,
                        "work_count": 7,
                        "no_information_streak": 1,
                        "review_recommended": True,
                        "history_path": "records/history.jsonl",
                        "history": ["SECRET"] * 100,
                    }
                ),
                encoding="utf-8",
            )
            code, result = cli(root, "snapshot")
            self.assertEqual(code, 0, result)
            serialized = json.dumps(result)
            self.assertNotIn("SECRET", serialized)
            self.assertNotIn("brain_task_lineage", serialized)
            self.assertEqual(result["records"]["current_work_id"], "work-1")
            self.assertNotIn("history", result["records"])
            policy = result["effective_policy"]
            self.assertTrue(policy["native_luna_subagents"])
            self.assertEqual(
                policy["provenance"]["native_luna_subagents"]["amendment_id"],
                "user_native_luna_transport_override_20260828",
            )
            self.assertFalse(policy["external_network"])

            state["autonomy_session"]["transport_policy"] = {
                "native_luna_subagents": False,
                "external_network": True,
            }
            self.assertFalse(runtime_control.effective_policy(state)["native_luna_subagents"])
            self.assertTrue(runtime_control.effective_policy(state)["external_network"])
            state["autonomy_session"].pop("transport_policy")
            state["autonomy_session"][
                "user_native_luna_transport_override_20260828"
            ] = {}
            self.assertFalse(runtime_control.effective_policy(state)["native_luna_subagents"])

    def test_reservations_idempotency_uncertain_settlement_and_overlap_guard(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            make_project(root)
            code, result = cli(
                root,
                "reserve-budget",
                "--generation",
                "1",
                "--task-id",
                "brain-1",
                "--idempotency-key",
                "task-a",
                "--external-calls",
                "2",
                "--external-cost-usd",
                "1.0",
                "--uncertain",
            )
            self.assertEqual(code, 0, result)
            code, duplicate = cli(
                root,
                "reserve-budget",
                "--generation",
                "1",
                "--task-id",
                "brain-1",
                "--idempotency-key",
                "task-a",
                "--external-calls",
                "2",
                "--external-cost-usd",
                "1.0",
                "--uncertain",
            )
            self.assertEqual(code, 0, duplicate)
            self.assertEqual(duplicate["status"], "duplicate")
            code, _ = cli(
                root,
                "record-budget",
                "--generation",
                "1",
                "--task-id",
                "brain-1",
                "--external-calls",
                "3",
            )
            self.assertNotEqual(code, 0)
            code, result = cli(
                root,
                "finalize-budget",
                "--generation",
                "1",
                "--task-id",
                "brain-1",
                "--idempotency-key",
                "task-a",
                "--external-calls",
                "1",
                "--external-cost-usd",
                "0.1",
            )
            self.assertEqual(code, 0, result)
            self.assertEqual(result["settled"]["external_calls"], 2)
            code, duplicate = cli(
                root,
                "finalize-budget",
                "--generation",
                "1",
                "--task-id",
                "brain-1",
                "--idempotency-key",
                "task-a",
            )
            self.assertEqual(code, 0, duplicate)
            self.assertEqual(duplicate["status"], "duplicate")
            state = read_state(root)
            self.assertEqual(
                state["autonomy_session"]["global_budget_used"]["external_calls"], 2
            )
            self.assertEqual(
                state["autonomy_session"]["global_budget_used"]["external_cost_usd"], 1.0
            )
            code, result = cli(
                root,
                "reconcile-budget",
                "--generation",
                "1",
                "--task-id",
                "brain-1",
                "--idempotency-key",
                "task-a",
                "--external-calls",
                "1",
                "--external-cost-usd",
                "0.1",
            )
            self.assertEqual(code, 0, result)
            self.assertEqual(result["status"], "reconciled")
            code, duplicate = cli(
                root,
                "reconcile-budget",
                "--generation",
                "1",
                "--task-id",
                "brain-1",
                "--idempotency-key",
                "task-a",
                "--external-calls",
                "1",
                "--external-cost-usd",
                "0.1",
            )
            self.assertEqual(code, 0, duplicate)
            self.assertEqual(duplicate["status"], "duplicate")
            state = read_state(root)
            self.assertEqual(
                state["autonomy_session"]["global_budget_used"]["external_calls"], 1
            )
            self.assertEqual(
                state["autonomy_session"]["global_budget_used"]["external_cost_usd"], 0.1
            )
            validation = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parent / "validate_autonomous_research.py"),
                    "--root",
                    str(root),
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(validation.returncode, 0, validation.stdout + validation.stderr)

    def test_reservation_lock_serializes_concurrent_writers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            make_project(root)
            commands = []
            for key in ("parallel-a", "parallel-b"):
                commands.append(
                    [
                        sys.executable,
                        str(SCRIPT),
                        "reserve-budget",
                        "--generation", "1", "--task-id", "brain-1",
                        "--root",
                        str(root),
                        "--idempotency-key",
                        key,
                        "--external-calls",
                        "3",
                    ]
                )
            processes = [
                subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for command in commands
            ]
            outputs = [process.communicate(timeout=15) for process in processes]
            statuses = [json.loads(stdout)["status"] for stdout, _ in outputs]
            self.assertEqual(statuses.count("reserved"), 1, outputs)
            self.assertEqual(statuses.count("error"), 1, outputs)
            state = read_state(root)
            self.assertEqual(
                state["autonomy_session"]["global_budget_reserved"]["external_calls"], 3
            )

    def test_finalize_overrun_preserves_incurred_usage_and_stops_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            make_project(root)
            code, result = cli(
                root,
                "reserve-budget",
                "--generation",
                "1",
                "--task-id",
                "brain-1",
                "--idempotency-key",
                "overrun-task",
                "--external-calls",
                "1",
            )
            self.assertEqual(code, 0, result)
            code, result = cli(
                root,
                "finalize-budget",
                "--generation",
                "1",
                "--task-id",
                "brain-1",
                "--idempotency-key",
                "overrun-task",
                "--external-calls",
                "5",
            )
            self.assertEqual(code, 0, result)
            self.assertEqual(result["status"], "finalized")
            self.assertTrue(result["overrun"])
            state = read_state(root)
            self.assertEqual(
                state["autonomy_session"]["global_budget_used"]["external_calls"], 5
            )
            self.assertEqual(state["autonomy_session"]["budget_overrun"]["status"], "overrun")
            code, result = cli(
                root,
                "assert-dispatch",
                "--generation",
                "1",
                "--task-id",
                "brain-1",
            )
            self.assertNotEqual(code, 0)
            validation = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parent / "validate_autonomous_research.py"),
                    "--root",
                    str(root),
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(validation.returncode, 0, validation.stdout + validation.stderr)


class RuntimeIntegrationTests(unittest.TestCase):
    def test_legacy_stops_and_terminal_are_not_dispatchable(self):
        for legacy_state, goal, expected in (("STOPPED_BY_USER", "unresolved", "PAUSED_USER"), ("PROJECT_CLOSED", "complete", "TERMINAL")):
            with tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                state = make_project(root, control_mode=None)
                state["research_runtime"]["state"] = legacy_state
                state["autonomy_session"]["goal_status"] = goal
                runtime_control.atomic_write(root / "artifacts/orchestration/pipeline_state.json", state)
                self.assertEqual(runtime_control.current_control_mode(state), expected)
                code, result = cli(root, "assert-dispatch", "--generation", "1", "--task-id", "brain-1")
                self.assertNotEqual(code, 0, result)

    def test_uncertainty_discovered_at_settlement_can_be_reconciled(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            make_project(root)
            owner = ("--generation", "1", "--task-id", "brain-1", "--idempotency-key", "late-uncertainty")
            code, result = cli(root, "reserve-budget", *owner, "--external-calls", "3")
            self.assertEqual(code, 0, result)
            code, result = cli(root, "finalize-budget", *owner, "--external-calls", "1", "--uncertain")
            self.assertEqual(code, 0, result)
            self.assertEqual(result["settled"]["external_calls"], 3)
            code, result = cli(root, "reconcile-budget", *owner, "--external-calls", "1")
            self.assertEqual(code, 0, result)
            self.assertEqual(result["used"]["external_calls"], 1)


if __name__ == "__main__":
    unittest.main()
