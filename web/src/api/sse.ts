export type JobEventType = "started" | "progress" | "succeeded" | "failed";

export interface JobEventPayload {
  job_id: number;
  message?: string;
  data?: Record<string, unknown>;
  created_at?: string;
}

export interface JobEvent extends JobEventPayload {
  type: JobEventType;
}

export interface SseHandle {
  close(): void;
}

const EVENT_TYPES: JobEventType[] = ["started", "progress", "succeeded", "failed"];

/**
 * Subscribe to a job event stream (`/api/events` or `/api/jobs/{id}/events`).
 * Automatically reconnects with exponential backoff (1s -> 30s) on errors.
 * Returns a handle whose `close()` stops the stream and any pending reconnect.
 */
export function subscribeJobEvents(
  url: string,
  onEvent: (event: JobEvent) => void,
  onStateChange?: (connected: boolean) => void,
): SseHandle {
  let source: EventSource | null = null;
  let closed = false;
  let retryDelay = 1000;
  let reconnectTimer: number | undefined;

  const connect = () => {
    if (closed) return;
    source = new EventSource(url, { withCredentials: true });

    source.onopen = () => {
      retryDelay = 1000;
      onStateChange?.(true);
    };

    for (const type of EVENT_TYPES) {
      source.addEventListener(type, (raw) => {
        const msg = raw as MessageEvent<string>;
        try {
          const payload = JSON.parse(msg.data) as JobEventPayload;
          onEvent({ ...payload, type });
        } catch {
          /* malformed event — ignore */
        }
      });
    }

    source.onerror = () => {
      onStateChange?.(false);
      source?.close();
      source = null;
      if (!closed) {
        reconnectTimer = window.setTimeout(connect, retryDelay);
        retryDelay = Math.min(retryDelay * 2, 30000);
      }
    };
  };

  connect();

  return {
    close() {
      closed = true;
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
      source?.close();
      source = null;
    },
  };
}
