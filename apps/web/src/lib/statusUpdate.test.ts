import { describe, expect, it } from "vitest";
import { targetCandidates, updateTarget, updateTargetText } from "./statusUpdate";

describe("statusUpdate", () => {
  it("resolves only with exactly one target id", () => {
    expect(updateTarget({ target_ids: ["t1"] })).toEqual({ id: "t1" });
    expect(updateTarget({ target_ids: [] })).toBeNull();
    expect(updateTarget({})).toBeNull();
    expect(updateTarget({ target_ids: ["a", "b"] })).toBeNull();
  });
  it("says needs a target when unresolved and never claims a change", () => {
    expect(updateTargetText({ target_ids: ["a", "b"] })).toBe("Needs a target");
    expect(updateTargetText({ target_ids: ["t1"] })).toMatch(/t1.*Nothing changes/);
  });
  it("lists candidates only for target clarifications", () => {
    expect(targetCandidates({ missing_fields: ["target"], candidate_ids: ["a", "b"] })).toEqual(["a", "b"]);
    expect(targetCandidates({ missing_fields: ["event_date_time"], candidate_ids: ["a"] })).toEqual([]);
    expect(targetCandidates({ missing_fields: ["target"] })).toEqual([]);
  });
});
