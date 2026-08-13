import { useEffect, useRef, useState } from "react";
import { api } from "@/api/client";
import { useAgentSocket } from "@/hooks/useAgentSocket";
import { useAppStore } from "@/store/appStore";

type ActivityState = "analyzing" | "validating" | "testing_sandbox" | "applying" | "idle";

const STATE_META: Record<ActivityState, { label: string; color: string; glow: boolean }> = {
  analyzing: { label: "Analyzing", color: "var(--cb-blue)", glow: true },
  validating: { label: "Validating against documentation", color: "var(--cb-teal)", glow: true },
  testing_sandbox: { label: "Testing in WASM sandbox", color: "var(--cb-amber)", glow: true },
  applying: { label: "Applying optimization", color: "var(--cb-red-bright)", glow: true },
  idle: { label: "Idle", color: "var(--cb-green)", glow: false },
};

// If no activity event arrives for a while, fall back to idle rather than
// leaving a stale "analyzing"/"testing" label showing forever -- covers a
// dropped WebSocket frame or a page that connected mid-activity.
const STALE_AFTER_MS = 25000;

export default function AgentStatusIndicator() {
  const [online, setOnline] = useState<boolean | null>(null);
  const [activity, setActivity] = useState<{ state: ActivityState; message: string } | null>(null);
  const { lastEvent } = useAgentSocket();
  const { selectedClusterId } = useAppStore();
  const staleTimer = useRef<ReturnType<typeof setTimeout>>();

  // The scheduler analyzes every registered cluster on its own cadence, not
  // just whichever one is selected -- without this filter, this indicator
  // could show "analyzing"/"idle for cluster X" for a totally different
  // cluster than the one currently in view, which reads as if it's telling
  // you about your current selection when it isn't.
  useEffect(() => {
    setActivity(null);
    clearTimeout(staleTimer.current);
  }, [selectedClusterId]);

  useEffect(() => {
    let cancelled = false;
    async function check() {
      try {
        await api.health();
        if (!cancelled) setOnline(true);
      } catch {
        if (!cancelled) setOnline(false);
      }
    }
    check();
    const id = setInterval(check, 15000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!lastEvent || lastEvent.type !== "agent_activity") return;
    const { state, message, cluster_id } = lastEvent.payload || {};
    if (!state || !(state in STATE_META)) return;
    if (!selectedClusterId || cluster_id !== selectedClusterId) return;

    setActivity({ state, message });
    clearTimeout(staleTimer.current);
    if (state !== "idle") {
      staleTimer.current = setTimeout(() => setActivity(null), STALE_AFTER_MS);
    }
  }, [lastEvent, selectedClusterId]);

  useEffect(() => () => clearTimeout(staleTimer.current), []);

  const meta = activity ? STATE_META[activity.state] : STATE_META.idle;
  const label = online === null ? "Checking agent..." : online === false ? "Agent unreachable" : (activity?.message || meta.label);
  const dotColor = online === false ? "var(--cb-red)" : online === null ? "var(--text-muted)" : meta.color;

  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 8, fontSize: 11.5, color: "var(--text-muted)" }}>
      <span
        className={online && activity && activity.state !== "idle" ? "status-dot-active" : undefined}
        style={{
          width: 7, height: 7, borderRadius: "50%", marginTop: 3, flexShrink: 0,
          background: dotColor,
          boxShadow: online && meta.glow ? `0 0 4px ${dotColor}` : "none",
        }}
      />
      <span style={{ lineHeight: 1.4 }}>{label}</span>
    </div>
  );
}
