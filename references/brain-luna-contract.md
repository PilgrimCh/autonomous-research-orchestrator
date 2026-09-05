# Brain and Luna contract

Brain model: Astra (`gpt-6-astra`). Worker model: `gpt-5.6-luna` with reasoning `max`. Preserve the Brain's user-selected reasoning effort; this role assignment does not change the experimental agent/provider/model.

Brain owns goal, hypothesis, data roles, estimand, comparison, metrics, decision rules, interpretation, authorization, records and routes. Luna implements, collects, executes, computes statistics, plots and repairs within its bounded scope. Brain may do lightweight control/record work directly; it does not replace independent evidence execution because a transport is inconvenient.

## Assignment

Maintain one scientific contract per experiment. Work/attempt IDs sit below its scientific stage. Record `work_kind: value | mechanism | infrastructure | documentation` and stable `scientific_stage_id`. Worker count and Task suffix are not progress metrics.

Include task/stage/route IDs, goal gap/question, decision affected, exact inputs, comparison/data roles/metrics/result consequences, allowed and forbidden actions, isolated write scope, method, result path, targeted check, finite resources, repair authority and stop rule. The runner reference specifies machine fields.

Verify actual Git/worktree availability rather than trusting stale prose. Parallel writing uses independent worktrees; otherwise serialize with distinct output paths. Tell workers they are not alone and must preserve others' edits. Workers do not modify main records, runtime state or goal. Return evidence and a contribution account.

## Data and repairs

Define roles before access:
- `engineering`: replayable fixtures/smoke tasks; inspected freely inside scope, never presented as independent scientific validation.
- `development`: method/threshold development and scouts; exposure recorded, never relabeled untouched.
- `confirmation`: frozen independent evaluation; explicit protection/authorization applies.

These defaults affect new allocations only. Existing no-replay identities, uncertain requests and protected data remain unchanged.

Separate scientific semantics (goal, model, data role, comparison, metric) from implementation (paths, parser, wrappers, arm initialization). Repair implementation within scope. Resume the original experiment or its decision-equivalent test before interpreting scientific evidence.

For HTTP200/schema failures inspect permitted response structure, finish reason, parse location and redacted examples. Repeated broad error names are not a root cause. Define retention in advance, exclude credentials, respect raw-data prohibitions. Reparsing an available local response differs from sending another request. Reconcile uncertain dispatch before retry.

Default three substantive repair cycles. Diagnostic probes belong inside cycles. Each cycle records reproducer, diagnosis, repair, validation, original-execution resumption and resources. A failed repair should improve the next diagnosis. At the bound, Brain can authorize one focused escalation within session limits with a better hypothesis, or park the uneconomic blocker as unanswered. New IDs do not erase attempts/resources.

Return to Brain before changing goal, comparison, experimental model/provider, data, metric, threshold, hard ceiling or authorization. Do not remove the mechanism under test to make execution pass.

## Results and acceptance

New assignments request `result_schema_version: 2`: separate `execution_status`, `evidence_validity`, `scientific_verdict` (runner reference). Retain core results, limitations, deviations, debug, resources, artifact pointers and required Brain decision in one result.

Execution/schema acceptance is not scientific success. `completed / valid / inconclusive` is normal. Platform failures or incomplete execution are `not_assessable`, not scientific negative/inconclusive.

Report what Luna implemented/executed/analyzed, user-supplied design/review if known, exact methods/counts and evidence pointers. Brain publishes history. Do not invent personal attribution or scientific impact from infrastructure success.

Brain verifies identity, scope and decisive artifacts. Reconcile metadata discrepancies explicitly; do not delete/rebuild all code by default. Real permission/resource violations remain recorded and can prevent acceptance; isolate affected evidence. Existing quarantines are not retroactively reversed.

Routine repairs stay inside the experiment; new scientific questions/designs earn new stages. Publish the outcome and next decision before dispatching further work.
