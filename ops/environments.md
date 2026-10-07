# Environments

Status key: **RAN** = executed in this repo without AWS; **UNRUN** = needs an AWS account, credentials and explicit user approval. Nothing in `ops/` has been run against a cloud account.

## Local (no cloud account needed) - RAN

```bash
make bootstrap        # python + node deps
cp .env.example .env  # METIS_MODE=local, fixture identity, SQLite, fixture transcriber and inference
make dev              # API + web + local worker loop
make test && make check
```

- `METIS_MODE=local` uses SQLite (`.metis/metis.sqlite`), local object dir, the fixture transcriber and the fixture inference client. Fixture output is labelled as fixture and is never production output.
- `METIS_ALLOW_FIXTURE_IDENTITY=true` is local-only. The cloud API function hard-sets it to `false`.
- Never put AWS keys in `.env` for the web app; the browser holds no credentials.

## Cloud (dev / staging / prod) - UNRUN

Prerequisites (all UNRUN until checked):

1. AWS account, one region (default `us-east-1`, `metis:defaultRegion`). Bedrock access granted for the three model ids in `infra/cdk.json` in that region (console, "Model access") - UNRUN.
2. S3 Vectors available in the region (`aws s3vectors help` / console) - UNRUN. The data stack uses `AWS::S3Vectors::*` CloudFormation types; confirm they exist in the region before deploy.
3. Lambda account concurrency limit. Defaults reserve outbox 2 + reminder 3 + daily-plan 1 = 6 and AWS requires 10 unreserved, so the account limit must be at least 16. New accounts are sometimes capped at 10. Check `aws lambda get-account-settings` - UNRUN. If the limit is 10, deploy with `-c metis:outboxConcurrency=0 -c metis:reminderConcurrency=0 -c metis:dailyPlanConcurrency=0` and file a quota increase.
4. Docker running (Lambda bundling uses `public.ecr.aws/sam/build-python3.12:latest-arm64`; the speech image is `linux/amd64`).
5. Node 22+, pnpm, `pnpm install` done. CDK CLI is pinned in `infra/package.json` (installed: 2.1144.x; library 2.272.x).
6. CDK bootstrap in the target account/region - UNRUN, requires approval: `pnpm --filter @metis/infra cdk bootstrap aws://<ACCOUNT>/<REGION>`.
7. VAPID private key stored by hand (see `secrets.md`) - UNRUN.

### Configuration

All config is CDK context (`infra/cdk.json` or `-c metis:key=value`), validated in `infra/lib/config.ts` before any template is produced. Prod additionally requires: `speechImageDigest`, `speechModelSha256`, a pinned (non-`main`) `speechModelRevision`, `alertEmail`, https `webOrigins`, and non-stub bundling.

| Context key | Meaning |
| --- | --- |
| `metis:env` | `dev` (default) / `staging` / `prod`; prefixes every name |
| `metis:monthlyBudgetUsd` | Budget ALARM threshold (not a cap) |
| `metis:webOrigins` | comma list; CORS + Cognito callback/logout URLs |
| `metis:speechImageDigest` | `sha256:<64 hex>` of the pushed ASR image |
| `metis:vapidPrivateKeyParam` / `vapidPublicKey` | SSM SecureString name; public key string |
| `metis:{outbox,reminder,dailyPlan}Concurrency` | reserved concurrency; 0 = unreserved |
| `metis:bundling` | `docker` (default). `stub` is used only by `make synth` and produces a non-deployable placeholder |

### Stack outputs consumed by API configuration

| Env var | Source |
| --- | --- |
| `METIS_REMINDER_TARGET_ARN` | jobs stack output `ReminderTargetArn` |
| `METIS_DAILY_PLAN_TARGET_ARN` | jobs stack output `DailyPlanTargetArn` (separate Lambda from the reminder target) |
| `METIS_SCHEDULER_GROUP`, `METIS_SCHEDULER_ROLE_ARN` | jobs stack |
| `METIS_INGEST_QUEUE_URL` | jobs stack |
| `METIS_SPEECH_STATE_MACHINE_ARN` | speech stack |
| `METIS_TABLE_NAME`, `METIS_SOURCES_BUCKET`, `METIS_VECTOR_*` | data stack |
| `METIS_COGNITO_*` | auth stack |

These are wired into the Lambda environments by the stacks themselves; the outputs exist for operators and the web client.
