import { useEffect, useState } from "react";
import { api } from "@/api/client";
import type { MemoryItem } from "@/api/types";
import { useAppStore } from "@/store/appStore";
import EmptyClusterState from "@/components/EmptyClusterState";

const TIERS = [
  { value: "short_term", label: "Short-term", desc: "Rolling working context, self-expires after a few hours." },
  { value: "episodic", label: "Episodic", desc: "One durable record per event: findings, approvals, applies, chats." },
  { value: "long_term", label: "Long-term", desc: "Consolidated, LLM-synthesized baselines and recurring patterns." },
];

export default function MemoryPage() {
  const { selectedClusterId } = useAppStore();
  const [tier, setTier] = useState("episodic");
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(false);

  async function load() {
    if (!selectedClusterId) return;
    setLoading(true);
    try {
      setItems(await api.listMemory(tier, selectedClusterId));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, [selectedClusterId, tier]);

  if (!selectedClusterId) return <EmptyClusterState />;

  return (
    <div style={{ padding: 24, display: "flex", flexDirection: "column", gap: 18 }}>
      <div>
        <h1 style={{ fontSize: 20, margin: 0 }}>Agent Memory</h1>
        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 4 }}>
          What the agent remembers about this cluster, stored in Couchbase Enterprise and recalled
          via native vector search.
        </div>
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        {TIERS.map((t) => (
          <button
            key={t.value}
            className="cb-btn"
            style={{
              background: tier === t.value ? "var(--bg-3)" : undefined,
              borderColor: tier === t.value ? "var(--cb-teal)" : undefined,
            }}
            onClick={() => setTier(t.value)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
        {TIERS.find((t) => t.value === tier)?.desc}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {loading && <div style={{ fontSize: 13, color: "var(--text-muted)" }}>Loading...</div>}
        {!loading && items.length === 0 && (
          <div style={{ fontSize: 13, color: "var(--text-muted)" }}>Nothing in this tier yet for this cluster.</div>
        )}
        {items.map((m, i) => (
          <div key={m.id || i} className="cb-card" style={{ padding: 12 }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--text-muted)" }}>
              <span>{m.kind}</span>
              <span>{new Date(m.created_at).toLocaleString()}</span>
            </div>
            <div style={{ fontSize: 12.5, marginTop: 6 }}>{m.text}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
