import {
  EventStreamContentType,
  fetchEventSource,
} from "@microsoft/fetch-event-source";

import type { SnapshotEvent } from "./types";

export type StreamStatus = "connecting" | "live" | "reconnecting" | "stopped";

export interface SnapshotStreamHandlers {
  token: string;
  onSnapshot: (snapshot: SnapshotEvent) => void;
  onHeartbeat?: (emittedAt: number) => void;
  onStatus: (status: StreamStatus) => void;
  onUnauthorized: () => void;
  signal: AbortSignal;
}

const BACKOFF_SCHEDULE_MS = [1_000, 2_000, 4_000, 8_000, 15_000];

export async function connectSnapshotStream({
  token,
  onSnapshot,
  onHeartbeat,
  onStatus,
  onUnauthorized,
  signal,
}: SnapshotStreamHandlers): Promise<void> {
  let retryIndex = 0;

  onStatus("connecting");
  await fetchEventSource("/v1/admin/events", {
    method: "GET",
    signal,
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "text/event-stream",
    },
    openWhenHidden: true,

    async onopen(response) {
      if (response.ok && response.headers.get("content-type")?.includes(EventStreamContentType)) {
        retryIndex = 0;
        onStatus("live");
        return;
      }
      if (response.status === 401) {
        onUnauthorized();
        throw new Error("unauthorized");
      }
      throw new Error(`SSE handshake failed: ${response.status}`);
    },

    onmessage(msg) {
      if (!msg.data) return;
      if (msg.event === "heartbeat") {
        try {
          const parsed = JSON.parse(msg.data) as { ts: number };
          onHeartbeat?.(parsed.ts);
        } catch {
          /* ignore malformed heartbeat */
        }
        return;
      }
      if (msg.event === "snapshot" || msg.event === "") {
        try {
          const parsed = JSON.parse(msg.data) as SnapshotEvent;
          onSnapshot(parsed);
          onStatus("live");
        } catch (err) {
          console.warn("sse: bad snapshot payload", err);
        }
      }
    },

    onerror(err) {
      // Returning a number from onerror tells fetchEventSource how long to wait
      // before retrying. Throwing aborts.
      if (signal.aborted) {
        throw err;
      }
      onStatus("reconnecting");
      const wait = BACKOFF_SCHEDULE_MS[Math.min(retryIndex, BACKOFF_SCHEDULE_MS.length - 1)];
      retryIndex++;
      return wait;
    },

    onclose() {
      if (!signal.aborted) {
        onStatus("reconnecting");
      } else {
        onStatus("stopped");
      }
    },
  });
}
