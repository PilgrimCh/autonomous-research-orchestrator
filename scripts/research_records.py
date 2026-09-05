"""Publish bounded working memory and a traceable research history, without running science.

Brain supplies meaning; this tool enforces retention, idempotence, size and freshness.
Only the active Brain can publish. A publication never resumes or edits research state.
"""
from __future__ import annotations

import argparse
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

LIMITS = {"task_plan.md": 12 * 1024, "findings.md": 20 * 1024, "progress.md": 8 * 1024}
INDEX = "artifacts/orchestration/records_index.json"
HISTORY = "research_history.md"
KINDS = {"value", "mechanism", "infrastructure", "documentation"}
VERDICTS = {"success", "negative", "mixed", "inconclusive", "not_assessable"}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(raw, path)
    finally:
        if Path(raw).exists():
            Path(raw).unlink()


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


@contextmanager
def record_lock(root):
    path = root / "artifacts/orchestration/records.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError("another publication is active; reconcile stale lock before retry") from exc
    try:
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def nonempty(obj, key):
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a nonempty string")
    return value.strip()


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", value):
        raise ValueError("event/stage IDs must be safe stable identifiers")
    return value


def local_path(root, raw):
    path = (root / raw).resolve()
    if not path.is_relative_to(root):
        raise ValueError("record path escapes project")
    return path


def validate_ref(root, raw):
    if not isinstance(raw, str) or not raw:
        raise ValueError("evidence pointer must be a nonempty string")
    if raw.startswith("codex-task:"):
        if not raw.removeprefix("codex-task:").strip():
            raise ValueError("missing transcript identity")
        return
    if raw.startswith("https://"):
        parsed = urlsplit(raw)
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("invalid or credential-bearing reference URL")
        return
    if not local_path(root, raw).exists():
        raise ValueError(f"missing evidence: {raw}")


def validate_packet(root, packet):
    safe_id(packet.get("event_id"))
    record = packet.get("record")
    view = packet.get("snapshot")
    if not isinstance(record, dict) or not isinstance(view, dict):
        raise ValueError("record and snapshot objects required")
    for key in ("work_id", "scientific_stage_id", "date", "question", "motivation", "method",
                "outcome", "claim_boundary", "decision", "next_step_reason"):
        nonempty(record, key)
    safe_id(record["scientific_stage_id"])
    if record.get("work_kind") not in KINDS:
        raise ValueError("invalid work_kind")
    if record.get("scientific_verdict") not in VERDICTS:
        raise ValueError("invalid scientific_verdict")
    if record.get("execution_status") not in {"completed", "blocked", "invalid"}:
        raise ValueError("invalid execution_status")
    if record.get("execution_status") != "completed" and record["scientific_verdict"] != "not_assessable":
        raise ValueError("incomplete execution is not scientific negative/inconclusive")
    if not isinstance(record.get("decision_changed"), bool):
        raise ValueError("decision_changed must be boolean")
    if record["decision_changed"]:
        nonempty(record, "decision_evidence")
    for key in ("actions", "artifacts", "contributions"):
        if not isinstance(record.get(key), list) or not record[key]:
            raise ValueError(f"{key} must be a nonempty list")
    if any(not isinstance(x, str) or not x.strip() for x in record["actions"]):
        raise ValueError("actions must describe actual work")
    if not isinstance(record.get("debug"), list):
        raise ValueError("debug must list attempts, or be empty")
    for ref in record["artifacts"]:
        validate_ref(root, ref)
    for item in record["contributions"]:
        if not isinstance(item, dict) or item.get("actor") not in {"user", "agent", "shared", "unknown"}:
            raise ValueError("contribution actor must be user/agent/shared/unknown")
        nonempty(item, "work")
        validate_ref(root, item.get("evidence"))
    for key in ("stable_formulation", "current_status"):
        nonempty(view, key)
    if not isinstance(view.get("constraints"), list) or not all(isinstance(x, str) for x in view["constraints"]):
        raise ValueError("constraints must be a list")
    for key in ("previous_stage", "next_stage"):
        if not isinstance(view.get(key), dict):
            raise ValueError(f"{key} must be an object")
        for field in ("id", "summary", "why_relevant"):
            nonempty(view[key], field)
    carry = view.get("carry_forward", [])
    if not isinstance(carry, list) or len(carry) > 5:
        raise ValueError("carry at most five older decision-critical facts; link remaining history")
    for item in carry:
        for key in ("claim", "needed_for_next"):
            nonempty(item, key)
        validate_ref(root, item.get("evidence"))
    for item in record["debug"]:
        if not isinstance(item, dict):
            raise ValueError("debug items must be objects")
        for key in ("cause", "action", "result"):
            nonempty(item, key)
    return record, view


def evidence_link(root, raw, label=None):
    if raw.startswith("codex-task:"):
        return f"`{raw}`"
    target = raw if raw.startswith("https://") else local_path(root, raw).as_posix()
    return f"[{label or raw}](<{target}>)"


def render_working(root, state, packet):
    record, view = packet["record"], packet["snapshot"]
    session, runtime = state["autonomy_session"], state["research_runtime"]
    goal = session["project_goal"]
    previous, upcoming = view["previous_stage"], view["next_stage"]
    history = evidence_link(root, HISTORY, "研究履历 / 完整方法、结果与贡献")
    state_ref = evidence_link(root, "artifacts/orchestration/pipeline_state.json", "运行状态 / 授权与保护清单")
    footer = f"\n完整历史按需查询：{history}。身份、预算、禁止重跑与权限以 {state_ref} 为准。\n"
    plan = (f"# 当前研究计划\n\n{goal}\n\n## 稳定设计与必要约束\n\n{view['stable_formulation']}\n\n"
            + "\n".join(f"- {x}" for x in view["constraints"])
            + f"\n\n## 上一阶段 → 下一阶段\n\n{previous['id']}：{previous['summary']}\n"
            + f"与下一步的关系：{previous['why_relevant']}\n\n"
            + f"{upcoming['id']}：{upcoming['summary']}\n为什么现在做：{upcoming['why_relevant']}\n" + footer)
    findings = (f"# 与当前决策有关的证据\n\n上一科学阶段 {previous['id']}：{previous['summary']}\n\n"
                f"最近工作 {record['work_id']}：{record['outcome']}\n\n"
                f"证据层次：{record['scientific_verdict']}。声明边界：{record['claim_boundary']}\n\n"
                + "\n".join(f"- {evidence_link(root, ref)}" for ref in record["artifacts"][:4])
                + "\n\n## 下一阶段必要的旧结论\n\n")
    findings += "\n".join(f"- {x['claim']}；现在需要它，因为 {x['needed_for_next']}。{evidence_link(root, x['evidence'])}"
                          for x in view.get("carry_forward", [])) or "无额外旧结论需要常驻。"
    findings += f"\n\n决策：{record['decision']}\n下一步依据：{record['next_step_reason']}\n" + footer
    progress = (f"# 当前进展\n\n{view['current_status']}\n\n"
                f"最近工作：{record['work_id']}（{record['work_kind']}，执行 {record['execution_status']}，科学 {record['scientific_verdict']}）。\n"
                f"实际结果：{record['outcome']}\n下一步：{upcoming['summary']}\n原因：{upcoming['why_relevant']}\n\n"
                f"运行态：{runtime.get('state')}；控制模式：{session.get('control_mode', 'legacy: use runtime snapshot')}。\n"
                f"机器 next_action：{runtime.get('next_action')}\n"
                f"预算已用：`{json.dumps(session.get('global_budget_used', {}), ensure_ascii=False)}`\n"
                f"预算剩余：`{json.dumps(session.get('global_budget_remaining', {}), ensure_ascii=False)}`\n" + footer)
    docs = dict(zip(LIMITS, (plan, findings, progress)))
    for name, text in docs.items():
        if len(text.encode("utf-8")) > LIMITS[name]:
            raise ValueError(f"{name} exceeds {LIMITS[name]} bytes: shorten snapshot; retain full detail in record")
    return docs


def render_history(root, packets, legacy_refs):
    parts = ["# 研究履历 / Research History\n\n"
             "记录实际问题、方法、执行、结果、修复、决策及贡献；用于溯源与 CV 证据核对。"
             "历史记录不是当前授权或计划；CV 表述须结合证据边界和实际个人角色。\n"]
    if legacy_refs:
        parts.append("## 既有历史入口\n\n" + "\n".join(f"- {evidence_link(root, ref)}" for ref in legacy_refs))
    navigation = ["## 导航\n\n| 工作 | 科学阶段 | 类型 | 结论 |\n|---|---|---|---|"]
    for packet in packets:
        r = packet["record"]
        navigation.append(f"| [{r['work_id']}](#event-{packet['event_id'].lower()}) | {r['scientific_stage_id']} | {r['work_kind']} | {r['scientific_verdict']} |")
    parts.append("\n".join(navigation))
    for packet in packets:
        r = packet["record"]
        parts.append(f"\n<a id=\"event-{packet['event_id'].lower()}\"></a>\n\n## {r['work_id']} · {r['date']}\n\n"
                     f"科学阶段：{r['scientific_stage_id']}；工作类型：{r['work_kind']}。\n\n"
                     f"**问题与动机。** {r['question']}\n\n{r['motivation']}\n\n**方法。** {r['method']}\n\n"
                     + "\n".join(f"- {action}" for action in r["actions"])
                     + f"\n\n**结果。** {r['outcome']}\n执行：{r['execution_status']}；科学：{r['scientific_verdict']}。\n\n"
                     f"**声明边界。** {r['claim_boundary']}\n\n**修复与异常。**\n\n"
                     + ("\n".join(f"- 原因：{x['cause']}；动作：{x['action']}；结果：{x['result']}" for x in r["debug"]) or "无已记录修复。")
                     + f"\n\n**决策与后继关系。** {r['decision']}\n\n{r['next_step_reason']}\n\n**实际贡献与 CV 证据。**\n\n"
                     + "\n".join(f"- {x['actor']}：{x['work']}。{evidence_link(root, x['evidence'])}" for x in r["contributions"])
                     + "\n\n**原始产物。**\n\n" + "\n".join(f"- {evidence_link(root, ref)}" for ref in r["artifacts"]))
    return "\n\n".join(parts) + "\n"


def publish(root, state, packet):
    root = Path(root).resolve()
    validate_packet(root, packet)
    # Render size checks BEFORE any event/index/document write.
    docs = render_working(root, state, packet)
    with record_lock(root):
        path = root / INDEX
        index = read_json(path) if path.exists() else {"schema_version": 1, "events": [], "legacy_refs": []}
        completed = (state["research_runtime"].get("completed_experiment_ids") or [None])[-1]
        if completed is not None and index.get("last_completed_experiment_id") != completed:
            if packet["record"].get("experiment_id") != completed:
                raise ValueError("newly completed experiment requires its own outcome record; refresh cannot hide it")
        relative = f"artifacts/orchestration/research-records/{packet['event_id']}.json"
        event_path = local_path(root, relative)
        if event_path.exists() and read_json(event_path) != packet:
            raise ValueError("event_id already has different content; publish a linked correction event")
        if not index["events"]:
            # One adoption archive only. A crash-retry preserves the first copy.
            for name in (*LIMITS, HISTORY):
                original = root / name
                if original.is_file():
                    saved = root / "artifacts/orchestration/records-adoption" / name
                    if not saved.exists():
                        atomic_text(saved, original.read_text(encoding="utf-8-sig"))
                    pointer = saved.relative_to(root).as_posix()
                    if pointer not in index["legacy_refs"]:
                        index["legacy_refs"].append(pointer)
        else:
            # Preserve manual edits before replacing generated views. Content-based
            # names make crash retries idempotent and keep this archive out of context.
            for name, previous_digest in index.get("published_digests", {}).items():
                if name not in (*LIMITS, HISTORY):
                    continue
                original = root / name
                if not original.is_file():
                    continue
                content = original.read_text(encoding="utf-8-sig")
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if digest != previous_digest:
                    saved = root / "artifacts/orchestration/records-edits" / f"{digest[:20]}-{name}"
                    if not saved.exists():
                        atomic_text(saved, content)
                    pointer = saved.relative_to(root).as_posix()
                    if pointer not in index["legacy_refs"]:
                        index["legacy_refs"].append(pointer)
        atomic_text(event_path, json_text(packet))
        if relative not in index["events"]:
            index["events"].append(relative)
        packets = [read_json(local_path(root, ref)) for ref in index["events"]]
        # Current is latest recorded event, even when retrying an older publication.
        current = packets[-1]
        docs = render_working(root, state, current)
        history = render_history(root, packets, index["legacy_refs"])
        for name, text in docs.items():
            atomic_text(root / name, text)
        atomic_text(root / HISTORY, history)
        stages = {p["record"]["scientific_stage_id"] for p in packets if p["record"]["work_kind"] in {"value", "mechanism"}}
        streak = 0
        for item in reversed(packets):
            if item["record"]["decision_changed"]:
                break
            streak += 1
        index.update(current_event=current["event_id"], current_work_id=current["record"]["work_id"],
                     scientific_stage_count=len(stages), work_count=len(packets),
                     no_information_streak=streak, review_recommended=streak >= 3,
                     last_completed_experiment_id=(state["research_runtime"].get("completed_experiment_ids") or [None])[-1],
                     history_path=HISTORY, document_limits=LIMITS)
        index["published_digests"] = {
            name: hashlib.sha256(text.encode("utf-8")).hexdigest()
            for name, text in {**docs, HISTORY: history}.items()
        }
        # Index last is commit marker. check detects an interrupted multi-file publication.
        atomic_text(path, json_text(index))
    return {"status": "published", "event_id": current["event_id"], "work_count": len(packets),
            "scientific_stage_count": len(stages), "review_recommended": streak >= 3,
            "document_bytes": {name: len(text.encode('utf-8')) for name, text in docs.items()}, "history_path": str(root / HISTORY)}


def check_records(root, state=None):
    root = Path(root).resolve()
    if not (root / INDEX).exists():
        return {"status": "not_adopted", "issues": ["publish the first adjacent-stage record at the next safe boundary"]}
    try:
        index = read_json(root / INDEX)
        if not index["events"]:
            raise ValueError("no published events")
        current = read_json(local_path(root, index["events"][-1]))
        issues = []
        if index.get("current_event") != current["event_id"]:
            issues.append("publication index is stale")
        for name, limit in LIMITS.items():
            path = root / name
            if not path.is_file() or path.stat().st_size > limit:
                issues.append(f"{name} missing or over budget")
        if not (root / HISTORY).is_file():
            issues.append("research history missing")
        for name, digest in index.get("published_digests", {}).items():
            if name not in (*LIMITS, HISTORY):
                continue
            path = root / name
            if path.is_file() and hashlib.sha256(path.read_text(encoding="utf-8-sig").encode("utf-8")).hexdigest() != digest:
                issues.append(f"{name} has edits: reconcile their meaning before automatic refresh; edits will be archived")
        if state is not None:
            last = (state["research_runtime"].get("completed_experiment_ids") or [None])[-1]
            if index.get("last_completed_experiment_id") != last:
                issues.append("latest completed work has not been recorded")
            expected = render_working(root, state, current)
            for name, text in expected.items():
                if not (root / name).is_file() or (root / name).read_text(encoding="utf-8-sig") != text:
                    issues.append(f"{name} requires automatic refresh")
        return {"status": "needs_refresh" if issues else "pass", "issues": issues,
                "current_work_id": index.get("current_work_id"), "scientific_stage_count": index.get("scientific_stage_count"),
                "review_recommended": index.get("review_recommended", False)}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"status": "needs_refresh", "issues": [str(exc)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("publish", "check", "refresh"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--packet", help="Brain-authored record/snapshot JSON; see references/research-records.md")
    parser.add_argument("--generation", type=int)
    parser.add_argument("--task-id")
    args = parser.parse_args()
    try:
        from runtime_control import load_runtime, assert_active
        root, unused, state = load_runtime(args.root)
        if args.command == "check":
            result = check_records(root, state)
        else:
            if args.generation is None:
                raise ValueError("record writes require --generation")
            assert_active(state, args.generation, args.task_id)
            if args.command == "refresh":
                index = read_json(root / INDEX)
                packet = read_json(local_path(root, index["events"][-1]))
            elif args.packet:
                packet = read_json(args.packet)
            else:
                raise ValueError("publish requires --packet")
            result = publish(root, state, packet)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] in {"published", "pass"} else 2
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
