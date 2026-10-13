import { ApiError } from "./api";

/** One honest sentence for a failed mutation; `conflict` means refetch before retrying. */
export function mutationError(what: string, cause: unknown): { conflict: boolean; text: string } {
  if (cause instanceof ApiError) {
    if (cause.status === 409)
      return { conflict: true, text: `${what} changed elsewhere. Nothing was overwritten; the latest version is shown.` };
    if (cause.status === 422) return { conflict: false, text: `Not accepted: ${cause.message}` };
    if (cause.status === 404) return { conflict: false, text: `${what} no longer exists.` };
  }
  return { conflict: false, text: "That did not reach Metis; nothing was changed." };
}
