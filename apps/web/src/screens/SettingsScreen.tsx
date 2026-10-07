/**
 * Settings and cost.
 *
 * Measured model spend and estimated infrastructure cost are labelled
 * separately, because one is a fact and the other is a guess. Technical
 * internals live in the diagnostics block at the bottom, not in the ordinary
 * controls.
 */
import { useEffect, useState } from "react";
import { JobState, type ExportStatusResponse, type TimeWindow, type UsageResponse } from "../contracts/metis";
import { Button } from "../components/Button";
import { Card, Notice, PageHeader, SectionHeading } from "../components/Card";
import { Field, inputClass } from "../components/Field";
import { useSession } from "../app/session";
import { useAuth } from "../app/AuthGate";
import { ApiError, api } from "../lib/api";
import { buildPatch, dailyScheduleLabel, toDraft, validate, type PrefsDraft } from "../lib/prefs";

const usd = (micro: number) => `$${(micro / 1_000_000).toFixed(2)}`;

function Row({ k, v }: { k: string; v: string | number }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-line py-2 last:border-0">
      <dt className="shrink-0 text-muted">{k}</dt>
      <dd className="min-w-0 break-words text-right font-mono text-xs text-ink">{v}</dd>
    </div>
  );
}

export function SettingsScreen() {
  const session = useSession();
  const { preferences, health, limits, notificationPermission } = session;
  const [usage, setUsage] = useState<UsageResponse | null>(null);
  const auth = useAuth();
  const [draft, setDraft] = useState<PrefsDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<{ tone: "good" | "warn"; text: string } | null>(null);
  const [confirmOut, setConfirmOut] = useState(false);

  useEffect(() => {
    api.usage().then(setUsage).catch(() => setUsage(null));
  }, []);

  useEffect(() => setDraft(preferences ? toDraft(preferences) : null), [preferences]);

  const save = async () => {
    if (!preferences || !draft) return;
    const problem = validate(draft);
    if (problem) return setNotice({ tone: "warn", text: problem });
    const patch = buildPatch(preferences, draft);
    if (Object.keys(patch).length === 0) return setNotice({ tone: "good", text: "Nothing changed." });
    setSaving(true);
    setNotice(null);
    try {
      await api.patchPreferences({ expected_revision: preferences.revision ?? 1, ...patch });
      await session.refreshPreferences();
      setNotice({ tone: "good", text: "Saved." });
    } catch (cause) {
      await session.refreshPreferences();
      setNotice({
        tone: "warn",
        text:
          cause instanceof ApiError && cause.currentRevision !== null
            ? "Your preferences changed elsewhere. Nothing was overwritten; the latest values are shown."
            : cause instanceof ApiError
              ? `Not saved: ${cause.message}`
              : "The request did not reach Metis. Nothing changed.",
      });
    } finally {
      setSaving(false);
    }
  };

  const spent = usage ? usage.measured_model_micro_usd + usage.reserved_micro_usd : 0;
  const pct = usage && usage.daily_limit_micro_usd > 0 ? Math.min(100, (spent / usage.daily_limit_micro_usd) * 100) : 0;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Settings" sub="Preferences, notifications, usage and your data." />

      <div className="grid gap-5 lg:grid-cols-2 lg:items-start">
        <Card>
          <SectionHeading>Preferences</SectionHeading>
          {!draft ? (
            <Notice tone="info">Preferences could not be read right now.</Notice>
          ) : (
            <>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Timezone" hint="Used for every local date and reminder time.">
                  <input className={inputClass} name="timezone" autoComplete="off" spellCheck={false} value={draft.timezone} onChange={(e) => setDraft({ ...draft, timezone: e.target.value })} />
                </Field>
                <Field label="Focus tasks per day" hint="1 to 10.">
                  <input className={inputClass} type="number" min={1} max={10} value={draft.maxFocus} onChange={(e) => setDraft({ ...draft, maxFocus: e.target.value })} />
                </Field>
                <Windows legend="Work hours" value={draft.workHours} onChange={(workHours) => setDraft({ ...draft, workHours })} />
                <Windows legend="Quiet hours" hint="No reminders in these windows. May cross midnight." value={draft.quietHours} onChange={(quietHours) => setDraft({ ...draft, quietHours })} />
                <Field label="Daily budget (USD)" hint="Cap on paid model calls per day. Blank keeps the current value.">
                  <input className={inputClass} type="number" min={0} step="0.01" inputMode="decimal" value={draft.dailyBudgetUsd} onChange={(e) => setDraft({ ...draft, dailyBudgetUsd: e.target.value })} />
                </Field>
              </div>
              <fieldset className="mt-4 rounded-md border border-line p-3">
                <legend className="px-1 text-xs font-medium">Daily plan</legend>
                <div className="flex flex-wrap items-end gap-4">
                  <label className="flex min-h-10 items-center gap-2 text-sm">
                    <input type="checkbox" checked={draft.dailyEnabled} onChange={(e) => setDraft({ ...draft, dailyEnabled: e.target.checked })} />
                    Prepare a plan every morning
                  </label>
                  <Field label="At (local time)">
                    <input className={`${inputClass} w-32`} type="time" value={draft.dailyTime} onChange={(e) => setDraft({ ...draft, dailyTime: e.target.value })} />
                  </Field>
                </div>
                {preferences && (
                  <p className="mt-2 text-xs text-muted" role="status">
                    Server state: <strong className="font-semibold text-ink">{dailyScheduleLabel(preferences).label}</strong>
                  </p>
                )}
              </fieldset>
              <div className="mt-4 flex flex-wrap items-center gap-3">
                <Button variant="primary" disabled={saving} onClick={() => void save()}>
                  {saving ? "Saving…" : "Save preferences"}
                </Button>
                <span role="status" className={`text-xs ${notice?.tone === "warn" ? "text-warn" : "text-muted"}`}>
                  {notice?.text}
                </span>
              </div>
            </>
          )}
        </Card>

        <Card>
          <SectionHeading hint={`channels: ${(preferences?.channels ?? []).join(", ") || "none"}`}>
            Notifications
          </SectionHeading>
          <p className="text-sm text-ink">
            Browser permission:{" "}
            <strong className="font-semibold">
              {notificationPermission === "unsupported" ? "not supported here" : notificationPermission}
            </strong>
          </p>
          {notificationPermission === "unsupported" && (
            <Notice tone="warn" className="mt-3">
              This browser or device cannot receive push. Reminders still appear in the Alerts feed.
            </Notice>
          )}
          <p className="mt-3 max-w-prose text-sm text-muted">
            Web push needs browser and platform support, and delivery is never guaranteed. Every reminder also lands
            in the in-app feed, which is the record we can vouch for.
          </p>
          {draft && (
            <label className="mt-3 flex min-h-10 items-center gap-2 text-sm">
              <input type="checkbox" checked={draft.webPush} onChange={(e) => setDraft({ ...draft, webPush: e.target.checked })} />
              Also send reminders as web push (saved with Preferences)
            </label>
          )}
          <Notice tone="info" className="mt-3">
            Push registration is not available yet: the server does not publish the public key a browser needs to
            subscribe, so this device is not registered and nothing is sent to it. In-app alerts still work.
          </Notice>
          {(notificationPermission === "default" || notificationPermission === "denied") && (
            <Button className="mt-3" onClick={() => void session.requestNotificationPermission()}>
              Ask for permission
            </Button>
          )}
          {notificationPermission === "denied" && (
            <p className="mt-2 text-xs text-muted">Blocked sites can only be re-enabled in the browser&apos;s site settings.</p>
          )}
        </Card>

        <Card>
          <SectionHeading hint={usage ? `for ${usage.local_date}` : "unavailable"}>Usage and cost</SectionHeading>
          {!usage ? (
            <Notice tone="info">Usage could not be read right now.</Notice>
          ) : (
            <>
              <div className="mb-3">
                <div className="mb-1 flex justify-between text-xs text-muted">
                  <span>Measured plus reserved: {usd(spent)}</span>
                  <span>Daily limit {usd(usage.daily_limit_micro_usd)}</span>
                </div>
                <div
                  className="h-2 overflow-hidden rounded-full bg-raised"
                  role="progressbar"
                  aria-label="Share of the daily limit used"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round(pct)}
                >
                  <div className={`h-full ${pct >= 90 ? "bg-warn" : "bg-accent"}`} style={{ width: `${pct}%` }} />
                </div>
              </div>
              <dl className="text-sm">
                <Row k="Measured model spend" v={usd(usage.measured_model_micro_usd)} />
                <Row k="Reserved (in flight)" v={usd(usage.reserved_micro_usd)} />
                <Row
                  k="Infrastructure"
                  v={
                    usage.estimated_infra_micro_usd == null
                      ? "not estimated"
                      : `${usd(usage.estimated_infra_micro_usd)} (estimate)`
                  }
                />
                <Row k="Model calls" v={usage.model_calls} />
                <Row k="Voice minutes" v={(usage.audio_seconds / 60).toFixed(1)} />
                <Row k="Speech jobs" v={usage.speech_jobs} />
              </dl>
            </>
          )}
          {usage?.budget_exhausted && (
            <Notice tone="warn" className="mt-3">
              The daily limit is reached. Paid steps are paused; typed capture and deterministic reminders still work.
            </Notice>
          )}
          {usage?.unmeasured_components?.length ? (
            <p className="mt-3 text-xs text-muted">Not measured here: {usage.unmeasured_components.join(", ")}.</p>
          ) : null}
        </Card>

        <ExportCard />

        {auth.signOut && (
          <Card>
            <SectionHeading hint="signed in">Account</SectionHeading>
            {confirmOut ? (
              <Notice tone="warn" action={
                <div className="flex gap-2">
                  <Button variant="danger" onClick={auth.signOut}>Sign out and clear this device</Button>
                  <Button variant="quiet" onClick={() => setConfirmOut(false)}>Stay signed in</Button>
                </div>
              }>
                Notes not yet sent are held only on this device and will be discarded.
              </Notice>
            ) : (
              <Button onClick={() => setConfirmOut(true)}>Sign out</Button>
            )}
          </Card>
        )}
      </div>

      <details className="group rounded-lg border border-line bg-surface p-4 md:p-5">
        <summary className="flex min-h-8 cursor-pointer items-center justify-between font-serif text-lg font-semibold">
          Diagnostics
          <span className="font-sans text-xs font-normal text-muted">for troubleshooting</span>
        </summary>
        {health && Object.values(health.adapters ?? {}).includes("fixture" as never) && (
          <Notice tone="info" className="mt-3">
            Fixture adapters are labelled on purpose: a fixture result is never presented as production output.
          </Notice>
        )}
        <dl className="mt-3 text-sm">
          <Row k="Mode" v={health?.mode ?? "unknown"} />
          <Row k="Contract version" v={health?.contract_version ?? "?"} />
          <Row
            k="Adapters"
            v={health ? Object.entries(health.adapters ?? {}).map(([k, v]) => `${k}=${v}`).join(", ") : "unknown"}
          />
          <Row k="Upload limit" v={`${limits.max_upload_bytes} bytes / ${limits.max_audio_seconds}s`} />
          <Row k="Speech model" v={preferences?.speech_model_id ?? "unknown"} />
        </dl>
      </details>
    </div>
  );
}

/** Editable list of HH:MM windows. */
function Windows({ legend, hint, value, onChange }: { legend: string; hint?: string; value: TimeWindow[]; onChange: (v: TimeWindow[]) => void }) {
  return (
    <fieldset className="flex min-w-0 flex-col gap-1.5 text-xs sm:col-span-2">
      <legend className="mb-1 font-medium text-ink">{legend}</legend>
      {value.length === 0 && <p className="text-muted">none</p>}
      {value.map((w, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <input aria-label={`${legend} ${i + 1} start`} className={`${inputClass} min-w-0 flex-1`} type="time" value={w.start} onChange={(e) => onChange(value.map((x, j) => (j === i ? { ...x, start: e.target.value } : x)))} />
          <span aria-hidden="true">–</span>
          <input aria-label={`${legend} ${i + 1} end`} className={`${inputClass} min-w-0 flex-1`} type="time" value={w.end} onChange={(e) => onChange(value.map((x, j) => (j === i ? { ...x, end: e.target.value } : x)))} />
          <Button variant="quiet" aria-label={`Remove ${legend} ${i + 1}`} onClick={() => onChange(value.filter((_, j) => j !== i))}>Remove</Button>
        </div>
      ))}
      <div>
        <Button variant="quiet" onClick={() => onChange([...value, { start: "09:00", end: "17:00" }])}>Add window</Button>
      </div>
      {hint && <span className="text-2xs text-muted">{hint}</span>}
    </fieldset>
  );
}

const POLL = new Set<string>([JobState.QUEUED, JobState.STARTING, JobState.UPLOADING]);

/** Export is a server job; this reflects its state and offers an authenticated download only when ready. */
function ExportCard() {
  const [status, setStatus] = useState<ExportStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [key, setKey] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);

  const start = async () => {
    setBusy(true);
    setError(null);
    const k = key ?? `export:${crypto.randomUUID()}`;
    setKey(k); // a retry after a dropped response replays the same job
    try {
      const accepted = await api.createExport({ include_sources: true, include_memories: true, include_commitments: true }, k);
      setKey(null);
      setStatus(null);
      setJobId(accepted.job_id);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The request did not reach Metis. Nothing was queued.");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!jobId) return;
    let stop = false;
    let timer = 0;
    const tick = async () => {
      try {
        const next = await api.getExport(jobId);
        if (stop) return;
        setStatus(next);
        if (next.state === JobState.READY || next.state === JobState.FAILED || next.state === JobState.CANCELED) return;
      } catch {
        if (stop) return;
        setError("The export status could not be read. Retrying.");
      }
      timer = window.setTimeout(tick, 2000);
    };
    void tick();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [jobId]);

  const download = async () => {
    if (!jobId) return;
    try {
      const blob = await api.downloadExport(jobId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `metis-export-${jobId}.zip`;
      a.click();
      URL.revokeObjectURL(url);
      setError(null);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The download did not reach Metis.");
    }
  };

  const working = !!jobId && (!status || POLL.has(status.state) || (status.state !== JobState.READY && status.state !== JobState.FAILED && status.state !== JobState.CANCELED));
  return (
    <Card>
      <SectionHeading hint="your data">Export and delete</SectionHeading>
      <p className="max-w-prose text-sm text-muted">
        An export is a server job and takes a moment. To delete a source, open it from any &ldquo;source&rdquo; link; the
        delete removes access at once and a background job purges derived copies, and its state is shown there.
      </p>
      {error && <Notice tone="warn" live className="mt-3">{error}</Notice>}
      {jobId && (
        <p role="status" className="mt-3 text-sm">
          {!status && "Checking the export…"}
          {status?.state === JobState.READY && `Ready${status.byte_size != null ? ` · ${(status.byte_size / 1024).toFixed(0)} KB` : ""}. Verified by the server.`}
          {status?.state === JobState.FAILED && `The export failed${status.error_message ? `: ${status.error_message}` : ""}.`}
          {status?.state === JobState.CANCELED && "The export was canceled."}
          {working && status && `Working on it (${status.state}). Nothing is downloadable yet.`}
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button disabled={busy || working} onClick={() => void start()}>
          {busy ? "Queuing…" : jobId && !working ? "Export again" : "Request export"}
        </Button>
        {status?.state === JobState.READY && status.download_url && (
          <Button variant="primary" onClick={() => void download()}>Download</Button>
        )}
      </div>
    </Card>
  );
}
