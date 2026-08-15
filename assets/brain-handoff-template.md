# Brain Handoff

```yaml
brain_handoff:
  session_id: <session-id>
  from_brain_generation: <n>
  from_brain_task_id: <predecessor-task-id>
  to_brain_generation: <n+1>
  to_brain_task_id: <fill-after-successor-creation>
  predecessor_transcript: retained-unarchived-in-codex
  rollover_reason: <second_context_compaction-or-other-observable-degradation>
  context_compactions_observed_in_source_brain: <count>
  project_goal: <goal>
  current_goal_status: unresolved | complete | infeasible
  current_high_level_formulation: <compact formulation>
  accepted_findings_that_matter_now: []
  current_active_route: <route-id-or-none>
  latest_stage_result: <result path and one-line meaning>
  why_current_direction_was_selected: <brief>
  unresolved_questions: []
  next_planned_action: <one action>
  running_luna_tasks: []
  current_authorization: <compact allowed/forbidden summary>
  global_session_budget_remaining: {}
  important_invalidated_or_superseded_work: []
  immediate_resume_instruction: <assert generation, verify lineage, read minimal state, continue>
```

Overwrite this file at the next rollover. Do not append a handoff history; full historical transcripts remain in the Codex tasks named by `brain_task_lineage`.
