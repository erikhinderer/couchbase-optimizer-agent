import { useEffect, useState } from "react";
import { WS_BASE_URL } from "@/api/client";

export interface SocketEvent {
  type: string;
  payload: any;
}

type Listener = (event: SocketEvent) => void;

/** Single shared WebSocket connection for the whole app. Every
 * useAgentSocket() call used to open its own independent connection (App.tsx,
 * AgentPanel, AgentStatusIndicator, and every page via useFindingsRefresh all
 * called this separately -- often 4+ live sockets at once). Each of those
 * connections drops and reconnects on its own schedule, so a one-shot
 * broadcast (like analysis_complete) could land while one particular
 * consumer's socket happened to be mid-reconnect: that consumer would
 * silently miss the event while every other consumer, on a different socket
 * that was still open, received it fine. That's what made the post-scan
 * auto-navigate-to-Dashboard unreliable -- the status indicator and chat
 * panel could update normally (their sockets were up) while App.tsx's own
 * socket happened to be down at that exact moment and never got the event to
 * navigate on. A single shared connection means every consumer sees exactly
 * the same event stream, every time. */
class AgentSocket {
  private ws: WebSocket | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private listeners = new Set<Listener>();
  private started = false;

  private connect() {
    const ws = new WebSocket(`${WS_BASE_URL}/ws`);
    this.ws = ws;
    ws.onmessage = (evt) => {
      let parsed: SocketEvent;
      try {
        parsed = JSON.parse(evt.data);
      } catch {
        return; // ignore malformed frames
      }
      for (const listener of this.listeners) listener(parsed);
    };
    ws.onclose = () => {
      this.retryTimer = setTimeout(() => this.connect(), 3000);
    };
    ws.onerror = () => ws.close();
  }

  start() {
    if (this.started) return;
    this.started = true;
    this.connect();
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    this.start();
    return () => {
      this.listeners.delete(listener);
    };
  }
}

// Module-level singleton -- intentionally never torn down when the last
// subscriber unmounts. Keeping the connection alive across route changes
// (e.g. navigating between pages that each call useFindingsRefresh) avoids
// constant reconnect thrash, and the cost of one idle open socket is
// negligible.
const sharedSocket = new AgentSocket();

/** Subscribes to the app's single shared WebSocket connection and exposes
 * the latest event -- used both by pages that want to react to fresh
 * findings/analysis completions, and by the agent chat panel to surface
 * proactive alerts. */
export function useAgentSocket() {
  const [lastEvent, setLastEvent] = useState<SocketEvent | null>(null);

  useEffect(() => {
    return sharedSocket.subscribe(setLastEvent);
  }, []);

  return { lastEvent };
}
