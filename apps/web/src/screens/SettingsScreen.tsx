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
import { Card, SectionHeading } from "../components/Card";
import { Field, inputClass } from "../components/Field";
import { useSession } from "../app/session";
import { api } from "../lib/api";

const usd = (micro: number) => `$${(micro / 1_000_000).toFixed(2)}`;

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

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <SectionHeading>Preferences</SectionHeading>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Timezone" hint="Used for every local date and reminder time.">
            <input className={inputClass} value={timezone} onChange={(e) => setTimezone(e.target.value)} />
          </Field>
          <Field label="Focus tasks per day">
            <input className={inputClass} value={preferences?.max_focus_tasks ?? ""} readOnly />
          </Field>
          <Field label="Quiet hours">
            <input
              className={inputClass}
              readOnly
              value={(preferences?.quiet_hours ?? []).map((w) => `${w.start}–${w.end}`).join(", ") || "none"}
            />
          </Field>
          <Field label="Active hours">
            <input
              className={inputClass}
              readOnly
              value={(preferences?.work_hours ?? []).map((w) => `${w.start}–${w.end}`).join(", ") || "none"}
            />
          </Field>
        </div>
        <div className="mt-3 flex items-center gap-2">
          <Button variant="primary" disabled={saving || !timezone} onClick={() => void saveTimezone()}>
            {saving ? "Saving…" : "Save timezone"}
          </Button>
          {notice && <span className="text-xs text-muted">{notice}</span>}
        </div>
      </Card>

      <Card>
        <SectionHeading hint={`channels: ${(preferences?.channels ?? []).join(", ") || "none"}`}>
          Notifications
        </SectionHeading>
        <p className="text-sm text-ink">Permission: {notificationPermission}</p>
        <p className="mt-1 text-xs text-muted">
          Web push needs browser and platform support, and delivery is never guaranteed. Every reminder also lands in
          the in-app feed, which is the record we can vouch for.
        </p>
        {notificationPermission !== "granted" && (
          <Button className="mt-2" onClick={() => void session.requestNotificationPermission()}>
            Ask for permission
          </Button>
        )}
      </Card>

      <Card>
        <SectionHeading hint={usage ? `for ${usage.local_date}` : "unavailable"}>Usage and cost</SectionHeading>
        {!usage ? (
          <p className="text-sm text-muted">Usage could not be read.</p>
        ) : (
          <dl className="grid grid-cols-2 gap-2 text-sm">
            <dt className="text-muted">Measured model spend</dt>
            <dd className="text-ink">{usd(usage.measured_model_micro_usd)}</dd>
            <dt className="text-muted">Reserved (in flight)</dt>
            <dd className="text-ink">{usd(usage.reserved_micro_usd)}</dd>
            <dt className="text-muted">Infrastructure (estimate)</dt>
            <dd className="text-ink">
              {usage.estimated_infra_micro_usd == null ? "not estimated" : `${usd(usage.estimated_infra_micro_usd)} — estimate`}
            </dd>
            <dt className="text-muted">Daily limit</dt>
            <dd className="text-ink">{usd(usage.daily_limit_micro_usd)}</dd>
            <dt className="text-muted">Model calls</dt>
            <dd className="text-ink">{usage.model_calls}</dd>
            <dt className="text-muted">Voice minutes</dt>
            <dd className="text-ink">{(usage.audio_seconds / 60).toFixed(1)}</dd>
            <dt className="text-muted">Speech jobs</dt>
            <dd className="text-ink">{usage.speech_jobs}</dd>
          </dl>
        )}
        {usage?.budget_exhausted && (
          <p className="mt-2 rounded-lg border border-warn/40 bg-warn-soft px-3 py-2 text-xs text-warn">
            The daily limit is reached. Paid steps are paused; typed capture and deterministic reminders still work.
          </p>
        )}
        {usage?.unmeasured_components?.length ? (
          <p className="mt-2 text-xs text-muted">Not measured here: {usage.unmeasured_components.join(", ")}.</p>
        ) : null}
      </Card>

      <Card>
        <SectionHeading hint="your data">Export and delete</SectionHeading>
        <p className="text-xs text-muted">
          An export is queued as a job on the server and takes a moment; a delete removes access immediately and the
          derived copies are purged by a follow-up job. Neither is instant, and the screen will say which stage it is
          in rather than claiming completion.
        </p>
        <p className="mt-2 text-xs text-warn">
          Not wired up in this build — the export endpoint exists, the confirmation flow does not.
        </p>
      </Card>

      <details className="rounded-xl border border-line bg-surface p-4">
        <summary className="cursor-pointer text-sm font-semibold text-ink">Diagnostics</summary>
        <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
          <dt className="text-muted">Mode</dt>
          <dd className="text-ink">{health?.mode ?? "unknown"}</dd>
          <dt className="text-muted">Contract version</dt>
          <dd className="text-ink">{health?.contract_version ?? "?"}</dd>
          <dt className="text-muted">Adapters</dt>
          <dd className="text-ink">
            {health ? Object.entries(health.adapters ?? {}).map(([k, v]) => `${k}=${v}`).join(", ") : "unknown"}
          </dd>
          <dt className="text-muted">Upload limit</dt>
          <dd className="text-ink">{limits.max_upload_bytes} bytes / {limits.max_audio_seconds}s</dd>
          <dt className="text-muted">Speech model</dt>
          <dd className="text-ink">{preferences?.speech_model_id ?? "unknown"}</dd>
        </dl>
        <p className="mt-2 text-xs text-muted">
          Fixture adapters are labelled as fixtures here on purpose: a fixture result is never presented as production
          output.
        </p>
      </details>
    </div>
  );
}
