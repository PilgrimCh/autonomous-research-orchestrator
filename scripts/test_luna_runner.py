"""Focused, no-network tests for the bounded Luna runner contract."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_luna_worker import (  # noqa: E402
    EFFORT,
    MODEL,
    ROLE,
    RunnerError,
    _validate_inputs_for_mode,
    _write_process_output,
    validate_assignment,
    validate_native_receipt,
    validate_result,
)


def base_assignment(root: Path) -> dict:
    return {
        "task_id": "task-v2",
        "stage_id": "stage-1",
        "route_id": "route-a",
        "project_goal_link": "bounded test",
        "question": "does the contract validate?",
        "decision_affected": "runner acceptance",
        "inputs": [],
        "scientific_contract": {"comparison": "fixed"},
        "allowed_actions": ["local test"],
        "forbidden_actions": ["scope expansion"],
        "write_scope": ["results"],
        "method": ["construct fixture"],
        "result_path": "results/result.json",
        "targeted_sanity_check": "contract check",
        "resource_ceiling": {"wall_minutes": 1},
        "repair_authority": {
            "allowed": ["execution-preserving repair"],
            "forbidden": ["scientific changes"],
            "debug_cycle_limit": 3,
            "required_sequence": [
                "reproduce",
                "minimize",
                "diagnose",
                "repair",
                "targeted_validate",
                "resume_original_experiment",
            ],
        },
        "stopping_rule": "stop after the fixture",
    }


def base_result(*, status: str = "inconclusive", **overrides: object) -> dict:
    result = {
        "experiment_id": "exp-v2",
        "status": status,
        "executed": {"method": "fixture"},
        "primary_results": {"signal": "uncertain"},
        "sanity_check": {"status": "qualified", "details": "targeted check"},
        "deviations": [],
        "repairs": [],
        "debug": {
            "encountered": False,
            "attempts": [],
            "resolved": None,
            "original_experiment_resumed": True,
            "local_repair_cycles": 0,
            "completed_repair_cycles": 0,
        },
        "resource_use": {"wall_minutes": 0},
        "artifacts": [],
        "claim_boundary": "fixture only",
        "limitations": [],
        "brain_decision_needed": "interpret fixture",
        "result_schema_version": 2,
        "execution_status": "completed",
        "evidence_validity": "valid",
        "scientific_verdict": status,
    }
    result.update(overrides)
    return result


def write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class LunaRunnerContractTests(unittest.TestCase):
    def test_v2_valid_inconclusive_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assignment, result_path = validate_assignment(base_assignment(root), root)
            write_json(result_path, base_result())
            report = validate_result(result_path, assignment)
            self.assertTrue(report.valid, report.errors)
            self.assertEqual(report.errors, [])

    def test_v2_infrastructure_completion_is_accepted_without_scientific_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_assignment = base_assignment(root)
            raw_assignment["work_kind"] = "infrastructure"
            assignment, result_path = validate_assignment(raw_assignment, root)
            write_json(
                result_path,
                base_result(
                    status="completed",
                    work_kind="infrastructure",
                    scientific_verdict="not_assessable",
                ),
            )
            report = validate_result(result_path, assignment)
            self.assertTrue(report.valid, report.errors)

    def test_scientific_work_cannot_use_non_scientific_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assignment, result_path = validate_assignment(base_assignment(root), root)
            write_json(
                result_path,
                base_result(
                    status="completed",
                    scientific_verdict="not_assessable",
                ),
            )
            report = validate_result(result_path, assignment)
            self.assertFalse(report.valid)
            self.assertTrue(any("work_kind" in error for error in report.errors))

    def test_result_task_and_stage_identifiers_bind_to_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assignment, result_path = validate_assignment(base_assignment(root), root)
            write_json(
                result_path,
                base_result(task_id="stale-task", stage_id="stage-1"),
            )
            report = validate_result(result_path, assignment)
            self.assertFalse(report.valid)
            self.assertTrue(any("result_identity_mismatch:task_id" in error for error in report.errors))

    def test_validation_result_override_must_stay_in_declared_write_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assignment_path = write_json(root / "assignment.json", base_assignment(root))
            args = SimpleNamespace(
                assignment=str(assignment_path),
                workdir=str(root),
                result_path=str(root / "outside-result.json"),
            )
            with self.assertRaises(RunnerError) as context:
                _validate_inputs_for_mode(args)
            self.assertIn("write_scope", str(context.exception))

    def test_false_success_contradiction_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assignment, result_path = validate_assignment(base_assignment(root), root)
            blocked_debug = {
                "encountered": True,
                "failure_class": "parser",
                "root_cause": "unresolved",
                "resolved": False,
                "original_experiment_resumed": False,
                "attempts": [{"kind": "diagnostic_read", "details": "read log"}],
                "local_repair_cycles": 0,
                "completed_repair_cycles": 0,
            }
            write_json(
                result_path,
                base_result(
                    status="success",
                    execution_status="blocked",
                    evidence_validity="not_assessed",
                    scientific_verdict="success",
                    debug=blocked_debug,
                ),
            )
            report = validate_result(result_path, assignment)
            self.assertFalse(report.valid)
            self.assertTrue(any("classification contradiction" in error for error in report.errors))

    def test_unresolved_error_cannot_be_scientific_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assignment, result_path = validate_assignment(base_assignment(root), root)
            write_json(
                result_path,
                base_result(
                    status="negative",
                    debug={
                        "encountered": True,
                        "failure_class": "runtime",
                        "root_cause": "still failing",
                        "resolved": False,
                        "original_experiment_resumed": False,
                        "attempts": [{"kind": "diagnostic_read", "details": "read log"}],
                        "local_repair_cycles": 0,
                        "completed_repair_cycles": 0,
                    },
                ),
            )
            report = validate_result(result_path, assignment)
            self.assertFalse(report.valid)
            self.assertTrue(any("unresolved blocker" in error or "invalid until" in error for error in report.errors))

    def test_legacy_result_is_valid_with_explicit_normalization_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "legacy188-result.json"
            write_json(
                path,
                {
                    "experiment_id": "legacy-188",
                    "status": "inconclusive",
                    "executed": {},
                    "primary_results": {},
                    "sanity_check": {"status": "qualified"},
                    "deviations": [],
                    "repairs": [],
                    "debug": {
                        "encountered": False,
                        "attempts": [],
                        "resolved": None,
                        "original_experiment_resumed": True,
                    },
                    "resource_use": {},
                    "artifacts": [],
                    "claim_boundary": "historical",
                    "limitations": [],
                    "brain_decision_needed": "none",
                },
            )
            report = validate_result(path)
            self.assertTrue(report.valid, report.errors)
            self.assertTrue(any("legacy_normalization" in warning for warning in report.warnings))

    def test_debug_cycle_discrepancy_is_structured_contract_deviation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assignment, result_path = validate_assignment(base_assignment(root), root)
            write_json(
                result_path,
                base_result(
                    debug={
                        "encountered": True,
                        "failure_class": "runtime",
                        "root_cause": "fixed",
                        "resolved": True,
                        "original_experiment_resumed": True,
                        "attempts": [{"kind": "repair_cycle", "details": "repair"}],
                        "local_repair_cycles": 1,
                        "completed_repair_cycles": 0,
                    }
                ),
            )
            report = validate_result(result_path, assignment)
            self.assertFalse(report.valid)
            self.assertTrue(any("debug_cycle_count_mismatch" in error for error in report.errors))

    def test_diagnostic_reads_do_not_count_as_repairs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assignment, result_path = validate_assignment(base_assignment(root), root)
            write_json(
                result_path,
                base_result(
                    debug={
                        "encountered": True,
                        "failure_class": "runtime",
                        "root_cause": "diagnostics only",
                        "resolved": True,
                        "original_experiment_resumed": True,
                        "attempts": [{"kind": "diagnostic_read", "details": "probe"}],
                        "local_repair_cycles": 0,
                        "completed_repair_cycles": 0,
                    }
                ),
            )
            report = validate_result(result_path, assignment)
            self.assertTrue(report.valid, report.errors)

    def test_native_receipt_accepts_independent_platform_evidence(self) -> None:
        receipt = {
            "receipt_schema_version": 1,
            "transport": "native_codex_thread",
            "native_agent_id": "native-agent-123",
            "configured_role": ROLE,
            "model": MODEL,
            "reasoning_effort": EFFORT,
            "cryptographic_proof": False,
            "platform_evidence": {
                "source": "brain_native_tool",
                "verified": True,
                "verified_by": "brain-1",
                "native_agent_id": "native-agent-123",
                "configured_role": ROLE,
                "model": MODEL,
                "reasoning_effort": EFFORT,
            },
        }
        report = validate_native_receipt(receipt)
        self.assertTrue(report.valid, report.errors)

    def test_native_receipt_rejects_self_report_or_crypto_claim(self) -> None:
        receipt = {
            "transport": "native_codex_thread",
            "native_agent_id": "native-agent-123",
            "configured_role": ROLE,
            "model": MODEL,
            "reasoning_effort": EFFORT,
            "cryptographic_proof": True,
            "platform_evidence": {"source": "self_report", "verified": False, "agent_id": "native-agent-123"},
        }
        report = validate_native_receipt(receipt)
        self.assertFalse(report.valid)
        self.assertTrue(any("cryptographic" in error or "independent" in error for error in report.errors))

    def test_native_accept_mode_uses_receipt_without_launching_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assignment, result_path = validate_assignment(base_assignment(root), root)
            write_json(result_path, base_result())
            receipt_path = write_json(
                root / "receipt.json",
                {
                    "receipt_schema_version": 1,
                    "transport": "native_codex_thread",
                    "native_agent_id": "native-agent-123",
                    "configured_role": ROLE,
                    "model": MODEL,
                    "reasoning_effort": EFFORT,
                    "cryptographic_proof": False,
                    "platform_evidence": {
                        "source": "brain_native_tool",
                        "verified": True,
                        "verified_by": "brain-1",
                        "native_agent_id": "native-agent-123",
                        "configured_role": ROLE,
                        "model": MODEL,
                        "reasoning_effort": EFFORT,
                    },
                },
            )
            with patch.object(sys, "argv", [
                "run_luna_worker.py", "--accept-native", "--assignment", str(root / "assignment.json"),
                "--workdir", str(root), "--result", str(result_path), "--runtime-receipt", str(receipt_path),
                "--run-dir", str(root / "run"),
            ]):
                write_json(root / "assignment.json", base_assignment(root))
                from run_luna_worker import main

                self.assertEqual(main(), 0)
            run = json.loads((root / "run" / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run["transport"], "native_codex_thread")
            self.assertFalse(run["dispatch_started"])

    def test_predispatch_and_timeout_have_different_uncertainty(self) -> None:
        pre = RunnerError("no cli", code="cli_discovery_failed", failure_phase="discovery")
        self.assertFalse(pre.dispatch_started)
        self.assertFalse(pre.side_effect_uncertain)
        self.assertIn("No worker dispatch occurred", pre.safe_next_action)
        timeout = RunnerError("timeout", code="worker_timeout", failure_phase="execution", dispatch_started=True, side_effect_uncertain=True)
        self.assertTrue(timeout.dispatch_started)
        self.assertTrue(timeout.side_effect_uncertain)
        self.assertIn("do not replay", timeout.safe_next_action)

    def test_timeout_output_is_bounded_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stdout, stderr = _write_process_output(
                Path(temporary),
                "token=super-secret-value " + "x" * 20000,
                "Authorization: Bearer abcdefghijklmnop",
            )
            self.assertIn("[REDACTED]", stdout)
            self.assertIn("[REDACTED]", stderr)
            self.assertLessEqual(len(stdout), 12020)
            self.assertLessEqual(len(stderr), 12000)


if __name__ == "__main__":
    unittest.main()
