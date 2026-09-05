import copy
import json
from pathlib import Path
import tempfile
import unittest

from research_records import INDEX, HISTORY, LIMITS, check_records, publish, read_json


class ResearchRecordsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "result.json").write_text('{"measured": 1}', encoding="utf-8")
        self.state = {
            "autonomy_session": {"project_goal": "真实任务上的选择性记忆", "control_mode": "PAUSED_USER",
                                 "global_budget_used": {"external_calls": 3}, "global_budget_remaining": {"external_calls": 7}},
            "research_runtime": {"state": "READY", "next_action": "await_explicit_user_resume", "completed_experiment_ids": []},
        }

    def packet(self, number=1, kind="value"):
        return {
            "event_id": f"event-{number}",
            "record": {
                "work_id": f"work-{number}", "scientific_stage_id": f"S{number // 3}", "date": "2026-09-05",
                "work_kind": kind, "question": "记忆是否改善真实任务？", "motivation": "已有相关性不足以证明收益",
                "method": "固定模型、任务家族分割、NH配对比较；样本和版本见产物",
                "actions": [f"实际实现及分析-{number}"], "outcome": f"result-{number}: 增益区间跨零",
                "claim_boundary": "开发分布；不能声明真实部署提升", "decision": "定位不确定性",
                "next_step_reason": "新增信息才能改变路线", "execution_status": "completed",
                "scientific_verdict": "inconclusive", "decision_changed": False, "debug": [],
                "artifacts": ["result.json"], "contributions": [
                    {"actor": "user", "work": "提出问题和审核设计", "evidence": "codex-task:known-task"},
                    {"actor": "agent", "work": f"实现 runner 并分析-{number}", "evidence": "result.json"},
                ],
            },
            "snapshot": {
                "stable_formulation": "候选与NH比较；不将相似度等同utility", "constraints": ["保持保护集未访问"],
                "previous_stage": {"id": f"S{number - 1}", "summary": f"previous-{number}", "why_relevant": "留下当前缺口"},
                "next_stage": {"id": f"S{number + 1}", "summary": f"next-{number}", "why_relevant": "有区别的可部署测试"},
                "current_status": "用户暂停中；记录维护不恢复执行", "carry_forward": [],
            },
        }

    def test_many_events_keep_working_memory_short_and_full_history(self):
        original_state = copy.deepcopy(self.state)
        for n in range(1, 31):
            publish(self.root, self.state, self.packet(n, "infrastructure" if n % 2 else "value"))
        for name, limit in LIMITS.items():
            text = (self.root / name).read_text(encoding="utf-8")
            self.assertLessEqual(len(text.encode("utf-8")), limit)
            self.assertNotIn("实际实现及分析-1", text)
        history = (self.root / HISTORY).read_text(encoding="utf-8")
        self.assertIn("实际实现及分析-1", history)
        self.assertIn("实际实现及分析-30", history)
        self.assertIn("user：提出问题", history)
        self.assertIn("agent：实现", history)
        self.assertIn("不能声明真实部署提升", history)
        index = read_json(self.root / INDEX)
        self.assertEqual(index["work_count"], 30)
        self.assertLess(index["scientific_stage_count"], 30)
        self.assertTrue(index["review_recommended"])
        self.assertEqual(check_records(self.root, self.state)["status"], "pass")
        self.assertEqual(self.state, original_state)

    def test_duplicate_event_does_not_duplicate_history_or_invent_progress(self):
        p = self.packet()
        publish(self.root, self.state, p)
        before = (self.root / HISTORY).read_bytes()
        publish(self.root, self.state, p)
        self.assertEqual((self.root / HISTORY).read_bytes(), before)
        self.assertEqual(read_json(self.root / INDEX)["work_count"], 1)
        p["record"]["outcome"] = "rewrite"
        with self.assertRaisesRegex(ValueError, "different content"):
            publish(self.root, self.state, p)

    def test_oversized_snapshot_rejected_without_losing_existing_history(self):
        publish(self.root, self.state, self.packet())
        before = (self.root / HISTORY).read_bytes()
        p = self.packet(2)
        p["snapshot"]["stable_formulation"] = "冗长" * 20000
        with self.assertRaisesRegex(ValueError, "exceeds"):
            publish(self.root, self.state, p)
        self.assertEqual((self.root / HISTORY).read_bytes(), before)
        self.assertEqual(read_json(self.root / INDEX)["work_count"], 1)

    def test_adoption_archives_existing_documents_once(self):
        (self.root / "findings.md").write_text("OLD-FULL-EVIDENCE", encoding="utf-8")
        publish(self.root, self.state, self.packet())
        publish(self.root, self.state, self.packet(2))
        archive = self.root / "artifacts/orchestration/records-adoption/findings.md"
        self.assertEqual(archive.read_text(encoding="utf-8"), "OLD-FULL-EVIDENCE")
        self.assertEqual(len(read_json(self.root / INDEX)["legacy_refs"]), 1)

    def test_invalid_claim_or_missing_evidence_never_publishes(self):
        p = self.packet()
        p["record"]["execution_status"] = "blocked"
        with self.assertRaisesRegex(ValueError, "incomplete execution"):
            publish(self.root, self.state, p)
        p["record"]["scientific_verdict"] = "not_assessable"
        p["record"]["artifacts"] = ["missing.json"]
        with self.assertRaisesRegex(ValueError, "missing evidence"):
            publish(self.root, self.state, p)
        self.assertFalse((self.root / INDEX).exists())

    def test_new_completed_work_cannot_be_hidden_by_refresh(self):
        p = self.packet()
        publish(self.root, self.state, p)
        self.state["research_runtime"]["completed_experiment_ids"] = ["E2"]
        self.assertEqual(check_records(self.root, self.state)["status"], "needs_refresh")
        with self.assertRaisesRegex(ValueError, "own outcome record"):
            publish(self.root, self.state, p)
        newer = self.packet(2)
        newer["record"]["experiment_id"] = "E2"
        publish(self.root, self.state, newer)
        self.assertEqual(check_records(self.root, self.state)["status"], "pass")

    def test_runtime_change_and_oversized_manual_append_detected(self):
        p = self.packet()
        publish(self.root, self.state, p)
        self.state["autonomy_session"]["control_mode"] = "RUNNING"
        self.assertEqual(check_records(self.root, self.state)["status"], "needs_refresh")
        publish(self.root, self.state, p)
        self.assertEqual(read_json(self.root / INDEX)["work_count"], 1)
        with (self.root / "progress.md").open("a", encoding="utf-8") as handle:
            handle.write("x" * LIMITS["progress.md"])
        self.assertEqual(check_records(self.root, self.state)["status"], "needs_refresh")

    def test_manual_history_and_working_edits_survive_regeneration(self):
        publish(self.root, self.state, self.packet())
        for name in (HISTORY, "task_plan.md"):
            with (self.root / name).open("a", encoding="utf-8") as handle:
                handle.write("\nUSER ADDED RESEARCH CONTRIBUTION\n")
        self.assertEqual(check_records(self.root, self.state)["status"], "needs_refresh")
        publish(self.root, self.state, self.packet(2))
        refs = read_json(self.root / INDEX)["legacy_refs"]
        self.assertEqual(len(refs), 2)
        for ref in refs:
            self.assertIn("USER ADDED RESEARCH CONTRIBUTION", (self.root / ref).read_text(encoding="utf-8"))
        self.assertEqual(check_records(self.root, self.state)["status"], "pass")

    def test_runtime_dispatch_requires_latest_result_publication(self):
        import argparse
        import runtime_control
        from test_runtime_control import make_project
        runtime_state = make_project(self.root)
        args = argparse.Namespace(root=str(self.root), generation=1, task_id="brain-1")
        publish(self.root, runtime_state, self.packet())
        self.assertEqual(runtime_control.command_assert_dispatch(args)["status"], "dispatchable")
        runtime_state["research_runtime"]["completed_experiment_ids"] = ["E2"]
        runtime_control.atomic_write(self.root / "artifacts/orchestration/pipeline_state.json", runtime_state)
        with self.assertRaisesRegex(runtime_control.RuntimeErrorWithCode, "records require refresh"):
            runtime_control.command_assert_dispatch(args)
        packet = self.packet(2)
        packet["record"]["experiment_id"] = "E2"
        publish(self.root, runtime_state, packet)
        self.assertEqual(runtime_control.command_assert_dispatch(args)["status"], "dispatchable")


if __name__ == "__main__":
    unittest.main()
