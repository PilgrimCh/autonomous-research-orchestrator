# Automatic working memory and research history

This maintenance is part of every work-item close, not a task the user must request. Brain chooses relevance; the publisher enforces size, traceability, immutable events and idempotence. No heuristic truncation may erase scientific/authorization boundaries.

## Two information layers

The three main documents are adjacent-stage working memory:

| Document | Keep | UTF-8 budget |
|---|---|---|
| task_plan.md | stable goal/formulation, mandatory constraints, relevant previous-stage conclusion, current/next stage and reason | 12 KiB |
| findings.md | previous-stage and latest-work results needed for next decision; at most five specifically justified older facts with pointers | 20 KiB |
| progress.md | short human-readable current status, latest work, outcome, next step/reason, current runtime/resources | 8 KiB |

These are ceilings, not targets. Do not retain the last five or ten tasks merely because they are recent. Keep the previous scientific stage and next/current stage; keep older material only when it changes that next decision. Stable goal, protected-data/no-replay rules, applicable authority and budget pointers remain available regardless of recency. Do not copy full ledgers into all three files.

`research_history.md` is the human research record. It preserves the full experimental narrative: question, why the work was needed, method/data/model/configuration, what actually ran, result/counts/uncertainty, failures and repairs, what the result permits claiming, decision and why it leads to the next work, and contribution attribution. It links original code/results/ledgers and Brain transcripts. Full process means no missing substantive step or lost evidence; it does not require pasting every console line. Zero-outcome setup, failed experiments and abandoned directions are recorded honestly.

This history is also the reference for CV research descriptions. Record concrete verbs (designed, implemented, evaluated, diagnosed), actual scale, method and artifact; distinguish user/agent/shared/unknown attribution. A researcher may describe leading an agent-assisted project if supported, but do not invent personal implementation, publication, benchmark success or generalization. CV wording is later derived from the record with its caveats, not automatically promoted from `success` flags.

Immutable event JSON under `artifacts/orchestration/research-records/` and `records_index.json` generate the Markdown. History is not current runtime authority and is never automatically loaded in full on resume. Query by work/stage/question or contribution when tracing or preparing a CV.

## Automatic publication

After Brain accepts a result or records a blocked/invalid disposition, first settle runtime accounting, then author one small JSON packet and run:

```powershell
python scripts/research_records.py publish --root "<root>" --generation <n> --task-id "<Brain-id>" --packet "<packet.json>"
```

The packet has the following shape (adapt content, keep fields):

```json
{
  "event_id": "E42-attempt-2",
  "record": {
    "work_id": "E42-repair-2",
    "experiment_id": "E42",
    "scientific_stage_id": "M3",
    "work_kind": "infrastructure",
    "date": "2026-09-05",
    "question": "Can the frozen evaluator finish all arms?",
    "motivation": "An uninitialized state blocked target access.",
    "method": "Pure fixtures over every arm; exact code/config and counts in linked result.",
    "actions": ["Initialized per-arm state", "Ran all-arm end-to-end fixtures"],
    "execution_status": "completed",
    "scientific_verdict": "not_assessable",
    "outcome": "Fixtures pass; no target outcome measured.",
    "claim_boundary": "Implementation recovery only; no utility claim.",
    "decision_changed": true,
    "decision_evidence": "All-arm fixtures now finish with independent state objects.",
    "debug": [{"cause": "Missing state initialization", "action": "Initialize each arm", "result": "Fixtures pass"}],
    "decision": "Use the repaired runner for the same planned experiment.",
    "next_step_reason": "The implementation blocker is removed; value is still untested.",
    "artifacts": ["artifacts/experiments/E42/result.json"],
    "contributions": [
      {"actor": "agent", "work": "Implemented and verified isolated arm initialization", "evidence": "artifacts/experiments/E42/result.json"}
    ]
  },
  "snapshot": {
    "stable_formulation": "Compare the frozen memory policy with the same agent without history.",
    "constraints": ["Protected confirmation remains locked; exact identities in runtime ledger."],
    "previous_stage": {"id": "M2", "summary": "Public validity prevents some bad actions.", "why_relevant": "It has not demonstrated task reward gain."},
    "next_stage": {"id": "M3", "summary": "Complete the predeclared paired value experiment.", "why_relevant": "A full result can change the route decision."},
    "current_status": "Evaluator repaired; no target outcome yet. Continue only when runtime permits.",
    "carry_forward": []
  }
}
```

`work_kind` is value/mechanism/infrastructure/documentation. Only unique stages with value/mechanism records count as scientific stages; repairs do not multiply the count. `scientific_verdict` is success/negative/mixed/inconclusive/not_assessable. Blocked/invalid execution must be not_assessable. `experiment_id` must match newly completed runtime work so old records cannot mask an unrecorded result. `carry_forward` entries contain claim, needed_for_next, evidence. Local evidence pointers must exist within the project; HTTPS and known `codex-task:<id>` references are supported without fetching them.

The publisher replaces all three main documents, rebuilds history from immutable events, and stores one index commit marker. A duplicate event is idempotent; changing a prior event requires a new correction event citing the original. The tool preserves existing main/history files once in `records-adoption/`. It never edits pipeline state, spends resources, resumes work or runs an experiment.

If a user or another tool edits generated documents after publication, `check` detects the change. Read and reconcile the substantive edit into the next packet before refresh; automatic regeneration also archives the changed document in `records-edits/` and links it from history, so edits are not lost. This content check serves document preservation only, not a scientific evidence audit.

If a snapshot is oversized, shorten only its working-memory fields and retain all detailed process in `record`. This is Brain's routine work; do not ask the user to prune it. If interrupted, retry the same unchanged packet; do not rerun science. Old event IDs cannot be silently overwritten.

## Resume and dispatch checks

```powershell
python scripts/research_records.py check --root "<root>"
python scripts/research_records.py refresh --root "<root>" --generation <n> --task-id "<Brain-id>"
```

`check` reads the index/current packet, checks sizes and current-state freshness, and reports `needs_refresh` or `not_adopted`. It does not read all history into context. `refresh` regenerates current views after a pause/budget/control change without duplicating the history. It refuses to conceal newly completed work that needs a new outcome record.

After adoption, dispatch checks require current records. Repair records automatically before dispatch; do not turn maintenance into a numbered scientific task or user approval gate. A no-information streak of three work items recommends Goal Mode review; it does not prove infeasibility, reset budgets, or authorize new scope.

## First adoption and historical coverage

Initialize new projects with a history entrypoint. For existing projects, seed only an accurate adjacent-stage snapshot from accepted records and the current stop/authorization state. Link the preserved main documents, previous full research notes, original experiments and Brain transcripts as the pre-adoption history. Be explicit that older work is linked rather than newly reconstructed. Do not fabricate missing details or personally attributed contributions. A complete retrospective narrative is an independent documentation request, not a prerequisite to resuming research.

When a new scientific result or direction change occurs, send the user a concise question/work/outcome/implication/next-step explanation. That communication is part of closing the work, not an extra status task. For CV requests, retrieve relevant history plus primary evidence and attribution, then write conservatively.
