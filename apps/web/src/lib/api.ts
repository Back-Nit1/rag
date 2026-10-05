/**
 * The only place the browser talks to Metis.
 *
 * Nothing here knows about a model, a provider or a cloud credential: the
 * client calls our own API and the server owns every paid call and every
 * ownership decision. `user_id` is never sent — the server derives it from the
 * authenticated subject.
 */
import {
  type CaptureDetail,
  type CompleteCaptureRequest,
  type CompleteCaptureResponse,
  type CreateCaptureRequest,
  type CreateCaptureResponse,
  type CreateReminderRequest,
  ErrorCode,
  type ErrorBody,
  type Event as MetisEvent,
  type EventPage,
  type HealthResponse,
  type Job,
  type Notification,
  type NotificationPage,
  type PatchTaskRequest,
  type PatchTranscriptRequest,
  type Preferences,
  type Reminder,
  type ResolveProposalRequest,
  type ResolveProposalResponse,
  type Task,
  type TaskPage,
  type TodayResponse,
  type UploadInstructions,
  type UsageResponse,
} from "../contracts/metis";

export const API_BASE: string =
  (import.meta.env?.VITE_METIS_API as string | undefined)?.replace(/\/$/, "") ?? "http://localhost:8000";

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
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
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
    request<{ capture_id: string }>(`/v1/captures/${captureId}`, { method: "DELETE" }),

  resolveProposal: (proposalId: string, body: ResolveProposalRequest, idempotencyKey: string) =>
    request<ResolveProposalResponse>(`/v1/proposals/${proposalId}/resolve`, {
      method: "POST",
      body,
      idempotencyKey,
    }),

  getJob: (jobId: string) => request<Job>(`/v1/jobs/${jobId}`),

  /** Local mode only: the cloud path PUTs the same bytes to a presigned URL. */
  async uploadBytes(upload: UploadInstructions, blob: Blob): Promise<void> {
    const url = upload.url.startsWith("http") ? upload.url : `${API_BASE}${upload.url}`;
    let response: Response;
    try {
      response = await fetch(url, {
        method: upload.method ?? "PUT",
        headers: { "Content-Type": blob.type || "application/octet-stream", ...(upload.headers ?? {}) },
        body: blob,
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
};
