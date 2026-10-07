# Prompt: authorized cloud smoke test

Do not run this prompt until the user has explicitly authorized an AWS account,
region, environment name, deployment scope and potentially paid resource
creation. An instruction to write or review code is not deployment approval.

```yaml
task_id: cloud-smoke
owner: infrastructure
base_sha: {{BASE_SHA}}
contract_version: 1
allowed_paths: [ops]
read_first:
  - CLAUDE.md
  - docs/spec/delivery.md
  - ops/deploy-runbook.md
dependencies: [infra-ops-readiness, local-e2e-green, explicit-cloud-approval]
```

Execute the approved deploy/smoke/rollback procedure in the named non-production
environment. Resolve account and region before any mutation, review `cdk diff`,
and stop if the target or expected cost is ambiguous. Do not broaden IAM or
skip approval flags to get a green result.

Verify with real evidence:

- Cognito PKCE login and server-derived ownership;
- private upload and real finite ASR execution/result verification;
- Bedrock extraction and planning under budget accounting;
- DynamoDB authoritative state and S3/vector owner isolation;
- EventBridge schedule creation, confirmed reminder delivery, cancellation and
  stale occurrence suppression;
- export result authorization and deletion/purge progression;
- logs, alarms, DLQ/stuck-job visibility and teardown/retention behavior.

Record resource IDs, timestamps, measured latency/cost inputs and redacted log
correlation IDs—never tokens, source content, recordings or credentials. Mark
every unsupported or unrun check explicitly. A successful deploy is not proof
that ASR, delivery or deletion worked.

After the smoke test, follow the user-approved retain/teardown decision. Do not
destroy retained data or a shared environment without separate confirmation.
Update only the assigned redacted operations report and return exact commands,
results, observed cost/latency, remaining resources and rollback status.
