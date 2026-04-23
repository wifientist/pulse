import { useEffect } from "react";

import { connectSnapshotStream } from "../api/sse";
import { useAuthStore } from "../store/auth";
import { useSnapshotStore } from "../store/snapshot";

/**
 * Opens the SSE snapshot stream while the user is authenticated. On token change
 * (login/logout) the old connection is aborted and a new one opens.
 */
export function useSnapshotStream(): void {
  const token = useAuthStore((s) => s.token);
  const setSnapshot = useSnapshotStore((s) => s.setSnapshot);
  const setStatus = useSnapshotStore((s) => s.setStatus);
  const noteHeartbeat = useSnapshotStore((s) => s.noteHeartbeat);
  const clearToken = useAuthStore((s) => s.clearToken);
  const reset = useSnapshotStore((s) => s.reset);

  useEffect(() => {
    if (!token) {
      reset();
      return;
    }

    const abort = new AbortController();
    connectSnapshotStream({
      token,
      onSnapshot: setSnapshot,
      onHeartbeat: noteHeartbeat,
      onStatus: setStatus,
      onUnauthorized: () => {
        clearToken();
      },
      signal: abort.signal,
    }).catch(() => {
      // connectSnapshotStream throws on abort or unauthorized — both handled elsewhere.
    });

    return () => {
      abort.abort();
      setStatus("stopped");
    };
  }, [token, setSnapshot, setStatus, noteHeartbeat, clearToken, reset]);
}
