/**
 * Memory search. Every result carries its evidence, which opens the cited
 * source and span. A degraded index and an empty result are stated as what
 * they are: no relevant memory is not an answer.
 */
import { useState } from "react";
import type { SearchResponse } from "../contracts/metis";
import { Button } from "../components/Button";
import { Chip, Empty, Loading, Notice, PageHeader } from "../components/Card";
import { EvidenceLink } from "../components/EvidenceLink";
import { inputClass } from "../components/Field";
import { api } from "../lib/api";
import { timeLabel } from "../lib/format";
import { useSession } from "../app/session";

export function SearchScreen() {
  const { timezone } = useSession();
  const [query, setQuery] = useState("");
  const [historical, setHistorical] = useState(false);
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [asked, setAsked] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    setBusy(true);
    setError(null);
    try {
      setResponse(await api.search({ query: q, include_historical: historical, limit: 20 }));
      setAsked(q);
    } catch {
      setError("Search could not run. Nothing was changed.");
    } finally {
      setBusy(false);
    }
  };

  const results = response?.results ?? [];
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Search" sub="Ask what you have already recorded. Answers come only from your own sources." />
      <form onSubmit={(e) => void submit(e)} role="search" className="flex flex-col gap-3">
        <div className="flex gap-2">
          <input
            className={`${inputClass} flex-1`}
            type="search"
            name="q"
            aria-label="Search your notes"
            placeholder="What did I decide about…"
            autoComplete="off"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <Button variant="primary" type="submit" disabled={busy || !query.trim()}>
            {busy ? "Searching…" : "Search"}
          </Button>
        </div>
        <label className="flex min-h-8 items-center gap-2 text-sm text-muted">
          <input type="checkbox" checked={historical} onChange={(e) => setHistorical(e.target.checked)} />
          Include historical and superseded memories
        </label>
      </form>
      {error && <Notice tone="bad" live>{error}</Notice>}
      {busy && <Loading label="Searching" rows={2} />}
      {response && !busy && (
        <section aria-label="Results" className="flex flex-col gap-3">
          {response.index_available === false && (
            <Notice tone="warn">
              The memory index is unavailable{response.degraded_reason ? ` (${response.degraded_reason})` : ""}, so results may be missing.
            </Notice>
          )}
          {response.index_stale && <Notice tone="info">The index may be behind your latest notes.</Notice>}
          {results.length === 0 ? (
            <Empty title="No relevant memory">
              Nothing recorded matches &ldquo;{asked}&rdquo;. That is not the same as it never happening: it may simply not have been captured.
            </Empty>
          ) : (
            <>
              <p role="status" className="text-xs text-muted">
                {results.length} result{results.length === 1 ? "" : "s"} · {response.mode_used} search
              </p>
              <ul className="flex flex-col gap-2">
                {results.map((r) => (
                  <li key={r.memory_id} className="rounded-lg border border-line bg-surface p-4">
                    <p className="font-serif text-base text-ink">{r.content}</p>
                    <p className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-muted">
                      <Chip>{r.kind}</Chip>
                      {r.evidence.validity && r.evidence.validity !== "current" && <Chip>{r.evidence.validity}</Chip>}
                      <span>recorded {timeLabel(r.evidence.recorded_at, timezone)}</span>
                      <EvidenceLink evidence={r.evidence} />
                    </p>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}
    </div>
  );
}
