# Runtime and Brain Rollover

## Authoritative runtime

Use schema v4 to separate:

- `autonomy_session`: session identity, project goal/status, cumulative limits/use/remaining, standing authorization;
- `brain_runtime`: active and previous generations/task IDs, append-only Brain task lineage, context health, rollover status/count, handoff path;
- `research_runtime`: session state, active stage/routes/experiments/Luna tasks, completed experiments, next action;
- `integrity_runtime`: invalidated artifacts and unresolved integrity issues.

The research session crosses stages and Brain tasks. The state file—not conversational memory—is authoritative for research control, while each retained Codex task remains the authoritative full transcript for that Brain generation.

Before every scientific decision, Luna CLI launch, or shared record/state write, run:

```powershell
python scripts/runtime_control.py assert-active --root "<project-root>" --generation <n> --task-id "<current-task-id>"
```

Omit `--task-id` only when the platform cannot expose the current task ID. Generation remains mandatory. A stale Brain must stop immediately without spawning or writing.

## Context health

Use `healthy`, `rollover_recommended`, or `rollover_required`. Context health is monotonic within one Brain generation and resets only after ownership transfers to its successor.

Treat a platform-created conversation compaction/automatic summary as an observable event. Count it exactly once when the current Brain first sees the compacted context:

```powershell
python scripts/runtime_control.py record-context-compaction `
  --root "<project-root>" `
  --generation <n> `
  --task-id "<current-task-id>" `
  --event-id "<platform-event-id-if-available>"
```

Use a platform event ID when available so retrying the command is idempotent. If the platform exposes no ID, omit it and invoke the command exactly once. Count only a new platform compaction of the active Brain task—not a handoff, a user summary, a reread, or a summary written by Brain itself. Never infer unobserved historical compactions.

Apply this deterministic ladder per Brain generation:

| New compactions observed | Required state | Action |
|---|---|---|
| 0 | `healthy` unless another degradation signal is stronger | Continue normally. |
| 1 | at least `rollover_recommended` | Continue useful work but favor a clean boundary and keep authoritative records current. |
| 2 | `rollover_required` | Dispatch no new scientific task; finish the active Luna safely, write the handoff, and roll over. |

The second compaction is a hard rollover trigger, not merely a suggestion. Other observable signals may require an earlier rollover: repeated rereads to recover recently active state, conflict between superseded details and current records, uncertainty about the latest result/active work/authorization, or a concrete risk that context degradation is changing scientific judgment. Raise health explicitly with `set-context-health --health rollover_required --reason "<observable reason>"`.

Do not rollover every stage. Do not reset or lower the compaction count because Brain reread the state successfully. Transfer resets the successor to zero compactions and `healthy`.

## Compact handoff

Overwrite the single `artifacts/orchestration/brain_handoff.md`. Include only the source and successor generations/task IDs, project goal/status, formulation, accepted findings that matter now, active route, latest result, direction rationale, unresolved questions, next action, running Luna tasks, authorization, remaining global budget, forbidden actions, invalidated/superseded work, and immediate resume instruction.

Do not copy chat history, long reasoning, every old experiment, or a handoff archive. Do not audit or hash the handoff. Compression controls what the successor loads; it does not replace, delete, or hide the predecessor's Codex transcript.

## Conversation preservation invariant

Keep every predecessor Brain task unarchived and readable in the same saved Codex project. Never call `set_thread_archived`, delete a task, move it out of the project, or reuse it as the successor during automatic rollover. Before transfer, give the predecessor a stable title such as `[History] <project-title> — Brain <n>` and create the successor with `<project-title> — Brain <n+1> (Active)`.

Maintain `brain_runtime.brain_task_lineage` as append-only metadata. Each entry records generation, task ID, `active | retired` status, predecessor task ID, and successor task ID. Transfer changes the current entry from `active` to `retired` and appends exactly one new `active` entry; it never removes an older entry. The existing `previous_brain_*` fields remain a convenience pointer, not the historical record.

For an older schema-v4 state created before lineage existed, seed the known predecessor from `previous_brain_*` and the current Brain on the next transfer. Preserve only task IDs established by state or native thread metadata; never invent missing earlier generations.

The successor normally reads only the compact handoff and minimal project records. It may verify the predecessor task ID/title through native thread metadata without loading the old transcript. Read the old transcript only when the user asks or when a concrete recovery, provenance, or contradiction investigation requires it.

## Safe transfer protocol

Prefer rollover at a boundary with no active Luna CLI process. If Luna is running, let that exact process or shell cell reach a safe completion point and record the result; do not cancel, duplicate, or attempt to migrate it into another Brain.

1. Old Brain stops dispatching new scientific work.
2. Old Brain writes the compact handoff with its own generation/task ID, `rollover_reason`, observed source-generation compaction count, and all current results/budgets.
3. Old Brain runs:

   ```powershell
   python scripts/runtime_control.py prepare-rollover --root "<project-root>" --generation <n>
   ```

4. Resolve the saved Codex project with `list_projects`. Rename the predecessor to `[History] <project-title> — Brain <n>`. Create exactly one successor using native `create_thread` in the same saved project with `environment: local` and title `<project-title> — Brain <n+1> (Active)` so both generations remain visible and share the authoritative root. This successor is the sole allowed new top-level research task; it is not a Worker. User activation of an autonomy session with `brain_rollover: true` is the explicit request permitting it.
5. Give the successor only the absolute project root, skill name, session ID, predecessor task ID, pending generation, state path, handoff path, and this instruction: wait until the state marks that generation active, then assert ownership, read minimal authoritative records, and resume the outer loop without user input.
6. After `create_thread` returns the successor task ID, add it to the compact handoff, then atomically transfer ownership. `transfer-brain` retires the predecessor in append-only lineage and appends the successor:

   ```powershell
   python scripts/runtime_control.py transfer-brain --root "<project-root>" --generation <n> --successor-task-id "<thread-id>"
   ```

7. Old Brain performs no further scientific or shared-state action. Leave its task unarchived, emit the created-task link/directive and both task IDs in its final response, and end its turn. Retirement means loss of write authority only.
8. Successor asserts generation `n+1`, verifies that state lineage names both tasks, reads `task_plan.md`, `findings.md`, `progress.md`, `pipeline_state.json`, `brain_handoff.md`, and only the latest needed result, then immediately resumes. Do not load the predecessor transcript merely to reconstruct state.

The two-phase `prepared -> transferred` protocol makes a successor wait rather than race. The compare-and-set generation check prevents the retired Brain from writing after transfer.

## Platform limitation fallback

Native automatic rollover requires callable thread creation, title management, and a saved Codex project that can open the same local research root. Do not use a same-directory fork that copies the long conversation merely to simulate compact continuation. If title management is unavailable, preserve both task IDs and continue with platform-generated titles; visibility and lineage matter more than naming.

If native creation is unavailable, complete the handoff/state machinery, set `ROLLOVER_PLATFORM_BLOCKED`, and report this single platform limitation accurately. Do not weaken the outer loop, reset the session, or pretend a new Brain was created.

## Resume invariants

Across rollover preserve exactly:

- session ID, project goal/status, formulation, standing authorization;
- global limits, used values, and remaining values;
- route states, active/completed experiments, results, invalidations, active Luna identities;
- every Brain generation/task ID in append-only lineage and every predecessor transcript;
- stage count and next action.

Increment only Brain generation and rollover count. Rollover is runtime maintenance and never a scientific stage.
