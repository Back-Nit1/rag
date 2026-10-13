/**
 * The only place the browser talks to Metis.
 *
 * Nothing here knows about a model, a provider or a cloud credential: the
 * client calls our own API and the server owns every paid call and every
 * ownership decision. `user_id` is never sent — the server derives it from the
 * authenticated subject.
 */
import {
  type AcceptPlanRequest,
  type AttachCaptureProjectRequest,
  type AvailabilityBlock,
  type CaptureDetail,
  type ChooseTargetRequest,
  type CompleteCaptureRequest,
  type CompleteCaptureResponse,
  type CreateCaptureRequest,
  type CreateCaptureResponse,
  type CreateExportRequest,
  type CreatePlanRequest,
  type CreatePlanResponse,
  type CreateReminderRequest,
  type DeleteCaptureResponse,
  ErrorCode,
  type ErrorBody,
  type Event as MetisEvent,
  type EventPage,
  type ExportStatusResponse,
  type HealthResponse,
  type Job,
  type JobAccepted,
  type Notification,
  type NotificationPage,
  type PatchPlanRequest,
  type PatchProjectRequest,
  type PatchTaskRequest,
  type PatchTranscriptRequest,
  type Plan,
  type Preferences,
  type Project,
  type ProjectDetail,
  type ProjectPage,
  type Reminder,
  type RescheduleReminderRequest,
  type SourceArtifact,
  type SearchRequest,
  type SearchResponse,
  type ResolveProposalRequest,
  type ResolveProposalResponse,
  type Task,
  type TaskPage,
  type TodayResponse,
  type UploadInstructions,
  type UsageResponse,
} from "../contracts/metis";
import { desktopAuthFor, desktopBase, isDesktop } from "./desktop";

export const API_BASE: string =
  (import.meta.env?.VITE_METIS_API as string | undefined)?.replace(/\/$/, "") ?? "http://localhost:8000";

/** Desktop mode without a runtime connection: nothing is sent anywhere. */
export class DesktopUnavailableError extends Error {
  constructor() {
    super("the local Metis services are not available");
    this.name = "DesktopUnavailableError";
  }
}

/** Desktop: only the runtime loopback base, never a fallback. Browser: API_BASE. */
function baseUrl(): string {
  if (!isDesktop()) return API_BASE;
  const base = desktopBase();
  if (!base) throw new DesktopUnavailableError();
  return base;
}

/** A server error with the contract's own shape preserved. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly body: ErrorBody,
  ) {
    super(body.message);
    this.name = "ApiError";
  }
  get code(): ErrorCode {
    return this.body.code;
  }
  get currentRevision(): number | null {
    return this.body.current_revision ?? null;
  }
  get retryAfterSeconds(): number | null {
    return this.body.retry_after_seconds ?? null;
  }
  /** Worth retrying the same request unchanged. */
  get retryable(): boolean {
    return (
      this.status >= 500 ||
      this.code === ErrorCode.RATE_LIMITED ||
      this.code === ErrorCode.PROVIDER_UNAVAILABLE ||
      this.code === ErrorCode.OBJECT_MISSING
    );
  }
}

/** The network itself failed: nothing reached the server, so nothing was lost. */
export class OfflineError extends Error {
  constructor(cause?: unknown) {
    super("the request did not reach Metis");
    this.name = "OfflineError";
    this.cause = cause;
  }
}

export function newCorrelationId(): string {
  return `web_${crypto.randomUUID()}`;
}

/** A bearer token when a real session exists; local fixture mode sends none. */
let authToken: string | null = null;
export function setAuthToken(token: string | null): void {
  authToken = token;
}

/** Called when a signed-in request is refused as unauthenticated (expired or revoked session). */
let onUnauthenticated: (() => void) | null = null;
export function setUnauthenticatedHandler(handler: (() => void) | null): void {
  onUnauthenticated = handler;
}

function authHeaders(url: string): Record<string, string> {
  return isDesktop() ? desktopAuthFor(url) : authToken ? { Authorization: `Bearer ${authToken}` } : {};
}

/** Server-supplied work packet for a project (the route has no response model in the contract). */
export type ResumePacket = {
  project_id: string;
  minutes: number;
  objective: string | null;
  attempts: { summary: string; recorded_at: string; outcome: string }[];
  blocker: string | null;
  provenance: string;
  evidence: { source_id: string; source_version: number; span_ref?: string | null }[];
};

type RequestOptions = {
  method?: string;
  body?: unknown;
  idempotencyKey?: string;
  correlationId?: string;
  signal?: AbortSignal;
};

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {
    "X-Correlation-Id": options.correlationId ?? newCorrelationId(),
  };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
  const url = `${baseUrl()}${path}`;
  Object.assign(headers, authHeaders(url));

  let response: Response;
  try {
    response = await fetch(url, {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new OfflineError(cause);
  }

  if (response.status === 204) return undefined as T;
  const text = await response.text();
  const parsed: unknown = text ? JSON.parse(text) : null;
  if (!response.ok) {
    if (response.status === 401 && authToken) onUnauthenticated?.();
    throw new ApiError(response.status, (parsed ?? {
      code: ErrorCode.INTERNAL,
      message: `the server returned ${response.status}`,
    }) as ErrorBody);
  }
  return parsed as T;
}

export const api = {
  health: () => request<HealthResponse>("/v1/health"),
  limits: () => request<Record<string, unknown>>("/v1/limits"),

  // ----- capture -------------------------------------------------------- //
  /** Idempotent on `client_capture_id`: a retried upload never doubles a note. */
  createCapture: (body: CreateCaptureRequest, idempotencyKey: string) =>
    request<CreateCaptureResponse>("/v1/captures", { method: "POST", body, idempotencyKey }),

  completeCapture: (captureId: string, body: CompleteCaptureRequest, idempotencyKey: string) =>
    request<CompleteCaptureResponse>(`/v1/captures/${captureId}/complete`, {
      method: "POST",
      body,
      idempotencyKey,
    }),

  getCapture: (captureId: string, signal?: AbortSignal) =>
    request<CaptureDetail>(`/v1/captures/${captureId}`, { signal }),

  patchTranscript: (captureId: string, body: PatchTranscriptRequest) =>
    request<CaptureDetail>(`/v1/captures/${captureId}/transcript`, { method: "PATCH", body }),

  deleteCapture: (captureId: string) =>
    request<DeleteCaptureResponse>(`/v1/captures/${captureId}`, { method: "DELETE" }),

  resolveProposal: (proposalId: string, body: ResolveProposalRequest, idempotencyKey: string) =>
    request<ResolveProposalResponse>(`/v1/proposals/${proposalId}/resolve`, {
      method: "POST",
      body,
      idempotencyKey,
    }),

  /** Answer an ambiguous status_update: `target_id` must be a recorded candidate. */
  chooseTarget: (proposalId: string, body: ChooseTargetRequest, idempotencyKey: string) =>
    request<ResolveProposalResponse>(`/v1/proposals/${proposalId}/target`, {
      method: "POST",
      body,
      idempotencyKey,
    }),

  attachCaptureProject: (captureId: string, body: AttachCaptureProjectRequest, idempotencyKey: string) =>
    request<SourceArtifact>(`/v1/captures/${captureId}/project`, { method: "POST", body, idempotencyKey }),

  getJob: (jobId: string) => request<Job>(`/v1/jobs/${jobId}`),

  /** Local mode only: the cloud path PUTs the same bytes to a presigned URL. */
  async uploadBytes(upload: UploadInstructions, blob: Blob): Promise<void> {
    const base = baseUrl();
    const url = upload.url.startsWith("http") ? upload.url : `${base}${upload.url}`;
    // Only our own API origin gets credentials and correlation; a presigned
    // external URL is signed for exactly the headers the server returned.
    const local = new URL(url).origin === new URL(base).origin;
    const own: Record<string, string> = local
      ? {
          "X-Correlation-Id": newCorrelationId(),
          ...(isDesktop() ? desktopAuthFor(url) : authToken ? { Authorization: `Bearer ${authToken}` } : {}),
        }
      : {};
    let response: Response;
    try {
      // S3 presigned POST: every returned field, then the file last, as multipart.
      // The browser sets the multipart Content-Type itself.
      const form = new FormData();
      const fields = upload.method === "POST" ? upload.fields ?? {} : null;
      if (fields) {
        for (const [k, v] of Object.entries(fields)) form.append(k, v);
        form.append("file", blob);
      }
      response = await fetch(url, {
        method: upload.method ?? "PUT",
        headers: fields
          ? { ...(upload.headers ?? {}), ...own }
          : { "Content-Type": blob.type || "application/octet-stream", ...(upload.headers ?? {}), ...own },
        body: fields ? form : blob,
      });
    } catch (cause) {
      throw new OfflineError(cause);
    }
    if (!response.ok) {
      const text = await response.text();
      let body: ErrorBody;
      try {
        body = JSON.parse(text) as ErrorBody;
      } catch {
        body = { code: ErrorCode.INTERNAL, message: `the upload failed with ${response.status}` };
      }
      throw new ApiError(response.status, body);
    }
  },

  // ----- commitments ---------------------------------------------------- //
  today: () => request<TodayResponse>("/v1/today"),
  listTasks: (params: { state?: string; limit?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.state) query.set("state", params.state);
    query.set("limit", String(params.limit ?? 50));
    return request<TaskPage>(`/v1/tasks?${query}`);
  },
  getTask: (taskId: string) => request<Task>(`/v1/tasks/${taskId}`),
  patchTask: (taskId: string, body: PatchTaskRequest) =>
    request<Task>(`/v1/tasks/${taskId}`, { method: "PATCH", body }),
  listEvents: () => request<EventPage>("/v1/events?limit=50"),
  getEvent: (eventId: string) => request<MetisEvent>(`/v1/events/${eventId}`),
  patchEvent: (eventId: string, body: Record<string, unknown>) =>
    request<MetisEvent>(`/v1/events/${eventId}`, { method: "PATCH", body }),

  // ----- reminders and notifications ------------------------------------ //
  createReminder: (body: CreateReminderRequest, idempotencyKey: string) =>
    request<Reminder>("/v1/reminders", { method: "POST", body, idempotencyKey }),
  getReminder: (reminderId: string) => request<Reminder>(`/v1/reminders/${reminderId}`),
  cancelReminder: (reminderId: string) => request<Reminder>(`/v1/reminders/${reminderId}`, { method: "DELETE" }),
  snoozeReminder: (reminderId: string, expectedRevision: number, minutes: number) =>
    request<Reminder>(`/v1/reminders/${reminderId}/snooze`, {
      method: "POST",
      body: { expected_revision: expectedRevision, snooze_minutes: minutes },
    }),
  /** Returns `pending_schedule` until the scheduler confirms; read the reminder again for the outcome. */
  rescheduleReminder: (reminderId: string, body: RescheduleReminderRequest, idempotencyKey: string) =>
    request<Reminder>(`/v1/reminders/${reminderId}/reschedule`, { method: "POST", body, idempotencyKey }),
  listNotifications: () => request<NotificationPage>("/v1/notifications?limit=50"),
  markNotificationRead: (notificationId: string) =>
    request<Notification>(`/v1/notifications/${notificationId}/read`, { method: "POST" }),
  registerPush: (body: { endpoint: string; p256dh: string; auth: string; user_agent?: string }) =>
    request<{ id: string }>("/v1/push-subscriptions", { method: "POST", body }),

  // ----- settings ------------------------------------------------------- //
  preferences: () => request<Preferences>("/v1/preferences"),
  patchPreferences: (body: Record<string, unknown>) =>
    request<Preferences>("/v1/preferences", { method: "PATCH", body }),
  usage: () => request<UsageResponse>("/v1/usage"),

  // ----- projects and memory -------------------------------------------- //
  listProjects: () => request<ProjectPage>("/v1/projects?limit=100"),
  getProject: (projectId: string) => request<ProjectDetail>(`/v1/projects/${projectId}`),
  patchProject: (projectId: string, body: PatchProjectRequest) =>
    request<Project>(`/v1/projects/${projectId}`, { method: "PATCH", body }),
  resumeProject: (projectId: string, minutes: number) =>
    request<ResumePacket>(`/v1/projects/${projectId}/resume`, { method: "POST", body: { minutes } }),
  search: (body: SearchRequest) => request<SearchResponse>("/v1/search", { method: "POST", body }),

  // ----- plans and availability ----------------------------------------- //
  /** Generation is asynchronous; a repeat with the same key replays the first answer. */
  createPlan: (body: CreatePlanRequest, idempotencyKey: string) =>
    request<CreatePlanResponse>("/v1/plans", { method: "POST", body, idempotencyKey }),
  getPlan: (planId: string) => request<Plan>(`/v1/plans/${planId}`),
  acceptPlan: (planId: string, body: AcceptPlanRequest) =>
    request<Plan>(`/v1/plans/${planId}/accept`, { method: "POST", body }),
  patchPlan: (planId: string, body: PatchPlanRequest) =>
    request<Plan>(`/v1/plans/${planId}`, { method: "PATCH", body }),
  listAvailability: (localDate: string) =>
    request<AvailabilityBlock[]>(`/v1/availability?local_date=${encodeURIComponent(localDate)}`),
  /** The server upserts by id; an edit re-posts the same id. */
  saveAvailability: (block: AvailabilityBlock) =>
    request<AvailabilityBlock>("/v1/availability", { method: "POST", body: block }),

  // ----- export ---------------------------------------------------------- //
  createExport: (body: CreateExportRequest, idempotencyKey: string) =>
    request<JobAccepted>("/v1/exports", { method: "POST", body, idempotencyKey }),
  getExport: (jobId: string) => request<ExportStatusResponse>(`/v1/exports/${jobId}`),
  /** Authenticated download: the bearer goes in a header and the bytes are returned as a Blob. */
  async downloadExport(jobId: string): Promise<Blob> {
    const url = `${baseUrl()}/v1/exports/${jobId}/download`;
    let response: Response;
    try {
      response = await fetch(url, { headers: { "X-Correlation-Id": newCorrelationId(), ...authHeaders(url) } });
    } catch (cause) {
      throw new OfflineError(cause);
    }
    if (!response.ok) {
      if (response.status === 401 && authToken) onUnauthenticated?.();
      const text = await response.text();
      let body: ErrorBody;
      try {
        body = JSON.parse(text) as ErrorBody;
      } catch {
        body = { code: ErrorCode.INTERNAL, message: `the download failed with ${response.status}` };
      }
      throw new ApiError(response.status, body);
    }
    return response.blob();
  },
};
