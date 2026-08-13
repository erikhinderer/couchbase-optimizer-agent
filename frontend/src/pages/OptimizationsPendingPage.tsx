import { useEffect, useState } from "react";
import { api } from "@/api/client";
import type { Finding } from "@/api/types";
import { useAppStore } from "@/store/appStore";
import { useFindingsRefresh } from "@/hooks/useFindingsRefresh";
import FindingCard from "@/components/optimizations/FindingCard";
import EmptyClusterState from "@/components/EmptyClusterState";

export default function OptimizationsPendingPage() {
  const { selectedClusterId } = useAppStore();
  const [findings, setFindings] = useState<Finding[]>([]);

  async function load() {
    if (!selectedClusterId) return;
    const all = await api.listFindings(selectedClusterId);
    setFindings(all.filter((f) => f.action_type === "safe_auto" && ["pending_approval", "approved"].includes(f.status)));
  }

  useEffect(() => {
    load();
  }, [selectedClusterId]);

  useFindingsRefresh(selectedClusterId, load);

  if (!selectedClusterId) return <EmptyClusterState />;

  return (
    <div style={{ padding: 24, display: "flex", flexDirection: "column", gap: 18 }}>
      <div>
        <h1 style={{ fontSize: 20, margin: 0 }}>Pending Approval</h1>
        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 4 }}>
          Optimizations the agent can perform safely with no application code change -- each one
          runs through the WASM sandbox and is cited to Couchbase documentation before it ever
          reaches you for approval.
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {findings.length === 0 && <div style={{ fontSize: 13, color: "var(--text-muted)" }}>Nothing waiting on approval right now.</div>}
        {findings.map((f) => (
          <FindingCard key={f.finding_id} finding={f} onChanged={load} />
        ))}
      </div>
    </div>
  );
}
