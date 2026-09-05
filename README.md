# Autonomous Research Orchestrator

A Codex skill for bounded empirical research: Brain chooses scientific milestones, Luna executes and repairs, and evidence determines whether to continue. [中文说明](README.zh-CN.md) · [Skill](SKILL.md) · [Changelog](CHANGELOG.md)

- Short working documents contain stable constraints plus previous/current/next-stage context.
- `research_history.md` records methods, actual work, outcomes, failures, decisions, evidence links and user/agent contributions for traceability and CV preparation.
- Records are maintained after each work item without a user reminder. History is not loaded in full on normal resume.
- Native Luna and CLI Luna share the scientific contract; normal platform checks and experimental-model boundaries remain.
- Repairs belong to an experiment; worker launches and handoffs are not scientific milestones.
- Execution completion, valid evidence and scientific success are distinct.
- User pauses, ownership and cumulative budgets survive handoff. Low-information continuation triggers review.

## Use

Install this directory under your personal Codex skills folder. Invoke:
```text
Use $autonomous-research-orchestrator to pursue the next decisive milestone,
maintain compact working records and complete research history, and preserve
the session's authorization, budgets and user-stop instructions.
```

Skill maintenance or a request for a CV/history summary does not resume experiments. Existing project results, no-replay identities and protected data are not retroactively changed.

## Implementation and validation

- [Record workflow and packet schema](references/research-records.md)
- [Worker contract](references/brain-luna-contract.md) and [transport](references/luna-cli-runner.md)
- [Runtime/handoff](references/runtime-and-rollover.md)
- [Authorization/stopping](references/authorization-and-stopping.md)
- [Initialization/adoption](references/migration.md)

```powershell
python -m unittest discover -s scripts -p "test_*.py"
python scripts/simulate_autonomous_loop.py
```

Tests use local temporary projects and mocks, not paid APIs or real research episodes. See individual tool `--help` for current CLI arguments. Python's standard library is sufficient for the runtime and record tools.
