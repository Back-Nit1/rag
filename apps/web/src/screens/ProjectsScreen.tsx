/**
 * Projects: list, detail with a source-backed timeline, and a "resume" work
 * packet. The packet is assembled by the server from recorded sources and is
 * labelled as such; suggested next actions are never presented as verified.
 * There is no project edit route yet, so updates happen by capturing a note.
 */
import { useCallback, useEffect, useState } from "react";
import type { Project, ProjectDetail } from "../contracts/metis";
import { Button } from "../components/Button";
import { Chip, Empty, Loading, Notice, PageHeader, SectionHeading } from "../components/Card";
import { EvidenceLink } from "../components/EvidenceLink";
import { Field, inputClass } from "../components/Field";
import { useSession } from "../app/session";
import { api, type ResumePacket } from "../lib/api";
import { timeLabel } from "../lib/format";

function Detail({ id, onBack }: { id: string; onBack: () => void }) {
  const { timezone } = useSession();
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [minutes, setMinutes] = useState("30");
  const [packet, setPacket] = useState<ResumePacket | null>(null);
  const [resuming, setResuming] = useState(false);

  const load = useCallback(() => {
    api.getProject(id).then((d) => (setDetail(d), setError(null)), () => setError("This project could not be loaded."));
  }, [id]);
  useEffect(load, [load]);

  const resume = async () => {
    setResuming(true);
    try {
      setPacket(await api.resumeProject(id, Number(minutes) || 30));
      setError(null);
    } catch {
      setError("A work packet could not be assembled. Nothing was changed.");
    } finally {
      setResuming(false);
    }
  };

  if (error && !detail) return <Notice tone="bad" live action={<Button onClick={onBack}>Back</Button>}>{error}</Notice>;
  if (!detail) return <Loading label="Loading the project" />;
  const p = detail.project;
  const wp = detail.work_packet;
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title={p.title}
        sub={p.objective ?? "No objective recorded."}
        action={<Button onClick={onBack}>All projects</Button>}
      />
      {error && <Notice tone="warn" live>{error}</Notice>}

      <section aria-labelledby="h-resume" className="rounded-lg border border-line bg-surface p-4">
        <SectionHeading id="h-resume" hint="assembled from your sources">Pick this back up</SectionHeading>
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Minutes available">
            <input className={`${inputClass} w-28`} type="number" min={5} step={5} value={minutes} onChange={(e) => setMinutes(e.target.value)} />
          </Field>
          <Button variant="primary" disabled={resuming} onClick={() => void resume()}>
            {resuming ? "Assembling…" : "Build work packet"}
          </Button>
        </div>
        {packet && (
          <div className="mt-4 flex flex-col gap-2 text-sm">
            <p className="text-xs text-muted">{packet.provenance}</p>
            <p><strong className="font-semibold">Objective:</strong> {packet.objective ?? "not recorded"}</p>
            <p><strong className="font-semibold">Blocker:</strong> {packet.blocker ?? "none recorded"}</p>
            <div>
              <strong className="font-semibold">Earlier attempts:</strong>
              {packet.attempts.length === 0 ? " none recorded" : (
                <ul className="mt-1 list-disc pl-5">
                  {packet.attempts.map((a, i) => (
                    <li key={i}>{a.summary} <span className="text-xs text-muted">({a.outcome}, {timeLabel(a.recorded_at, timezone)})</span></li>
                  ))}
                </ul>
              )}
            </div>
            <p className="text-xs text-muted">No next action is invented here: the server did not return one.</p>
            {packet.evidence.length > 0 && (
              <p className="flex flex-wrap gap-1.5">{packet.evidence.map((e, i) => <EvidenceLink key={i} evidence={e} />)}</p>
            )}
          </div>
        )}
        {wp && (
          <details className="mt-4 text-sm">
            <summary className="min-h-8 cursor-pointer font-medium">
              Saved work packet {detail.work_packet_stale ? "(out of date)" : ""}
            </summary>
            {detail.work_packet_stale && (
              <Notice tone="warn" className="mt-2">Tasks or the project changed since this was assembled. Treat it as history, not current.</Notice>
            )}
            <p className="mt-2"><strong className="font-semibold">Now:</strong> {wp.current_state}</p>
            <p><strong className="font-semibold">Next:</strong> {wp.next_action}</p>
            <p><strong className="font-semibold">Done when:</strong> {wp.completion_criterion}</p>
            {wp.estimate && <p className="text-xs text-muted">{wp.estimate.minutes} min ({wp.estimate.provenance} estimate)</p>}
          </details>
        )}
      </section>

      <section aria-labelledby="h-open">
        <SectionHeading id="h-open" hint={`${detail.open_tasks?.length ?? 0} open`}>Open tasks</SectionHeading>
        {(detail.open_tasks ?? []).length === 0 ? (
          <Empty>No open tasks are linked to this project.</Empty>
        ) : (
          <ul className="divide-y divide-line rounded-lg border border-line bg-surface">
            {(detail.open_tasks ?? []).map((t) => (
              <li key={t.id} className="px-4 py-2.5 text-sm">
                {t.title} <span className="text-xs text-muted">· {t.deadline_local_date ? `due ${t.deadline_local_date}` : "no deadline"}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="h-time">
        <SectionHeading id="h-time" hint="newest recorded first, each with its source">Timeline</SectionHeading>
        {(detail.recent_memories ?? []).length === 0 ? (
          <Empty title="Nothing recorded yet">Capture a note that mentions this project; the server decides what it links.</Empty>
        ) : (
          <ol className="flex flex-col gap-2">
            {[...(detail.recent_memories ?? [])]
              .sort((a, b) => b.evidence.recorded_at.localeCompare(a.evidence.recorded_at))
              .map((m) => (
                <li key={m.memory_id} className="rounded-lg border border-line bg-surface p-3.5">
                  <p className="text-sm text-ink">{m.content}</p>
                  <p className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-muted">
                    <Chip>{m.kind}</Chip>
                    <span>{timeLabel(m.evidence.recorded_at, timezone)}</span>
                    <EvidenceLink evidence={m.evidence} />
                  </p>
                </li>
              ))}
          </ol>
        )}
      </section>
    </div>
  );
}

export function ProjectsScreen() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(() => {
    api.listProjects().then((p) => (setProjects((p.items ?? []).filter((x) => !x.archived)), setError(false)), () => setError(true));
  }, []);
  useEffect(load, [load]);

  if (open) return <Detail id={open} onBack={() => setOpen(null)} />;
  if (error) return <Notice tone="bad" live action={<Button onClick={load}>Try again</Button>}>Projects could not be loaded.</Notice>;
  if (!projects) return <Loading label="Loading projects" />;
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Projects" sub="Longer work, with what you recorded about it." />
      {projects.length === 0 ? (
        <Empty title="No projects yet">Projects appear when a captured note is linked to one. This build cannot create one by hand.</Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {projects.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                onClick={() => setOpen(p.id)}
                className="min-h-14 w-full rounded-lg border border-line bg-surface p-4 text-left hover:bg-raised"
              >
                <span className="block font-serif text-base font-semibold text-ink">{p.title}</span>
                <span className="block text-xs text-muted">{p.objective ?? "No objective recorded."}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
