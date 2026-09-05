# Runtime control and compact Brain handoff

Schema v4 separates session authority/resources, Brain ownership/context, research work, and integrity. Preserve existing state; do not reinitialize to adopt these changes. The state governs control, while retained Codex tasks hold full transcripts.

## Read and dispatch

```powershell
python scripts/runtime_control.py snapshot --root "<root>"
python scripts/runtime_control.py assert-active --root "<root>" --generation <n> --task-id "<Brain-id>"
python scripts/runtime_control.py assert-dispatch --root "<root>" --generation <n> --task-id "<Brain-id>"
```

Snapshot is read-only and compact: current ownership, effective control/policy with source, resources, active work and record pointers. Query full state only for an unresolved authorization, protected identity or provenance question.

`assert-active` checks ownership for maintenance, accounting and acceptance, including while paused. A stale Brain stops shared writes and spawning. `assert-dispatch` additionally requires RUNNING, a usable context, no unresolved budget overrun, and current main documents after record adoption. Refresh documents automatically when stale. It does not itself launch work or replace transport-specific authorization and resource reservation.

An absent legacy `control_mode` is inferred from the existing stop/terminal markers. The session's `enabled` flag is not permission to resume after a user stop. Effective transport policy prefers current typed policy over recognized prior user authorization over old defaults; preserve each authorization's scope. Unrecognized prose is not automatically parsed into permission. Resolve an applicable explicit instruction into typed policy with its source before dependent actions.

## Pause and explicit resume

```powershell
python scripts/runtime_control.py pause --root "<root>" --generation <n> --task-id "<Brain-id>" --mode PAUSED_USER --reason "<user stop>"
python scripts/runtime_control.py resume --root "<root>" --generation <n> --task-id "<Brain-id>"
```

Run `resume` only for explicit user resumption, or for a non-user pause whose existing authorized unblock condition has actually been satisfied. Never infer resumption from skill editing, history maintenance, review, compaction or ownership transfer. A user-pause latch requires explicit user resume even if a later platform/review pause was also recorded.

Pause blocks new dispatch while preserving an active worker's identity. Drain or interrupt according to the user's stop instruction; receipt acceptance, cost settlement and records may finish. Never launch replacement work as part of draining. PAUSED_PLATFORM and PAUSED_REVIEW carry their actual unblock condition; TERMINAL cannot resume by this command.

## Cumulative resource accounting

For every new dispatch reserve finite amounts under a stable work/attempt key:

```powershell
python scripts/runtime_control.py reserve-budget --root "<root>" --generation <n> --task-id "<Brain-id>" --idempotency-key "<work-attempt-id>" --wall-minutes 20 --worker-assignments 1 --external-calls 0 --external-cost-usd 0
python scripts/runtime_control.py finalize-budget --root "<root>" --generation <n> --task-id "<Brain-id>" --idempotency-key "<work-attempt-id>" --wall-minutes 12 --worker-assignments 1 --external-calls 0 --external-cost-usd 0
```

Settlement arguments are actual amounts, not deltas. Repeating an identical operation must not double-charge. Active reservations reduce available capacity. Uncertain execution keeps a conservative charge (`--uncertain`); after inspecting execution evidence, use `reconcile-budget` with the same owner/idempotency key and confirmed actual amount flags to replace that charge exactly once. Never declare zero usage because a request timed out. Unexpected actual overrun stays recorded and blocks further dispatch for review. Legacy `record-budget` remains available for old unreserved accounting; do not also use it for a reserved task.

State mutations use an operating-system exclusive lock, re-read under lock and atomic replacement. The lock releases when the process exits; the remaining lock file is not proof of an active writer. Document publication uses its own exclusive marker: after a crash verify no publisher remains before removing that stale marker. Never blindly steal a live lock.

## Context health

Count each newly observed platform compaction exactly once:

```powershell
python scripts/runtime_control.py record-context-compaction --root "<root>" --generation <n> --task-id "<Brain-id>" --event-id "<observed-platform-event-id>"
```

If the platform exposes no event ID, omit it and invoke once for that observed event. Do not count user summaries, handoffs, rereads or invented historical compactions. Context health is monotonic within a generation. One compaction recommends a clean boundary; `brain_runtime.rollover_threshold` determines the hard trigger (default 2). A generation-specific amendment applies only to its stated generation; it is not automatically global policy.

Observable degradation can require an earlier rollover using `set-context-health --health rollover_required --reason "<evidence>"`. At the hard trigger dispatch no new science, finish current authorized work safely, update records and prepare handoff. Do not create a stage for rollover. Do not lower the count by rereading state.

## Transfer protocol

Keep one compact `artifacts/orchestration/brain_handoff.md`, using the template. Include goal and current formulation, the relevant preceding result, next question/reason, latest artifact, source/successor identities, control mode and saved action, effective authority/source, remaining resources/reservations, active workers, protected boundaries and history pointer. Do not paste long history or every ledger.

1. Reach a safe boundary with no active worker that would be lost or duplicated. The old Brain stops dispatch.
2. Write the compact handoff, including any user pause and the instruction to preserve it.
3. Run `prepare-rollover --root "<root>" --generation <n> --task-id "<Brain-id>"`.
4. Create a successor only when the user has explicitly requested automatic successor tasks (including an applicable standing request), the host allows it, and project policy permits rollover. Merely invoking this skill or finding `brain_rollover: true` is not a substitute for the user request required by the host tool. Do not request permission again when that request is already established. Resolve the saved project and use the same local research root, with a compact starting prompt.
5. Preserve predecessor and successor as separate readable, unarchived tasks in the project. Rename for clarity if available. Keep append-only `brain_task_lineage`; do not invent unknown historical task IDs.
   For the authorized successor creation, explicitly pass `model: "gpt-6-astra"`; preserve the user's reasoning-effort setting and verify the returned task configuration. Record the intended Brain model in the handoff. If Astra is unavailable, report that concrete platform limitation rather than silently substituting another Brain model.
6. The successor waits until state names its generation and task as active. After creation returns a real task ID, update the handoff and run:

   `transfer-brain --root "<root>" --generation <n> --task-id "<Brain-id>" --successor-task-id "<new-task-id>"`
7. Old Brain ends shared writes/decisions after transfer. Successor asserts ownership, reads snapshot, compact handoff, three main documents and latest required result. It dispatches only if the preserved control mode and authority permit; paused research stays paused.

Transfer preserves session goal/status, authority, budgets, reservations, scientific stage count, results, protected/invalidated identities and predecessor transcripts. Ownership generation and rollover count advance; successor context count resets. A paused session retains the saved next action rather than being forced to RUNNING.

If native creation is unavailable or lacks an essential explicit user request, complete the compact handoff and record the exact limitation via `mark-platform-blocked` after preparation. Do not fork a long transcript to imitate compact continuation, reset the session or claim creation succeeded. Title management alone is optional: actual task IDs and readable history are essential.
