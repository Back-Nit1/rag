# Retention, deletion, export

All behaviour below is from the code and templates in this repo; deployed behaviour is UNRUN.

## Retention

| Data | Policy | Where |
| --- | --- | --- |
| Raw recordings | S3 lifecycle deletes objects tagged `metis_kind=recording` under `users/` after 30 days | `infra/lib/data-stack.ts` |
| Transcripts, sources, tasks | kept until the user deletes them; recording expiry never deletes text | separate prefix and DynamoDB |
| Incomplete multipart uploads | aborted after 1 day | S3 lifecycle |
| Idempotency/claim rows | DynamoDB TTL attribute `ttl`; cleanup only, never a timer | DynamoDB |
| Logs | 1 month (Lambda, task, state machine, API) | CloudWatch |
| Queue DLQ | 14 days | SQS |
| ASR images | last 10 | ECR |

Known mismatch: the application setting `METIS_SOURCE_RETENTION_DAYS` is not wired to the 30-day lifecycle rule. Changing one does not change the other; both must be edited, or the coordinator should pick one source of truth. The lifecycle rule depends on the upload path tagging objects `metis_kind=recording` - verify in the smoke test (UNRUN), because an untagged recording never expires.

TTL deletion in DynamoDB is eventual (up to ~48 h); it is not used for reminder or budget timing.

## Deletion

1. User deletes a capture/source through the API (`DELETE /v1/captures/{id}`), which increments the revision so late job results cannot resurrect it, and enqueues cleanup via the outbox.
2. (Intended behaviour, not verified against the backend code in this task: confirm the delete handler actually removes objects, vectors and schedules.) The outbox Lambda holds the IAM grants to remove the audio object, derived vectors (S3 Vectors `DeleteVectors`) and schedules; it holds the IAM grants for exactly those.
3. Verify (UNRUN): `aws s3 ls s3://<sources-bucket>/users/<user_id>/` empty for that source; vector query returns nothing; schedule absent in the group.
4. Prod bucket versioning is on, so noncurrent versions persist until a lifecycle expiry exists. **Gap:** there is no noncurrent-version expiry rule, so a "deleted" prod object remains recoverable indefinitely. Add one (e.g. 30 days) before claiming deletion; infra change pending coordinator decision.
5. DynamoDB point-in-time recovery retains deleted items for up to 35 days inside backups.
6. Whole-account erasure: export, then delete user data by prefix and partition, delete the Cognito user, delete schedules by group, then destroy stacks (`runbook.md`).

## Export

- Owner requests `POST` export; the API writes an `export.requested` outbox row; the outbox Lambda builds a deterministic, size- and time-bounded zip under the owner's prefix; the owner downloads via the API (ownership checked server-side).
- Limits (`LIMIT_EXCEEDED`) apply to archive size, record count and run time; a refused export is reported, not truncated silently.
- Delete export archives after download: no lifecycle rule exists for them yet (same gap class as above).
- Smoke check 9 in `runbook.md` is UNRUN.
