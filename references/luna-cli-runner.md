# Luna execution and transport recovery

The assignment and scientific contract are independent of transport. Prefer native `luna_worker` when available and permitted by effective project policy. Use `scripts/run_luna_worker.py` for CLI execution or validation of a native result. This script cannot create native agents: Brain uses the host's native delegation tools.

## Bounded assignment

Persist one assignment JSON with these fields, filling actual paths, constraints and finite resources:

```json
{
  "task_id": "E42-repair-2",
  "stage_id": "M3",
  "route_id": "route-a",
  "work_kind": "infrastructure",
  "result_schema_version": 2,
  "project_goal_link": "Recover the evaluator needed to measure real memory value",
  "question": "Can all evaluator arms initialize and finish?",
  "decision_affected": "Whether the existing planned comparison can execute",
  "inputs": ["Exact fixture and evaluator paths"],
  "scientific_contract": {"comparison": "All-arm replayable engineering fixtures", "success": "All fixtures pass", "negative": "No scientific negative from engineering failure", "inconclusive": "Unresolved execution remains not_assessable"},
  "allowed_actions": ["Repair per-arm initialization and run local fixtures"],
  "forbidden_actions": ["No external calls", "No target access", "No changes to parent scope or protected identities"],
  "write_scope": ["artifacts/experiments/E42-repair-2"],
  "method": ["Reproduce", "Diagnose", "Repair", "Validate all fixture arms"],
  "result_path": "artifacts/experiments/E42-repair-2/result.json",
  "targeted_sanity_check": "All arms have independent initialized state",
  "resource_ceiling": {"wall_minutes": 20, "external_calls": 0},
  "repair_authority": {
    "allowed": ["Execution-preserving repairs inside write_scope"],
    "forbidden": ["Scientific-contract and authorization changes"],
    "debug_cycle_limit": 3,
    "required_sequence": ["reproduce", "minimize", "diagnose", "repair", "targeted_validate", "resume_original_experiment"]
  },
  "stopping_rule": "Stop after the result and sanity check, or at the resource ceiling"
}
```

Engineering completion resumes only the authorized engineering test. It does not authorize a protected scientific run. Scientific assignments specify the real comparison, data roles, metrics and different decisions for each result. Tell writing workers they are not alone and must preserve others' edits; isolate parallel writers in independent worktrees.

## Native path

1. Run runtime `assert-dispatch`, reconcile record freshness and reserve resources. Confirm no duplicate or uncertain execution exists.
2. Invoke native delegation with the configured `luna_worker` role and complete bounded assignment. Its configured identity must be `gpt-5.6-luna` / `max`. Use the returned actual agent ID to follow up/wait. A name containing Luna is insufficient evidence.
3. Brain records a small receipt from exposed role configuration and actual native tool response; never ask the worker to attest its own identity. For example:

```json
{
  "receipt_schema_version": 1,
  "transport": "native_codex_thread",
  "native_agent_id": "actual-agent-id",
  "configured_role": "luna_worker",
  "model": "gpt-5.6-luna",
  "reasoning_effort": "max",
  "platform_evidence": {
    "source": "native delegation tool response plus exposed role configuration",
    "verified": true,
    "verified_by": "actual-Brain-id",
    "agent_id": "actual-agent-id",
    "role": "luna_worker",
    "model": "gpt-5.6-luna",
    "reasoning_effort": "max"
  }
}
```

This is a Brain-attested receipt of platform evidence, not cryptographic proof. Preserve the actual source pointer when available; do not fabricate unavailable configuration.

```powershell
python scripts/run_luna_worker.py --accept-native --assignment "<assignment.json>" --workdir "<worktree>" --runtime-receipt "<receipt.json>"
```

This validates existing files and writes a run record; it neither launches nor repeats the task. `--validate-only` validates results without claiming verified native execution. Brain inspection of scope and decisive artifacts remains necessary.

## CLI path

```powershell
python scripts/run_luna_worker.py --assignment "<assignment.json>" --workdir "<worktree>" --sandbox workspace-write --timeout-seconds 3600 --dry-run
```

Remove `--dry-run` to execute after ownership, policy and budget checks. Discovery/catalog preflight does not launch the assigned worker. The runner uses ephemeral `codex exec` with exact Luna/max identity; this is not a native subagent. Workspace writes use the CLI's supported approval mechanism. Broader sandbox access requires matching authority and the explicit flag; never use it to bypass a denial.

Run records live under `artifacts/orchestration/luna-cli-runs/<task_id>/`; validation records under `luna-validation-runs/`. Track transport, actual agent/process/cell ID, assignment, worktree, result and run record in active runtime work. Wait on the exact live execution; do not relaunch because an outer tool yielded. Use bounded waits compatible with host communication requirements.

## Result contract and acceptance

For new assignments request `result_schema_version: 2`. Retain required legacy descriptive fields (see `REQUIRED_RESULT_KEYS` in the runner), plus `execution_status`, `evidence_validity`, `scientific_verdict`, and `work_kind`. Bind result identity to assignment. Interpret these separately:

| Work/result | Execution | Evidence | Scientific verdict |
|---|---|---|---|
| Valid evaluated comparison | completed | valid | success / negative / mixed / inconclusive |
| Valid engineering/documentation work | completed | valid | not_assessable |
| Unresolved execution blocker | blocked | not_assessed | not_assessable |
| Invalid execution/evidence | invalid, or completed with invalid evidence | invalid | not_assessable |

For compatibility, `status` equals the scientific verdict for interpretable science, `completed` for valid engineering/documentation, and `blocked`/`invalid` for those dispositions. `run.json.status=accepted` means transport/result checks passed; it never establishes scientific success. Legacy results can be inspected with explicit normalization warnings; do not silently rewrite their old meanings into v2.

Record `debug.encountered`, `attempts`, and when applicable root cause, failure class, resolution and original-execution resumption. Each attempt declares `kind: repair_cycle | diagnostic_read | probe`; count substantive repairs, with consistent `local_repair_cycles` and `completed_repair_cycles`. Count discrepancies become explicit contract deviations requiring reconciliation, not automatic deletion or experiment replay. A successful result cannot hide an unresolved blocker.

## Failure handling

Read structured `failure_phase`, error code, `dispatch_started`, `side_effect_uncertain` and `safe_next_action` first. Preserve sanitized bounded logs and valid artifacts.

- Discovery/catalog or supported-interface failure before dispatch: minimally diagnose. If already authorized, select a supported native/CLI path with verified identity. Fix the transport; keep the intended question and comparison.
- Authorization/safety denial: report the exact boundary and do not evade it through another transport. Resolve a permission projection mismatch under existing authority through normal supported controls.
- Timeout, process uncertainty or partial external request: reconcile execution, retained responses and cost first. Never automatically resend through a second transport.
- Response/schema/adapter error: inspect permitted redacted structure, reproduce locally, repair parsing/initialization, validate the original path. Reparse retained responses when possible instead of spending another target request.
- Result-contract discrepancy: reconcile metadata in place. Isolate only affected evidence; preserve valid artifacts and existing quarantines.

Bound repairs by substantive cycles and resources. Escalate with a better diagnosis or pause for review when no worthwhile authorized path remains; do not replace a decisive broken component with a proxy and claim the original question answered.
