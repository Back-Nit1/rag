# Prompt: infrastructure and operations readiness

```yaml
task_id: infra-ops-readiness
owner: infrastructure
base_sha: {{BASE_SHA}}
contract_version: 1
allowed_paths: [infra, ops]
read_first:
  - CLAUDE.md
  - docs/spec/architecture.md
  - docs/spec/cost.md
  - docs/spec/delivery.md
  - docs/progress.md
dependencies: [backend-jobs, speech-worker-boundary]
```

Make the declared AWS architecture synthesizable and operationally reviewable.
This prompt authorizes local code and documentation changes only. Do not deploy,
bootstrap an AWS environment, create paid resources or run a cloud smoke test
without a separate explicit user instruction.

Use the repository's Cloudflare skills for nothing here; the architecture is
AWS CDK. Read an installed AWS/CDK skill only if one actually exists and
matches the task.

## Infrastructure

- Add `infra/bin/metis.ts` and instantiate the existing stacks from validated
  configuration.
- Define the outbox, reminder-due and daily-planning Lambda functions/triggers
  with least-privilege roles, bounded concurrency, logs and alarms.
- Implement the selected finite ECS/Step Functions ASR boundary, result
  verification and failure handling, or clearly isolate it behind a separate
  stack if the coordinator has deferred it.
- Add required queues/DLQs, scheduler group/role, notification wiring and
  outputs consumed by API configuration.
- Add web hosting/auth callback configuration only if it matches the approved
  deployment architecture.
- Add budget alarms and observability for missed reminders, stuck outbox rows,
  ASR failures and job age. A budget alarm is not described as a hard cap.
- Fix the CDK scripts for the installed CLI; do not retain ignored/obsolete
  flags merely because typecheck passes.

## Operations

- Local and cloud environment procedures
- Model-artifact pinning/mirroring procedure
- Cost worksheet with assumptions and 30/300/600-recording scenarios
- Deploy, smoke-test, rollback and teardown runbook
- Retention/deletion/export and secret-rotation procedures
- Exact prerequisites and explicit labels for every unrun cloud check

## Validation

```bash
pnpm --filter @metis/infra typecheck
make synth
```

Add focused template assertions if practical. Review synthesized IAM and
resource counts without deploying. Commit only explicit assigned paths. Return
the standard handoff, synthesized stack list, IAM/cost risks and
the exact command that would perform an authorized deployment later.
