<!-- Extracted from prompt (1).md; block `cost`. Do not edit here; edit the source spec. -->

## Cost, credentials and runtime token controls

### Minimal external credentials

Use AWS SDK default credential resolution locally through a profile/SSO, and IAM roles in deployed services. Do not put AWS credentials or Bedrock calls in frontend code. V1 requires no Anthropic direct API key, no OpenAI key, no commercial speech API key, no Pinecone key, and no mandatory Google/Microsoft calendar key.

Authentication and web-push signing have configuration material. These are not additional paid AI provider keys. The infrastructure agent generates/configures them securely where required and documents their purpose. A push adapter can use standard VAPID signing; no separate paid push SaaS is required for the PWA prototype.

Local demonstration mode must run with fixture inference/transcription and a local deterministic store without any cloud account. Production mode must use real adapters and explicitly fail if a required capability is unavailable. A mock response must never masquerade as completed production transcription or delivery.

### Billing model

Do not hard-code historical prices as eternal facts or repeat unsupported claims that serverless means free. At implementation/deployment time, verify the selected AWS region, service generation, instance/task configuration, model ID, inference mode and free-tier conditions.

The monthly estimate must include:

`ASR task CPU + ASR task memory + image/model download/startup + language inference input/output + embeddings + vector upload/storage/query + task DB/storage/requests + source object storage/requests + queues/scheduler + API/frontend hosting + notifications + logs/metrics + network + optional agent hosting`.

Use a workload sheet with actual assumptions: recordings per day, recording lengths, task startup and processing time, model prompt/output lengths, plan requests, memory updates, retained audio days, vector count, query frequency and retry rate. No single-user label implies low cost by itself.

The previous commercial transcription estimate does not apply to the owned ASR implementation. Self-hosted ASR avoids a speech-provider per-minute fee but incurs cloud compute and startup charges. Benchmark CPU throughput and cold starts before claiming it is cheaper for the actual usage pattern.

Current AWS documentation distinguishes Bedrock Managed Knowledge Base from self-managed Knowledge Bases, and OpenSearch Serverless NextGen from Classic. Aurora Serverless can support auto-pause to zero on suitable configurations. Do not deploy an idle-cost service because of a stale example, or assume every service configuration scales to zero. S3 Vectors is the baseline candidate for this project, subject to actual feature/latency needs.

### Initial runtime limits

These are conservative proposed defaults and must be configurable, documented and measured:

| Operation | Initial limit / policy |
| --- | --- |
| Audio upload | 5 minutes, 25 MiB maximum; typical 10–120 seconds |
| Extraction | 1 model call, bounded source/context, up to 1 schema-repair retry |
| Ambiguous extraction escalation | At most 1 stronger-model call if enabled and budget reserved |
| Candidate evidence | Up to 20 candidates, at most 6 admitted items per selected task |
| Daily plan | At most 8 candidate tasks for expensive contextual preparation; obligations outside that set remain visible in the constraint/overflow input |
| Model input | Start with 4,000 tokens for extraction and 8,000 for planning; count actual model/provider tokens and handle oversize inputs deliberately |
| Model output | Start with 1,200 tokens for extraction and 2,000 for plans; cap output and handle truncation |
| Runtime tool loop | At most 4 iterations per planning request; deterministic worker path has no open-ended loop |
| Ordinary reminder | No LLM call needed for a templated notification |
| Semantic indexing | Only changed units; bounded asynchronous batch size |
| Retry | Bounded by error class, per-job attempt limit and DLQ; no unbounded retries |

Long notes may contain more obligations than a single output can represent. Never silently truncate them to satisfy token caps. Persist the original, split into semantically coherent source ranges with a bounded maximum batch count, deduplicate extractions, and visibly defer excess processing when a configured budget would be exceeded. Record per-range completion so retries do not restart all completed ranges.

Budgets must be enforced before requests, not only displayed afterwards. Reserve a conservative upper-bound cost/usage allocation atomically, then settle against actual usage. Concurrent workers must not all pass a stale read of the same remaining budget. Release only allocations known to be unconsumed, such as a request that was never started. A failed/timed-out request may still be billed: retain a conservative charge/reservation when usage is uncertain, and reconcile it through an idempotent path when authoritative usage becomes available. Track infrastructure cost estimates separately from exact token usage; the model-call cap is not an absolute cap on every AWS bill.

At the configured cap, keep manual task edits, source access, typed capture, existing reminders and deterministic fallback planning working. Defer optional embedding/replanning jobs with a visible status. Do not silently switch to an expensive provider or ignore a user cap.

### Reuse and caching

- Save unchanged embeddings keyed by semantic content hash and embedding model/version.
- Reuse a work packet or event briefing when its relevant source/task/preference versions have not changed.
- Reuse tool results with a TTL appropriate to freshness, including date-sensitive travel/event information.
- Use stable prompt prefixes for instructions, schemas and tool definitions where the selected model supports caching. Check model/API eligibility, minimum token length, TTL and cache-write/read prices.
- Measure cache hit rates. An infrequent personal workflow may receive little benefit from short-lived prefix caching.
- Include source/task versions, provider/model/prompt version and ownership scope in generated-result cache keys. Invalidate on material updates.
- Never share user-specific cached responses across users. Do not cache missing permissions as authorized access.

### Runtime measurement

Record per-operation model ID, input/output/cache tokens when available, prompt version, evidence count/token total, tool iterations, estimated cost, latency, retry count and outcome. Do not log raw private sources, audio, access tokens or credentials by default. Metrics must be sufficient to compare simple retrieval against hybrid/reranking and cold ASR against an always-warm alternative.

A small initial budget target can be configured, but every budget report must distinguish measured inference usage, estimated infrastructure cost, free-tier assumptions and unmeasured components. AWS budget alerts can be an additional account-level mechanism; they do not substitute for application-enforced usage limits.
