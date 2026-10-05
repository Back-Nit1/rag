<!-- Extracted from prompt (1).md; block `sources`. Do not edit here; edit the source spec. -->

## Primary references and capability verification

The architecture and project policies above are proposed Metis design decisions. The references below ground provider/tool capabilities, not a universal best architecture or a fixed future price. Relevant documentation was checked during preparation on 2026-10-05. Recheck a source when its capability materially affects the chosen implementation; avoid re-browsing every link on every agent turn.

### AWS inference, retrieval and infrastructure

- [Bedrock Agents Classic maintenance status](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-classic-maintenance-mode.html): new-project planning should account for the closure to new customers and optional AgentCore path.
- [Bedrock structured outputs](https://docs.aws.amazon.com/bedrock/latest/userguide/structured-output.html): model/API support and supported schema subset need explicit checks.
- [Titan text embeddings](https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html): embedding model IDs, dimensions and limits.
- [S3 Vectors and Bedrock Knowledge Bases](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-vectors-bedrock-kb.html): integration capabilities and limits; direct vector retrieval and Knowledge Bases are alternative implementation paths.
- [S3 Vectors metadata filtering](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-vectors-metadata-filtering.html): ownership and project metadata filters, limits, and supported operations.
- [Bedrock pricing](https://aws.amazon.com/bedrock/pricing/): distinguish managed and self-managed Knowledge Bases, model inference and optional features.
- [S3 pricing](https://aws.amazon.com/s3/pricing/): ordinary object storage and vector storage/query/upload have separate billing dimensions.
- [Lambda pricing](https://aws.amazon.com/lambda/pricing/): requests, execution duration and free-tier limits do not imply every personal app is free.
- [Lambda quotas](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html): verify ordinary Lambda and any separately selected hosting modes independently.
- [EventBridge schedule types](https://docs.aws.amazon.com/scheduler/latest/UserGuide/schedule-types.html): one-time/recurring scheduling, timezones and invocation precision.
- [ECS/Fargate tasks through Step Functions](https://docs.aws.amazon.com/step-functions/latest/dg/connect-ecs.html): durable task completion; successful launch is not completed inference.
- [Fargate pricing](https://aws.amazon.com/fargate/pricing/) and [FAQ](https://aws.amazon.com/fargate/faqs/): CPU/memory runtime, startup billing, minimums and GPU availability.
- [OpenSearch pricing](https://aws.amazon.com/opensearch-service/pricing/): distinguish NextGen and Classic collection compute policies before considering an alternative store.
- [Aurora auto-pause](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-serverless-v2-auto-pause.html): supported configurations may pause compute; remaining charges and constraints still matter.

### Speech and browser input

- [Whisper model and implementation](https://github.com/openai/whisper): pretrained neural ASR, supported models and license.
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper): CTranslate2 implementation, quantization and decoding parameters; published machine-specific benchmarks are not Metis performance guarantees.
- [whisper.cpp](https://github.com/ggml-org/whisper.cpp): optional alternative only after a measured architectural decision.
- [MediaRecorder](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder): recording and platform-dependent media output behavior.

### Claude Code and optional development tools

- [Claude Code subagents](https://code.claude.com/docs/en/sub-agents): role contexts, project agent definitions and supported delegation capabilities.
- [Claude Code agent teams](https://code.claude.com/docs/en/agent-teams): verify experimental availability, independent contexts and limitations.
- [Claude Code worktrees](https://code.claude.com/docs/en/worktrees): verify base selection and installed-version behavior; explicit Git worktrees remain a fallback.
- [Claude Code skills](https://code.claude.com/docs/en/skills): role-specific procedure loading and supporting references.
- [Claude Code cost management](https://code.claude.com/docs/en/costs): focused tasks, scoped context and aggregate multi-agent usage.
- [jCodeMunch](https://github.com/jgravelle/jcodemunch-mcp): optional symbol retrieval; check current license and installed tool names. Do not repeat marketing savings percentages as a guaranteed Metis result.
- [Anthropic frontend-design skill](https://github.com/anthropics/skills/blob/main/skills/frontend-design/SKILL.md): optional design reference if installed/selected. It does not resolve the unidentified "Ponytail" skill name.

## Start now

Begin with repository inspection and the contracts-first bootstrap. Maintain the implementation ledger, three-minute safe checkpoint cadence and bounded agent assignments. Build and validate the complete Metis workflow through the phase gates. When blocked, finish independent authorized work and report the specific blocker. Keep actual implementation and measured evidence aligned with this specification; update an architecture decision when a justified change is required.
