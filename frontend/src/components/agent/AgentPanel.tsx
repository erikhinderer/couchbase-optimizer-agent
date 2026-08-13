import { useEffect, useRef, useState } from "react";
import { AlertTriangle, ExternalLink, MessageSquareText, Send, ShieldCheck, X } from "lucide-react";
import { api, ApiError } from "@/api/client";
import { useAgentSocket } from "@/hooks/useAgentSocket";
import { useAppStore } from "@/store/appStore";
import type { DocReference, Finding } from "@/api/types";

interface ChatTurn {
  role: "user" | "assistant" | "alert";
  content: string;
  docRefs?: DocReference[];
  severity?: string;
}

function findingTurn(finding: Finding): ChatTurn {
  return {
    role: "alert",
    severity: finding.severity,
    content:
      `${finding.severity.toUpperCase()} · ${finding.category}: ${finding.title}\n${finding.description}` +
      (finding.action_type === "safe_auto"
        ? "\nThis one is safe to auto-apply once approved -- see Pending Approval."
        : "\nThis one needs an application code change -- see Needs Code Change."),
    docRefs: finding.doc_references,
  };
}

export default function AgentPanel() {
  const { selectedClusterId } = useAppStore();
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [sending, setSending] = useState(false);
  const { lastEvent } = useAgentSocket();
  const scrollRef = useRef<HTMLDivElement>(null);

  // Chat history is cluster-scoped context, not a durable transcript -- carrying
  // findings/replies for cluster A into the conversation after switching to
  // cluster B misleadingly implies they apply to whatever's now selected
  // (this is what made a resolved/stale finding look like it was still live
  // for a different cluster). Clear on every switch, including to "none".
  useEffect(() => {
    setTurns([]);
  }, [selectedClusterId]);

  useEffect(() => {
    if (!lastEvent) return;
    if (lastEvent.type !== "finding") return;
    const finding: Finding = lastEvent.payload?.finding;
    if (!finding) return;
    if (!selectedClusterId || finding.cluster_id !== selectedClusterId) return;
    if (finding.severity === "info") return; // don't interrupt for low-signal findings

    setOpen(true);
    setTurns((prev) => [...prev, findingTurn(finding)]);
  }, [lastEvent, selectedClusterId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [turns, sending]);

  async function send() {
    const text = message.trim();
    if (!text || sending) return;
    setTurns((prev) => [...prev, { role: "user", content: text }]);
    setMessage("");
    setSending(true);
    try {
      const resp = await api.chat(text, selectedClusterId || undefined);
      setTurns((prev) => [...prev, { role: "assistant", content: resp.reply, docRefs: resp.doc_references }]);
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : "Something went wrong reaching the agent.";
      setTurns((prev) => [...prev, { role: "assistant", content: detail }]);
    } finally {
      setSending(false);
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="cb-btn cb-btn-primary"
        style={{
          position: "fixed", bottom: 24, right: 24, borderRadius: 999,
          height: 52, padding: "0 22px", display: "flex", alignItems: "center", justifyContent: "center",
          gap: 9, fontSize: 14, boxShadow: "0 8px 24px rgba(0,0,0,0.35)", zIndex: 50,
        }}
        aria-label="Ask the agent"
      >
        <MessageSquareText size={20} />
        Ask the agent
      </button>
    );
  }

  return (
    <div
      style={{
        position: "fixed", top: 0, right: 0, bottom: 0, width: 420,
        background: "var(--bg-1)", borderLeft: "1px solid var(--border-subtle)",
        borderRadius: 0, display: "flex", flexDirection: "column", overflow: "hidden",
        boxShadow: "-12px 0 40px rgba(0,0,0,0.4)", zIndex: 50,
      }}
    >
      <div style={{
        padding: "14px 16px", borderBottom: "1px solid var(--border-subtle)",
        display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0,
      }}>
        <div style={{ fontSize: 13, fontWeight: 700 }}>Ask the agent</div>
        <button onClick={() => setOpen(false)} style={{ background: "none", border: "none", color: "var(--text-muted)" }}>
          <X size={16} />
        </button>
      </div>

      <div ref={scrollRef} className="cb-scrollbar" style={{ flex: 1, overflowY: "auto", padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
        {turns.length === 0 && (
          <div style={{ fontSize: 12.5, color: "var(--text-primary)", display: "flex", flexDirection: "column", gap: 12 }}>
            <div>
              I continuously analyze this cluster's queries, indexes, and resource usage and surface
              optimization opportunities right here as they come up.
            </div>
            <div>
              <div style={{ fontWeight: 700, marginBottom: 4 }}>Automatically, I:</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <div>- Watch for primary scans, unreplicated indexes, and resource pressure</div>
                <div>- Test safe fixes in a WASM sandbox before proposing them</div>
                <div>- Recall similar past findings from Couchbase-backed memory</div>
              </div>
            </div>
            <div>
              <div style={{ fontWeight: 700, marginBottom: 4 }}>Ask me to:</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <div>- Explain a finding and why it matters</div>
                <div>- Walk through what a suggested index would change</div>
                <div>- Summarize what's changed on this cluster recently</div>
              </div>
            </div>
          </div>
        )}
        {turns.map((t, i) =>
          t.role === "alert" ? (
            <div
              key={i}
              style={{
                alignSelf: "flex-start", maxWidth: "94%", display: "flex", flexDirection: "column", gap: 6,
                background: "var(--bg-2)",
                border: `1px solid ${t.severity === "critical" ? "var(--cb-red)" : "var(--cb-amber)"}`,
                borderRadius: 10, padding: "9px 11px", fontSize: 12,
              }}
            >
              <div style={{ display: "flex", gap: 8 }}>
                {t.severity === "critical" ? (
                  <AlertTriangle size={14} color="var(--cb-red-bright)" style={{ flexShrink: 0, marginTop: 1 }} />
                ) : (
                  <ShieldCheck size={14} color="var(--cb-amber)" style={{ flexShrink: 0, marginTop: 1 }} />
                )}
                <div style={{ whiteSpace: "pre-wrap" }}>{t.content}</div>
              </div>
              <DocRefs refs={t.docRefs} />
            </div>
          ) : (
            <div
              key={i}
              style={{
                alignSelf: t.role === "user" ? "flex-end" : "flex-start",
                maxWidth: "88%",
                background: t.role === "user" ? "var(--cb-red)" : "var(--bg-2)",
                color: t.role === "user" ? "white" : "var(--text-primary)",
                borderRadius: 10, padding: "8px 11px", fontSize: 12.5,
              }}
            >
              <div style={{ whiteSpace: "pre-wrap" }}>{t.content}</div>
              <DocRefs refs={t.docRefs} />
            </div>
          ),
        )}
        {sending && <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Thinking...</div>}
      </div>

      <div style={{ padding: 12, borderTop: "1px solid var(--border-subtle)", display: "flex", gap: 8, flexShrink: 0 }}>
        <input
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="Ask about this cluster..."
          style={{ flex: 1 }}
        />
        <button onClick={send} className="cb-btn cb-btn-primary" style={{ padding: "8px 10px" }} disabled={sending}>
          <Send size={14} />
        </button>
      </div>
    </div>
  );
}

function DocRefs({ refs }: { refs?: DocReference[] }) {
  if (!refs || refs.length === 0) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3, marginTop: 2 }}>
      {refs.map((d) => (
        <a key={d.url} href={d.url} target="_blank" rel="noreferrer" className="doc-ref-link" style={{ borderTop: "none", paddingTop: 0, marginTop: 0 }}>
          <ExternalLink size={11} />
          {d.title}
        </a>
      ))}
    </div>
  );
}
