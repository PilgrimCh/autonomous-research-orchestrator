# Luna CLI runner

Use the logical `luna_worker` role through `scripts/run_luna_worker.py`. This is a compatibility transport built on an ephemeral local `codex exec` process; it is not a native Codex subagent thread. It will not appear in the Subagents panel and cannot be controlled with native subagent follow-up or wait operations.

## Assignment input

Persist one self-contained JSON assignment per task. It must include:

```json
{
  "task_id": "route-a-scout-01",
  "stage_id": "stage-01",
  "route_id": "route-a",
  "project_goal_link": "How this task advances the final goal",
  "question": "One bounded research question",
  "decision_affected": "The decision this evidence can change",
  "inputs": ["Exact files or sources"],
  "scientific_contract": {"comparison": "...", "success": "...", "negative": "...", "inconclusive": "..."},
  "allowed_actions": ["Specific permitted actions"],
  "forbidden_actions": ["No scope expansion", "No external calls unless authorized"],
  "write_scope": ["artifacts/experiments/route-a-scout-01"],
  "method": ["Bounded execution steps"],
  "result_path": "artifacts/experiments/route-a-scout-01/result.json",
  "targeted_sanity_check": "One decision-relevant check",
  "resource_ceiling": {"wall_minutes": 20, "external_calls": 0},
  "repair_authority": "Routine execution-preserving fixes inside write_scope only",
  "stopping_rule": "Stop after result and sanity check, or when the ceiling is reached"
}
```

Do not place credentials or secrets in the assignment. The Brain owns its completeness and ensures that every path is inside the selected worktree.

## Launch

Run:

```powershell
python scripts/run_luna_worker.py `
  --assignment <absolute-assignment.json> `
  --workdir <absolute-worktree> `
  --sandbox workspace-write `
  --timeout-seconds 3600
```

The runner selects the newest usable versioned Codex Desktop CLI, verifies that its model catalog advertises `gpt-5.6-luna` and reasoning `max`, and invokes `codex exec --ephemeral` with that exact identity. For the default write policy it uses CLI-managed automatic review (`--approve-for-me`); this CLI version may report a read-only base sandbox plus `approval: on-request`, while approved in-workspace writes still succeed. It never silently falls back to Sol, Terra, or another model. `danger-full-access` requires both explicit matching authorization and `--allow-danger-full-access`.

Set the outer launch/tool timeout so the runner remains blocking through the task's genuine timeout whenever the platform supports it. Do not choose a short timeout merely to regain Brain control and inspect progress.

Use `--dry-run` before the first task in a runtime. For parallel writing workers, use independent worktrees with non-overlapping ownership. Without write isolation, use one writer at a time.

## Acceptance

The runner writes `run.json`, CLI logs, and the last model message beneath `artifacts/orchestration/luna-cli-runs/<task_id>/` unless `--run-dir` is supplied. Accept a worker result only when:

- `run.json.status` is `accepted`;
- runtime identity verifies `gpt-5.6-luna` and reasoning `max`;
- the declared `result.json` exists and passes its required-key contract;
- Brain inspection confirms the expected scope, artifacts, and scientific sanity check.

Track live executions in `research_runtime.active_luna_tasks` with `transport: codex_cli_ephemeral`, CLI executable/version, process or shell cell, worktree, assignment path, run record, result path, and status. A task name containing `luna` is not evidence of model identity.

If a shell invocation yields a running cell, wait passively on that exact cell; do not launch a duplicate. When Brain has no independent decision-relevant work, do not poll the cell or its files, emit heartbeat/status commentary, or resume model reasoning merely to verify liveness. Use the platform's blocking wait path and resume Brain only on completion, failure, a genuine timeout that requires handling, or an exception that requires a Brain decision. On CLI discovery, catalog, timeout, identity, or result-contract failure, inspect `run.json` and logs, preserve the unresolved task, and enter `WORKER_PLATFORM_BLOCKED` only when no authorized CLI Luna path remains. Do not kill, duplicate, or custom-migrate an active Luna CLI process during Brain rollover.
