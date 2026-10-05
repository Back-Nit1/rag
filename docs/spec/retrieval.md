<!-- Extracted from prompt (1).md; block `retrieval`. Do not edit here; edit the source spec. -->

## Memory and retrieval design

### Separation of responsibilities

The authoritative task/event database answers exact questions about deadlines, completion, cancellation, ownership and current versions. Semantic memory supplies supporting context: why a task exists, what was tried, what the user intended, and which prior thoughts relate to it. Retrieved memory must not override newer authoritative state.

Expose separate operations for `getCurrentTaskState`, `listEligibleObligations`, `getProjectHistory`, `lookupIdentifier`, and `retrieveSupportingMemories`. Keep retrieval providers behind a stable interface so a vector-store change does not rewrite planning logic.

### Ingestion

1. Persist the source and its version.
2. Extract typed records with source IDs and source spans.
3. Create short memory units by meaning: observation, attempt, decision, blocker, idea, intention or preference. Do not embed every JSON property as if it were prose.
4. Include enough surrounding source context to preserve negation, conditionals and the distinction between an idea and a decision.
5. Compute embeddings only for new or materially changed semantic text. Store embedding-model ID/version, dimensions, content hash and source version.
6. Index with server-enforced `user_id`, optional `project_id`, memory type, source/version and current/historical status metadata.
7. Treat indexing as asynchronous. The latest task record must remain usable before its vector update completes.

Keep raw source text available for citations. A memory summary cannot become the only surviving evidence. If chunking loses "do not" or "we abandoned this", repair the chunking design before changing search algorithms.

### Baseline retrieval

Use structured lookup when fields are known. Use project/time filters to scope history. Use dense semantic retrieval for paraphrased thought recall. Use explicit identifier fields and aliases for project names, filenames, error codes and task IDs; ordinary full-text tokenization may not preserve every literal identifier.

For the initial cloud semantic store, use direct S3 Vectors behind `MemoryStore`, with a Bedrock embedding model supported in the selected region. Direct indexing provides control over source updates. Bedrock Knowledge Bases with a chosen vector store is an alternative, not a second mandatory RAG engine to pay for alongside direct retrieval.

Self-managed Knowledge Bases backed by S3 Vectors support semantic retrieval; do not assume their hybrid-search or metadata capabilities equal every other store. AWS capabilities change: verify current official limits and APIs before coding. Represent a provider's restrictions in an adapter capability map rather than in vague comments.

Default candidate limits are project-tunable configuration, not claims of optimality: retrieve up to 20 candidate memory units, validate ownership/source versions/currentness, deduplicate related spans, and supply at most 6 evidence items per task. Enforce a total evidence token budget across the plan; avoid multiplying the per-task maximum by dozens of tasks.

### Query planning

"Plan my day" should not become one unscoped embedding search. Read availability and eligible obligations first. Select a bounded number of candidate focus tasks through deadline/dependency/explicit-priority rules. Retrieve evidence for tasks with missing resumption context. Reuse an already-current work packet where possible.

When the task is "resume Cerno", retrieve that project's current state and relevant chronological attempt history before searching broadly. If a new thought refers to "that caching approach" without a project name, use semantic search with the user's scope, then ask a targeted clarification if several projects fit.

Retrieval scores are ranking signals, not calibrated probabilities, factual certainty, urgency or user priority. Recency alone also does not establish validity: an older uncompleted deadline can be more important than a new casual idea.

### Evidence and conflict handling

- Every evidence item includes `source_id`, `source_version`, `span_ref`, `memory_id`, and a resolvable source link.
- Hydrate candidate IDs against authoritative source/task state. Drop deleted, inaccessible, superseded-as-current or mismatched-version candidates.
- Historical failed attempts can be retrieved as historical evidence, labeled accordingly; do not filter away all history needed to explain a blocker.
- Preserve a chain of superseding decisions. The current decision is explicit; past alternatives remain inspectable.
- If records conflict without a clear update relation, flag the conflict and ask a compact clarification instead of silently choosing.
- If evidence is insufficient, return a bounded answer with missing information. Do not add invented project details to make a plan look complete.
- Reject cross-user memory before constructing any model prompt, even if the store unexpectedly returns it.

### Retrieval experiments

Hybrid retrieval is a feature flag backed by an experiment. Compare filtered dense retrieval, lexical retrieval and hybrid fusion on the same labeled corpus. Compare a reranker only after candidate recall has been examined. A reranker cannot recover an item absent from its candidate list.

Start with 40 synthetic or consented real queries covering paraphrase, exact identifier, project/time lookup, obsolete decisions, ambiguous references, missing memory, and changed commitments. Split development questions from held-out evaluation questions. Fix generator settings and final evidence budgets for comparison; also report retrieval/reranking cost and latency so added computation is visible.

Report Recall@k, useful top-result ranking, stale-evidence admission, source-link validity, cross-user leakage, and downstream plan defects. Adopt hybrid or reranking only when repeatable improvements justify measured overhead. Do not copy a universal dense/sparse weight or cosine threshold from an unrelated benchmark.

### Preference feedback

Capture accepted, edited, deferred and rejected recommendations with reason where supplied. Use explicit rules first, such as the user preferring one morning focus block. A later trained ranking model is optional. Store feedback separately from raw private notes and provide deletion/export for both.
