import { describe, expect, it, vi } from "vitest";
import { handleOpenReview, takePendingReview } from "./openReview";

describe("handleOpenReview", () => {
  it("queues a valid id and navigates", () => {
    const go = vi.fn();
    handleOpenReview({ captureId: "cap_1" }, go);
    expect(go).toHaveBeenCalledOnce();
    expect(takePendingReview()).toBe("cap_1");
    expect(takePendingReview()).toBeNull();
  });
  it("ignores malformed and null-id events", () => {
    const go = vi.fn();
    for (const bad of [null, {}, { captureId: "x" }, { captureId: "a/b/c" }, { captureId: null }]) handleOpenReview(bad, go);
    expect(go).not.toHaveBeenCalled();
    expect(takePendingReview()).toBeNull();
  });
});
