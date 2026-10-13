import type { PatchProjectRequest, Project } from "../contracts/metis";

export type ProjectForm = { title: string; aliases: string; objective: string; pinned_context: string; archived: boolean };

export const formFromProject = (p: Project): ProjectForm => ({
  title: p.title,
  aliases: (p.aliases ?? []).join(", "),
  objective: p.objective ?? "",
  pinned_context: p.pinned_context ?? "",
  archived: !!p.archived,
});

/** Only changed fields are sent; a blanked optional text is an explicit null (clear). Null when nothing changed. */
export function projectPatch(p: Project, f: ProjectForm): PatchProjectRequest | null {
  const body: PatchProjectRequest = { expected_revision: p.revision ?? 1 };
  let changed = false;
  const set = <K extends Exclude<keyof PatchProjectRequest, "expected_revision">>(k: K, v: PatchProjectRequest[K]) => {
    body[k] = v;
    changed = true;
  };
  const title = f.title.trim();
  if (title && title !== p.title) set("title", title);
  const aliases = [...new Set(f.aliases.split(",").map((a) => a.trim()).filter(Boolean))];
  if (aliases.join("\n") !== (p.aliases ?? []).join("\n")) set("aliases", aliases);
  for (const k of ["objective", "pinned_context"] as const) {
    const v = f[k].trim();
    if (v !== (p[k] ?? "")) set(k, v || null);
  }
  if (f.archived !== !!p.archived) set("archived", f.archived);
  return changed ? body : null;
}
