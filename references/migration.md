# Initialize, adopt and resume

Normal entrypoints: `task_plan.md`, `findings.md`, `progress.md`, a read-only runtime snapshot, and the latest needed result. `research_history.md` is the detailed experimental/contribution record, retrieved by question rather than loaded in full. `brain_handoff.md` is read only for rollover. The JSON-compatible `research_pipeline.yaml` adapter identifies runtime paths.

## New project

Preview then apply:
```powershell
python scripts/init_autonomous_research.py --root "<root>" --project-id "<id>" --title "<title>" --goal "<goal>" --enable-autonomy
```
Repeat with `--apply` after inspecting the plan. Defaults: 240 wall minutes, eight worker assignments, zero external calls/cost. Use applicable user limits, not invented authority. The initializer creates missing main documents, research-history entrypoint, adapter and state. It never creates a scientific result or handoff.

After initialization, the first Brain record publication adopts automatic adjacent-stage views. Native Luna is allowed as a local worker transport by default in new sessions, subject to actual platform availability; external/model permissions are unchanged.

## Existing projects

For schema v2/v3 use `--migrate --apply`. Preserve a single recoverable adapter/state backup, identity, outcomes, invalidations, active work, cumulative usage and existing user stops. Do not replay experiments to make history tidy. Preserve external authorization evidence and apply only current applicable ceilings.

For schema v4 do not reinitialize or reset the runtime to adopt these improvements. Read `runtime_control.py snapshot`; reconcile an actual policy conflict using the latest user instruction and its scope. Old narrative amendments remain source evidence until a structured effective policy records the resolution. Never turn a generation-specific threshold into a global policy.

At the next safe boundary, follow [research-records.md](research-records.md): author a current record/snapshot packet, publish it, preserve pre-adoption files once, and link existing full research notes/artifacts/transcripts as the older history. Do not retroactively synthesize unverified personal contributions or reinterpret invalid experiments. Detailed historical backfill is separate from adopting automatic future maintenance.

No historical Task ID is renumbered. New repairs/work items remain under their scientific milestone. Existing protected and no-replay commitments remain binding; replayable engineering roles apply only to new allocations.

## Source precedence

1. Applicable explicit user instructions.
2. Effective runtime for ownership, control mode, budget, authorization and active work.
3. Short plan for stable formulation and next-stage strategy.
4. Relevant findings for accepted evidence and claim boundaries.
5. Progress for human-readable previous/current/next status.
6. History/artifacts/transcripts for targeted traceability and contributions.

Resolve only contradictions affecting the next decision. Main-document upkeep is automatic and cannot resume paused research. Do not create routine audit packets, duplicate control ledgers or report-per-worker documents; the single generated history and original artifacts provide traceability.

Retired `advance-research-stage` and `visible-research-orchestrator` names are migration markers only; do not reactivate them.
