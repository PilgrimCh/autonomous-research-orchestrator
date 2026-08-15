# Authorization, Budgets, and Stopping

## Session-level standing authorization

Store authority and budgets under `autonomy_session` in `pipeline_state.json`. Treat them as cumulative across all stages, routes, workers, Goal Mode entries, and Brain generations.

Safe defaults after explicit skill invocation may authorize finite local code, local compute, development data, ephemeral Luna CLI workers, and Brain rollover. Default external calls and external cost to zero. Never infer permission for downloads/uploads, authenticated services, new providers/datasets, untouched confirmation, destructive actions, or goal expansion.

Each stage and Luna task receives a smaller local ceiling. A local ceiling ending closes or replans that work; it does not replenish or terminate the global session.

Before dispatch, confirm:

```text
projected task use <= global budget remaining
action is allowed and not forbidden
task does not cross a user checkpoint
```

Record actual cumulative use after each Luna result. Rollover copies nothing into a new budget; it keeps the same state file and counters.

## Continue automatically

Do not ask the user to choose an estimator, threshold, feature, route, local pivot, diagnostic, or experiment sequence. Brain makes scientific choices.

If one proposed action is blocked, search the current portfolio and Goal Mode for another authorized decision-relevant route. Continue when one exists.

## Ask the user only for

- new money or use beyond the standing cumulative cost/call ceiling;
- a new provider, service, external dataset, download, upload, or authenticated access;
- untouched test/confirmation evidence;
- destructive or irreversible action;
- material expansion/change of the final research goal;
- a decision that genuinely requires the user's subjective preference.

Ask for the exact missing authority. Do not reopen already granted session permissions at every stage or rollover.

## Distinguish transitions from terminal states

These are internal transitions, not session stops:

- `stage_complete`: interpret, replan, and continue;
- `route_stop`: prune/park locally, return to the goal, and continue;
- `goal_replan`: enter Goal Mode and rebuild the portfolio;
- `brain_rollover`: transfer the same session to one successor Brain;
- `authorization_block` or `resource_block` affecting only one route: try another legal route.
- `WORKER_PLATFORM_BLOCKED` or `ROLLOVER_PLATFORM_BLOCKED`: preserve the unresolved session and wait for the missing worker or rollover capability; never convert it into a scientific failure.

Allow only these terminal outcomes:

1. `PROJECT_COMPLETE`: accepted evidence is sufficient for the final goal or deliverable.
2. `TRUE_AUTHORIZATION_BLOCK`: every worthwhile next route needs missing consequential authority.
3. `GLOBAL_RESOURCE_EXHAUSTED`: the session-wide ceiling cannot fund any worthwhile next route.
4. `PROJECT_INFEASIBLE`: after Goal Mode, no reasonable route exists under current resources/conditions.
5. `UNRECOVERABLE_INTEGRITY_OR_SAFETY_FAILURE`: invalid evidence is isolated and no safe legal alternative remains.

Project infeasibility is a high-threshold scientific judgment. Before setting it, Goal Mode must reconsider the formulation, measurement, representation, architecture, data, and direct paths to the same final goal.

Do not keep running merely to consume remaining time. Stop when no decision-relevant work remains, but only after Goal Mode verifies that conclusion.
