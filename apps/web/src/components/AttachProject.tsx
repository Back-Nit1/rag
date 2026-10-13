/** File a capture (and its not-yet-filed tasks/events) under a project. Server decides what moves. */
import { useEffect, useState } from "react";
import type { CaptureDetail, Project } from "../contracts/metis";
import { api } from "../lib/api";
import { mutationError } from "../lib/mutationError";
import { Button } from "./Button";
import { Card, Notice } from "./Card";
import { Field, inputClass } from "./Field";

export function AttachProject({ capture, onChanged }: { capture: CaptureDetail; onChanged: () => void }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [choice, setChoice] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ bad: boolean; text: string } | null>(null);
  const current = capture.source.project_id ?? null;

  useEffect(() => {
    api.listProjects().then((p) => setProjects((p.items ?? []).filter((x) => !x.archived)), () => setProjects([]));
  }, []);

  if (projects.length === 0) return null;
  const nameOf = (id: string) => projects.find((p) => p.id === id)?.title ?? id;

  const attach = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await api.attachCaptureProject(
        capture.source.id,
        { expected_revision: capture.source.revision ?? 1, project_id: choice },
        `attach:${capture.source.id}:${capture.source.revision ?? 1}:${choice}`,
      );
      setMessage({ bad: false, text: `Filed under ${nameOf(choice)}. Items already in another project were left there.` });
      onChanged();
    } catch (cause) {
      const e = mutationError("This note", cause);
      setMessage({ bad: true, text: e.text });
      if (e.conflict) onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Project" hint={current ? `Now: ${nameOf(current)}` : "Not filed under a project"}>
          <select className={inputClass} value={choice} onChange={(e) => setChoice(e.target.value)}>
            <option value="">Choose…</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.title}
              </option>
            ))}
          </select>
        </Field>
        <Button disabled={busy || !choice || choice === current} onClick={() => void attach()}>
          {busy ? "Filing…" : "File under project"}
        </Button>
      </div>
      {message && (
        <Notice tone={message.bad ? "bad" : "good"} live className="mt-3">
          {message.text}
        </Notice>
      )}
    </Card>
  );
}
