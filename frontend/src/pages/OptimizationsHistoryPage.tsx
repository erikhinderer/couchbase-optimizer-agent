import { useEffect, useState } from "react";
import { api } from "@/api/client";
import type { Finding } from "@/api/types";
import { useAppStore } from "@/store/appStore";
import { useFindingsRefresh } from "@/hooks/useFindingsRefresh";
import FindingCard from "@/components/optimizations/FindingCard";
import EmptyClusterState from "@/components/EmptyClusterState";

export default function OptimizationsHistoryPage() {
  const { selectedClusterId } = useAppStore();
  const [findings, setFindings] = useState<Finding[]>([]);

  async function load() {
    if (!selectedClusterId) return;
    const all = await api.listFindings(selectedClusterId);
    setFindings(all.filter((f) => ["applied", "apply_failed", "rejected"].includes(f.status)));
  }

  useEffect(() => {
    load();
  }, [selectedClusterId]);

  useFindingsRefresh(selectedClusterId, load);

  if (!selectedClusterId) return <EmptyClusterState />;

  return (
    <div style={{ padding: 24, display: "flex", flexDirection: "column", gap: 18 }}>
      <div>
        <h1 style={{ fontSize: 20, margin: 0 }}>Applied History</h1>
        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 4 }}>
          Every optimization decision made on this cluster -- applied, failed, or rejected -- stays
          here and in the agent's episodic memory.
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {findings.length === 0 && <div style={{ fontSize: 13, color: "var(--text-muted)" }}>No history yet.</div>}
        {findings.map((f) => (
          <FindingCard key={f.finding_id} finding={f} onChanged={load} />
        ))}
      </div>
    </div>
  );
}
