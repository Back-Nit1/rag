# Deploy, smoke test, rollback, teardown

Nothing in this runbook has been executed. Every command that touches AWS is **UNRUN** and requires an explicit user instruction naming the account and region. `make synth` (no AWS calls) is the only step proven here.

## Stacks (synthesized, `metis-<env>-` prefix)

network, data, auth, speech, jobs, api, observability. Dependency order is resolved by CDK: network + data + auth -> speech -> jobs -> api; observability needs jobs + speech.

## 0. Pre-flight (RAN locally)

```bash
pnpm install --frozen-lockfile
pnpm --filter @metis/infra typecheck
pnpm --filter @metis/infra test      # template assertions
make synth                            # stub bundling, no Docker, no AWS
```

Pre-flight against AWS (UNRUN): `aws sts get-caller-identity` (confirm account), Bedrock model access, Lambda account limits, S3 Vectors availability (see `environments.md`).

## 1. Deploy (UNRUN, needs approval)

Authorized deploy command, run from `infra/` with a named profile, only after the user approves the account/region and cost:

```bash
cd infra && AWS_PROFILE=<profile> CDK_DEFAULT_ACCOUNT=<account> CDK_DEFAULT_REGION=<region> \
  npx cdk deploy --all --require-approval broadening --outputs-file cdk.outputs.json \
  -c metis:env=dev -c metis:alertEmail=<you@example> -c metis:webOrigins=<https-origin>
```

(First time only, separately approved: `npx cdk bootstrap aws://<account>/<region>`.) Never pass `-c metis:bundling=stub` to deploy; the stub is a placeholder that raises on invoke.

Order of operations for a fresh environment:

1. Deploy (speech image digest placeholder; ASR will fail visibly until step 3).
2. Create the VAPID parameter (`secrets.md`).
3. Push the ASR image (`model-artifacts.md`), then redeploy with `-c metis:speechImageDigest=sha256:<digest>`.
4. Confirm the alert email subscription (SNS sends a confirmation link; unconfirmed = no alerts).
5. Create the single Cognito user (self sign-up is off): `aws cognito-idp admin-create-user ...` - UNRUN.

## 2. Smoke test (all UNRUN)

Each check records pass/fail and a timestamp in `docs/progress.md`; a skipped check stays labelled UNRUN, not "passed".

| # | Check | Expected |
| --- | --- | --- |
| 1 | `GET <ApiUrl>/v1/health` unauthenticated | 200, mode `cloud` |
| 2 | Request without token to `/v1/sources` | 401 from the API Gateway authorizer |
| 3 | Sign in; create a typed capture | source created, extraction proposal appears (one real Bedrock call; check usage record) |
| 4 | Upload a 10 s real recording; complete | states queued -> starting -> transcribing -> ready; Step Functions execution named by job id; transcript is real text, not a fixture |
| 5 | Re-complete the same capture | no second execution (name collision) |
| 6 | Create a reminder 3 minutes ahead | state `pending_schedule` then `scheduled` only after the schedule exists in group `metis-<env>-reminders`; fires once; delivery recorded |
| 7 | Set daily planning preference | schedule created targeting the daily-plan Lambda; invoke once manually with `{"user_id","scheduled_time"}`; `plan.requested` enqueued |
| 8 | Break it on purpose: upload an unsupported file | job `failed` with actionable error; ASR alarms stay quiet (not a platform fault) |
| 9 | Request an export | `export.requested` processed by the outbox Lambda; download works for owner only |
| 10 | Another user's token on the first user's ids | 404/403, never data |
| 11 | Alarm path: send a test message to the SNS topic | email received |
| 12 | Delete the capture | audio object and derived records gone per `data-lifecycle.md` |

## 3. Observability

Alarms (observability stack), all to the alerts topic:

| Alarm | Fires when | Triage |
| --- | --- | --- |
| IngestDlqNotEmpty | wake-ups exhausted 5 receives | find outbox rows with state `dead` |
| IngestQueueAge | oldest message > 15 min | check OutboxErrors/Throttled, reserved concurrency |
| OutboxNotRunning | no outbox invocation in 15 min (timer + SQS) | EventBridge rule `outbox-timer`, event source mapping state |
| OutboxErrors / OutboxThrottled | unhandled errors / throttling | CloudWatch log group `/aws/lambda/metis-<env>-outbox` |
| ReminderTargetErrors / ReminderDropped / ReminderFn* | Scheduler could not deliver, or target failed | missed-reminder incident: list schedules in the group, re-sync affected reminders |
| DailyPlanErrors | daily-plan target failed | log group `.../dailyplanning` |
| AsrExecutionsFailed / TimedOut / Aborted / AsrFinalizeErrors | ASR boundary failures | execution history, task log group `/metis/<env>/speech-task` |
| Budget alarm | actual 50/80/100% or forecast 100% of `monthlyBudgetUsd` | alert only; nothing is stopped |

Known gaps, stated rather than hidden:

- **Stuck outbox rows** are detected by proxy (queue age, dead-letter, dispatcher not running). A row stuck in `pending` with a lost wake-up is drained by the 5-minute timer but has no per-row age metric. Request: the dispatcher should emit a custom metric (oldest ready row age); not added because backend code is out of scope here.
- **Late reminders** (delivered but late) have no metric; only dropped/errored invocations alarm. Same request: emit delivery lag at `reminder_due`.
- The ASR concurrency cap is the launcher's count-then-start check plus outbox reserved concurrency (default 2). Worst case is two concurrent tasks, not an unbounded number. Step Functions has no native semaphore.
- Alarm thresholds have never been exercised.

## 4. Rollback

- **Code/infra**: redeploy the previous git SHA with the same context (`cdk deploy --all`). CloudFormation rolls back a failed update automatically.
- **ASR image**: redeploy with the previous `metis:speechImageDigest`.
- **Bad outbox behaviour**: set `-c metis:outboxConcurrency=...` is not a stop switch; to pause dispatch, disable the SQS event source mapping and the `outbox-timer` rule (console or `aws events disable-rule`) - UNRUN. Rows stay in the table; re-enable to resume. Handlers are idempotent.
- **Data**: DynamoDB point-in-time recovery is on; restore creates a NEW table (`aws dynamodb restore-table-to-point-in-time`), then repoint `METIS_TABLE_NAME` - UNRUN, never rehearsed.
- Schedules created by the API live outside CloudFormation (group contents); a rollback does not remove them.

## 5. Teardown (UNRUN, destructive, needs explicit approval)

```bash
cd infra && npx cdk destroy --all   # asks for confirmation; add --force only when instructed
```

- dev/staging: table, bucket (auto-delete), key and ECR repo are DESTROY; the KMS key enters a 7-30 day deletion window, so data is unreadable immediately.
- prod: table has deletion protection, and the table, bucket, key, ECR repo and Cognito pool are RETAIN. Teardown leaves them; deleting them is a separate manual, approved step after an export.
- Not removed by CloudFormation: schedules inside the group (delete them first or the group delete fails), the SSM VAPID parameter, the `CDKToolkit` bootstrap stack, S3 Vectors data if the type is retained, CloudWatch log groups created outside stacks.
- Before any teardown: export the owner's data (`data-lifecycle.md`) and confirm the budget alarm and endpoints are gone (`aws ec2 describe-vpc-endpoints`) since endpoints bill hourly.
