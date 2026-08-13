import { useEffect, useState } from "react";
import { api } from "@/api/client";
import type { Finding } from "@/api/types";
import { useAppStore } from "@/store/appStore";
import { useFindingsRefresh } from "@/hooks/useFindingsRefresh";
import FindingCard from "@/components/optimizations/FindingCard";
import EmptyClusterState from "@/components/EmptyClusterState";

export default function OptimizationsSuggestedPage() {
  const { selectedClusterId } = useAppStore();
  const [findings, setFindings] = useState<Finding[]>([]);

  async function load() {
    if (!selectedClusterId) return;
    const all = await api.listFindings(selectedClusterId);
    setFindings(all.filter((f) => f.action_type === "requires_code_change"));
  }

  useEffect(() => {
    load();
  }, [selectedClusterId]);

  useFindingsRefresh(selectedClusterId, load);

  if (!selectedClusterId) return <EmptyClusterState />;

  return (
    <div style={{ padding: 24, display: "flex", flexDirection: "column", gap: 18 }}>
      <div>
        <h1 style={{ fontSize: 20, margin: 0 }}>Needs Code Change</h1>
        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 4 }}>
          The agent can't safely apply these on its own -- they need an application-side change.
          Each one still comes with documentation to validate the recommendation.
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {findings.length === 0 && <div style={{ fontSize: 13, color: "var(--text-muted)" }}>Nothing in this bucket right now.</div>}
        {findings.map((f) => (
          <FindingCard key={f.finding_id} finding={f} onChanged={load} />
        ))}
      </div>
    </div>
  );
}
