# Brain Handoff

```yaml
brain_handoff:
  session_id: <session-id>
  from_brain_generation: <n>
  from_brain_task_id: <predecessor-task-id>
  to_brain_generation: <n+1>
  to_brain_task_id: <fill-after-successor-creation>
  brain_model: gpt-6-astra
  predecessor_transcript: retained-unarchived-in-codex
  rollover_reason: <effective_compaction_threshold-or-other-observable-degradation>
  context_compactions_observed_in_source_brain: <count>
  effective_rollover_threshold_and_scope: <threshold and applicable generations>
  project_goal: <goal>
  current_goal_status: unresolved | complete | infeasible
  current_high_level_formulation: <compact formulation>
  accepted_findings_that_matter_now: []
  current_active_route: <route-id-or-none>
  latest_stage_result: <result path and one-line meaning>
  why_current_direction_was_selected: <brief>
  unresolved_questions: []
  next_planned_action: <one action>
  control_mode: RUNNING | PAUSED_USER | PAUSED_PLATFORM | PAUSED_REVIEW | TERMINAL
  user_pause_latch: <true-or-false>
  saved_next_action: <preserved action; not permission to run>
  running_luna_tasks: []
  current_authorization: <compact effective allowed/forbidden summary with source and scope>
  global_session_budget_remaining: {}
  active_budget_reservations: <compact pointer>
  research_history_pointer: research_history.md
  important_invalidated_or_superseded_work: []
  immediate_resume_instruction: <assert generation, verify lineage, read snapshot and minimal views; preserve pause; dispatch only if control mode and authority permit>
```

Overwrite this file at the next rollover. Do not append a handoff history; full historical transcripts remain in the Codex tasks named by `brain_task_lineage`.
