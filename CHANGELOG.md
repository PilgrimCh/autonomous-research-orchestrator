# Changelog

All notable changes to this skill are documented here. Dates use `YYYY-MM-DD`.

## Unreleased — 2026-09-05

### Added

- Automatic research-record publishing. Each closed work item can now produce an immutable event packet, a compact adjacent-stage snapshot, and a regenerated `research_history.md`; the history preserves evidence links, failures, decisions, and user/agent contribution attribution for later review or CV preparation.
- `research_records.py` with `publish`, `check`, and `refresh` commands. It validates evidence pointers, keeps publications idempotent, preserves user edits before regeneration, enforces working-document size limits, and never resumes research or alters runtime state.
- Explicit runtime control modes for user pauses, review pauses, platform blocks, and terminal states, plus pause/resume commands and a compact runtime snapshot.
- Budget reservation, finalization, reconciliation, and overrun accounting so cumulative finite resources survive handoffs without double counting.
- Strict v2 assignment/result contracts that distinguish execution status, evidence validity, and scientific verdict; native-worker receipts are also validated against the required role, model, and reasoning effort.
- Regression tests for record publication, runtime control, and Luna-runner validation.

### Changed

- Reframed the orchestration policy around one active scientific route, explicit milestone consequences, cheapest discriminating experiments, and mandatory review triggers for low-information continuation.
- Made record maintenance part of normal work-item closure. The three main documents now retain only stable and adjacent-stage context, while the full traceable narrative lives in `research_history.md`.
- Strengthened rollover and handoff handling with a compact handoff requirement, preserved pause/ownership/budget state, configurable compaction thresholds, and explicit platform-blocked outcomes.
- Unified native Luna and CLI Luna around the same scientific contract and recovery sequence; transport failures are classified and repaired without silently substituting the configured experimental worker.
- Updated initialization, migration, validation, templates, and documentation to adopt the record workflow prospectively while preserving existing results and protected identities.

### Compatibility

- Existing projects are adopted conservatively: prior main documents are preserved once and historical work is linked rather than reconstructed or rerun.
- Legacy result records remain readable, but new strict assignments and results use the v2 contract.

## 中文摘要

本次更新将技能从“长任务编排”扩展为可追溯、可恢复的研究运行时：每个工作项结束后可自动发布不可变记录、刷新短主文档并生成完整研究履历；新增暂停/恢复、预算预留与结算、严格的 worker 结果与原生回执校验；同时将科学阶段、执行状态、证据有效性和科学结论分离记录。旧项目采用保守迁移：保留原文档和已有结果，只建立面向后续工作的紧凑快照，不追溯重跑或重写历史。
