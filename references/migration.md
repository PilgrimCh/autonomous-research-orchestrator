# Initialization, Migration, and Resume

## Minimal project records

Use only:

```text
task_plan.md
findings.md
progress.md
research_pipeline.yaml
artifacts/orchestration/pipeline_state.json
artifacts/orchestration/brain_handoff.md   # only once rollover is prepared
artifacts/orchestration/results/<experiment-id>/result.json
```

Do not create `next_experiment_prompt.md`, evolution ledgers, routine stage decisions, transition packets, audit reports, reuse ledgers, manifests, or report-per-worker records. Preserve old scientific files during migration but remove them from the active control contract.

## New initialization

Preview first:

```powershell
python scripts/init_autonomous_research.py --root "<project-root>" --project-id "<id>" --title "<title>" --goal "<goal>" --enable-autonomy
```

Then repeat with `--apply`. Defaults are finite local-only autonomy: 240 wall minutes, eight worker assignments, zero external calls, and zero external cost. Pass user-approved session ceilings explicitly when different.

The initializer creates only missing main documents and the schema-v4 adapter/state. It does not create a handoff or experiment report.

## Legacy migration

For an existing schema-v2/v3 visible pipeline, add `--migrate --apply`. The migration:

- makes at most one recoverable `.pre-autonomous-v4.bak` copy of the old adapter and state;
- preserves project identity, root, research question, route state, accepted artifacts, anomaly/invalidation records, active work, and cumulative resource use;
- maps `STAGE_CLOSED` to a nonterminal next-stage replan when the project goal is unresolved;
- maps old subagents to active Luna task records without replaying them;
- removes `next_experiment_prompt` and evolution history from current source precedence without deleting user scientific history;
- sets external remaining budget to zero unless newly authorized;
- never computes or stores cryptographic identities;
- never scans the repository or validates document headings.

The former `advance-research-stage` and `visible-research-orchestrator` skill directories are retired and may be absent. Treat their names only as legacy migration markers; never call or recreate them from the unified loop.

## Lightweight resume

Read the adapter/state plus `task_plan.md`, `findings.md`, and `progress.md`. Read the current handoff only when `brain_runtime.handoff_status` indicates rollover. Read the latest active result when needed. Do not read the full historical tree or old conversations.

Resolve conflicts by precedence:

1. latest explicit user instruction;
2. schema-v4 runtime for current ownership/budgets/authorization/active work;
3. `task_plan.md` for stable goal/formulation/constraints;
4. `findings.md` for accepted evidence and bounded claims;
5. `progress.md` for recent status;
6. current `brain_handoff.md` during rollover only.

Fix only a concrete conflict that blocks the next decision. Then resume the autonomous outer loop immediately.
