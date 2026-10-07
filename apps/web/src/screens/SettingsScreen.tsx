/**
 * Settings and cost.
 *
 * Measured model spend and estimated infrastructure cost are labelled
 * separately, because one is a fact and the other is a guess. Technical
 * internals live in the diagnostics block at the bottom, not in the ordinary
 * controls.
 */
import { useEffect, useState } from "react";
import type { UsageResponse } from "../contracts/metis";
import { Button } from "../components/Button";
import { Card, Notice, PageHeader, SectionHeading } from "../components/Card";
import { Field, inputClass } from "../components/Field";
import { useSession } from "../app/session";
import { api } from "../lib/api";

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
  const [timezone, setTimezone] = useState(preferences?.timezone ?? "");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    api.usage().then(setUsage).catch(() => setUsage(null));
  }, []);

  useEffect(() => setTimezone(preferences?.timezone ?? ""), [preferences?.timezone]);

  const saveTimezone = async () => {
    if (!preferences) return;
    setSaving(true);
    setNotice(null);
    try {
      await api.patchPreferences({ expected_revision: preferences.revision ?? 1, timezone });
      await session.refreshPreferences();
      setNotice("Timezone saved.");
    } catch {
      setNotice("That timezone was rejected. Nothing changed.");
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
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <Field label="Timezone" hint="Used for every local date and reminder time.">
                <input className={inputClass} value={timezone} onChange={(e) => setTimezone(e.target.value)} />
              </Field>
            </div>
            <Field label="Focus tasks per day" hint="Read only here.">
              <input className={inputClass} value={preferences?.max_focus_tasks ?? ""} readOnly />
            </Field>
            <Field label="Quiet hours" hint="Read only here.">
              <input
                className={inputClass}
                readOnly
                value={(preferences?.quiet_hours ?? []).map((w) => `${w.start}–${w.end}`).join(", ") || "none"}
              />
            </Field>
            <Field label="Active hours" hint="Read only here.">
              <input
                className={inputClass}
                readOnly
                value={(preferences?.work_hours ?? []).map((w) => `${w.start}–${w.end}`).join(", ") || "none"}
              />
            </Field>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button variant="primary" disabled={saving || !timezone} onClick={() => void saveTimezone()}>
              {saving ? "Saving…" : "Save timezone"}
            </Button>
            <span role="status" className="text-xs text-muted">
              {notice}
            </span>
          </div>
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

        <Card>
          <SectionHeading hint="your data">Export and delete</SectionHeading>
          <p className="max-w-prose text-sm text-muted">
            An export is queued as a job on the server and takes a moment; a delete removes access immediately and the
            derived copies are purged by a follow-up job. Neither is instant, and this screen will say which stage it
            is in rather than claiming completion.
          </p>
          <Notice tone="info" className="mt-3">
            Not available in this build: the export endpoint exists, the confirmation flow does not.
          </Notice>
        </Card>
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
