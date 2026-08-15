# Brain and Luna Contract

## Responsibility boundary

Use scientific policy versus execution as the boundary, never small versus large.

Brain alone owns the project goal, accepted evidence, hypotheses, route portfolio, stage design, comparison, estimand, sample definition, primary metrics, decision rules, claim boundary, authorization interpretation, global records, Goal Mode, project stopping, and Brain rollover.

Delegate all evidence-producing execution to the logical `luna_worker` role through `scripts/run_luna_worker.py`: implementation, code changes, API calls, collection, cleaning, transforms, fitting, rollouts, simulation, statistics, bootstrap, sensitivity work, plots, metric calculation, runtime debugging, environment repair, and tests whose outputs affect research evidence.

## Luna assignment

Give each Luna one compact contract and persist it as the task-local JSON consumed by the CLI runner. The shape below is conceptual; `references/luna-cli-runner.md` defines the exact required JSON fields.

```yaml
assignment:
  task_id: <stable-id>
  stage_id: <stage-id>
  route_id: <route-id>
  route_depth: scout | focus | confirm
  project_goal_link: <how this moves the final goal>
  question: <bounded question>
  decision_affected: <decision>
  inputs: [<exact paths, datasets, versions, target IDs>]
  scientific_contract:
    estimand_or_target: <fixed target>
    main_comparison: <fixed contrast>
    sample_definition: <fixed scope>
    primary_metrics: [<metrics>]
    success_negative_inconclusive: <rules>
  allowed_actions: [<execution actions>]
  forbidden_actions: [<data, access, scope, destructive actions>]
  write_scope: [<isolated paths>]
  method: <frozen procedure with reasonable local implementation freedom>
  result_path: <one result.json path>
  targeted_sanity_check: <decision-critical check>
  resource_ceiling: <finite time, calls, cost, storage, compute>
  repair_authority:
    allowed: [<execution-preserving repairs>]
    forbidden: [<scientific or authorization changes>]
    debug_cycle_limit: 3
    required_sequence: [reproduce, minimize, diagnose, repair, targeted_validate, resume_original_experiment]
  stopping_rule: <completion/boundary rule>
```

Use experiment IDs, paths, dataset names/versions, row or sample counts, target IDs, configs, seeds, timestamps, provider/model strings, and execution metadata for identity and reproducibility. Do not create cryptographic identities unless the user explicitly requests them.

## Luna execution autonomy

Require Luna to solve routine execution problems rather than merely report them. Permit path fixes, dependency/environment repair, parsing/serialization repair, Windows file-lock recovery, transient retry, checkpoint resume, invalid intermediate regeneration, deterministic implementation fixes, and targeted reruns when scientific semantics remain frozen.

An execution blocker is not scientific evidence. It does not support a negative result, route pruning, or a pivot. When the original idea remains decision-relevant, require a hypothesis-preserving debug loop under the same frozen scientific contract:

```text
reproduce -> minimize -> identify root cause -> repair -> targeted validation
          -> resume the original experiment -> obtain an interpretable result
```

Default to as many as three bounded debug cycles inside the task ceiling. A failed first repair is a new diagnostic observation, not permission to skip the task. Prefer a minimal reproducer, a known-good baseline, dependency/environment isolation, checkpoint recovery, and then a clean semantics-equivalent reimplementation of the failing component. Never "fix" the task by removing the component, comparison, data, metric, or mechanism needed to test the idea.

After each repair, run the smallest check that can falsify the proposed root cause. A repair-only unit test is not enough: Luna must resume the original experiment or its decision-equivalent minimal test before returning a scientific status. If the blocker remains after the bounded cycles, return `blocked` or `invalid`, preserve the reproducer/logs/attempts, state the narrow unresolved root cause, and propose the cheapest next unblock action. Never translate an unresolved execution blocker into `negative` or `inconclusive` scientific evidence.

When a blocked task tests a central or high-decision-value idea and global budget remains, Brain normally dispatches one focused follow-up debug assignment or a clean known-good reimplementation before considering a bypass. Brain may park the task only after recording why further debugging has lower decision value than the alternative, what was actually tried, and how to resume it. The idea remains unanswered, not refuted.

Luna must return to Brain before changing the goal, research question, estimand, main comparison, provider/model/data boundary, sample definition, primary metric, success criterion, hard resource ceiling, or authorization scope.

If the CLI runner cannot discover a compatible executable, verify the Luna catalog entry, or confirm runtime identity as `gpt-5.6-luna`/`max`, do not silently use another role/model and do not move evidence work into Brain. A task or process name containing `luna` is not identity proof. Record `WORKER_PLATFORM_BLOCKED`, keep the goal unresolved, and report the exact error so the same session can resume when worker capability returns.

## Anomaly handling

- `execution_anomaly`: Luna repairs within the contract, runs the targeted check, and resumes.
- `scientific_or_design_failure`: Luna stops the affected task and returns evidence; Brain replans.
- `authorization_or_resource_boundary`: Luna stops before the boundary; Brain searches for another authorized route before asking the user.
- `integrity_issue`: Luna isolates clearly invalid evidence, attempts a semantics-preserving repair, and returns unresolved integrity questions to Brain.

Do not accept silent sample exclusions, provider substitutions, changed prompts, new paid calls, or metric changes as repairs.

## Result contract

Default to one concise `result.json`:

```json
{
  "experiment_id": "<id>",
  "status": "success | negative | mixed | inconclusive | invalid | blocked",
  "executed": {},
  "primary_results": {},
  "sanity_check": {"status": "pass | qualified | fail", "details": "<brief>"},
  "deviations": [],
  "repairs": [],
  "debug": {
    "encountered": false,
    "failure_class": null,
    "attempts": [],
    "root_cause": null,
    "resolved": null,
    "original_experiment_resumed": true
  },
  "resource_use": {},
  "artifacts": [],
  "claim_boundary": "<narrow execution-side statement>",
  "limitations": [],
  "brain_decision_needed": "<specific interpretation or replan question>"
}
```

When `debug.encountered` is true, record each attempt with its reproduction, diagnosis, repair, and targeted-validation outcome. A `success | negative | mixed | inconclusive` status is valid only when `debug.resolved` and `debug.original_experiment_resumed` are both true.

Brain first requires an accepted CLI `run.json`, then inspects the decision-critical fields/artifacts, decides the scientific meaning, updates shared records, and immediately chooses the next stage. Luna never writes `task_plan.md`, `findings.md`, `progress.md`, `pipeline_state.json`, or `brain_handoff.md`.
