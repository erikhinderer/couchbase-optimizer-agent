import { useEffect, useRef, useState } from "react";
import { WS_BASE_URL } from "@/api/client";

export interface SocketEvent {
  type: string;
  payload: any;
}

/** Keeps a single live WebSocket connection open and exposes the latest
 * event -- used both by pages that want to react to fresh findings/analysis
 * completions, and by the agent chat panel to surface proactive alerts. */
export function useAgentSocket() {
  const [lastEvent, setLastEvent] = useState<SocketEvent | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout>;

    function connect() {
      if (cancelled) return;
      const ws = new WebSocket(`${WS_BASE_URL}/ws`);
      wsRef.current = ws;
      ws.onmessage = (evt) => {
        try {
          setLastEvent(JSON.parse(evt.data));
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        if (!cancelled) retryTimer = setTimeout(connect, 3000);
      };
      ws.onerror = () => ws.close();
    }
    connect();

    return () => {
      cancelled = true;
      clearTimeout(retryTimer);
      wsRef.current?.close();
    };
  }, []);

  return { lastEvent };
}
