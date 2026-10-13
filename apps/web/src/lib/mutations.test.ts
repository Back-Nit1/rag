import { afterEach, describe, expect, it, vi } from "vitest";
import { ErrorCode, StatusUpdateAction, type Project } from "../contracts/metis";
import { ApiError, api } from "./api";
import { mutationError } from "./mutationError";
import { formFromProject, projectPatch } from "./projectEdit";
import { openTargetChoices } from "./statusUpdate";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
function mock(res: Response) {
  const calls: { url: string; init: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => (calls.push({ url, init }), res.clone())));
  return calls;
}
afterEach(() => vi.unstubAllGlobals());

const project = { id: "p1", user_id: "u", created_at: "", updated_at: "", title: "Cerno", revision: 3, aliases: ["a"], objective: "Ship" } as Project;

describe("target choice", () => {
  it("lists only unanswered choices that have candidates", () => {
    const choices = [
      { item_index: 0, update_action: StatusUpdateAction.CANCELED, candidate_ids: ["a", "b"], chosen_id: null },
      { item_index: 1, update_action: StatusUpdateAction.CANCELED, candidate_ids: ["a", "b"], chosen_id: "a" },
      { item_index: 2, update_action: StatusUpdateAction.CANCELED, candidate_ids: [] },
    ];
    expect(openTargetChoices({ target_choices: choices }).map((c) => c.item_index)).toEqual([0]);
    expect(openTargetChoices({})).toEqual([]);
  });
  it("posts the expected revision and chosen id, never a user id", async () => {
    const calls = mock(json({ proposal: {} }));
    await api.chooseTarget("pr_1", { expected_revision: 2, item_index: 0, target_id: "ev_2" }, "k1");
    expect(calls[0]!.url).toMatch(/\/v1\/proposals\/pr_1\/target$/);
    expect(JSON.parse(String(calls[0]!.init.body))).toEqual({ expected_revision: 2, item_index: 0, target_id: "ev_2" });
    expect((calls[0]!.init.headers as Record<string, string>)["Idempotency-Key"]).toBe("k1");
  });
});

describe("reminder reschedule", () => {
  it("posts to /reschedule with the expected revision and surfaces the pending state as returned", async () => {
    const calls = mock(json({ id: "r1", state: "pending_schedule", revision: 5 }));
    const r = await api.rescheduleReminder("r1", { expected_revision: 4, local_date: "2026-10-08", local_time: "10:30" }, "k");
    expect(calls[0]!.url).toMatch(/\/v1\/reminders\/r1\/reschedule$/);
    expect(r.state).toBe("pending_schedule");
  });
  it("maps 422 to a shown error and 409 to a refetch", async () => {
    mock(json({ code: ErrorCode.VALIDATION_FAILED, message: "trigger is in the past" }, 422));
    const e422 = await api.rescheduleReminder("r1", { expected_revision: 1 }, "k").catch((e) => e);
    expect(e422).toBeInstanceOf(ApiError);
    expect(mutationError("This reminder", e422)).toEqual({ conflict: false, text: "Not accepted: trigger is in the past" });
    mock(json({ code: ErrorCode.REVISION_CONFLICT, message: "x", current_revision: 9 }, 409));
    const e409 = await api.rescheduleReminder("r1", { expected_revision: 1 }, "k").catch((e) => e);
    expect(mutationError("This reminder", e409).conflict).toBe(true);
  });
});

describe("project edit", () => {
  it("sends nothing when unchanged and only changed fields otherwise", () => {
    expect(projectPatch(project, formFromProject(project))).toBeNull();
    const f = { ...formFromProject(project), title: "Cerno 2", aliases: "a, b, b", objective: "  " };
    expect(projectPatch(project, f)).toEqual({ expected_revision: 3, title: "Cerno 2", aliases: ["a", "b"], objective: null });
  });
  it("sends archived and PATCHes /v1/projects/{id}", async () => {
    const calls = mock(json(project));
    const body = projectPatch(project, { ...formFromProject(project), archived: true })!;
    await api.patchProject("p1", body);
    expect(calls[0]!.init.method).toBe("PATCH");
    expect(JSON.parse(String(calls[0]!.init.body))).toEqual({ expected_revision: 3, archived: true });
  });
});

describe("attach capture to project", () => {
  it("posts expected revision and project id", async () => {
    const calls = mock(json({ id: "src_1", project_id: "p1" }));
    await api.attachCaptureProject("src_1", { expected_revision: 2, project_id: "p1" }, "k");
    expect(calls[0]!.url).toMatch(/\/v1\/captures\/src_1\/project$/);
    expect(JSON.parse(String(calls[0]!.init.body))).toEqual({ expected_revision: 2, project_id: "p1" });
  });
  it("409 is a conflict, 404 says it is gone", async () => {
    mock(json({ code: ErrorCode.REVISION_CONFLICT, message: "x", current_revision: 3 }, 409));
    const e = await api.attachCaptureProject("s", { expected_revision: 1, project_id: "p" }, "k").catch((x) => x);
    expect(mutationError("This note", e).conflict).toBe(true);
  });
});
