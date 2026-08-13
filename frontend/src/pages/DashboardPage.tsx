import { useEffect, useState } from "react";
import { api } from "@/api/client";
import type { ClusterSnapshot, Finding } from "@/api/types";
import { useAppStore } from "@/store/appStore";
import { useFindingsRefresh } from "@/hooks/useFindingsRefresh";
import StatCard from "@/components/dashboard/StatCard";
import DurationDistributionChart from "@/components/charts/DurationDistributionChart";
import ScanTypeChart from "@/components/charts/ScanTypeChart";
import FindingCard from "@/components/optimizations/FindingCard";
import EmptyClusterState from "@/components/EmptyClusterState";

export default function DashboardPage() {
  const { selectedClusterId } = useAppStore();
  const [snapshot, setSnapshot] = useState<ClusterSnapshot | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [loading, setLoading] = useState(false);

  async function load() {
    if (!selectedClusterId) return;
    setLoading(true);
    try {
      const [snap, fs] = await Promise.all([
        api.snapshot(selectedClusterId),
        api.listFindings(selectedClusterId),
      ]);
      setSnapshot(snap);
      setFindings(fs);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, [selectedClusterId]);

  // Keep this in sync with analysis passes that complete while the page is
  // open -- otherwise a finding the chat panel already announced can sit
  // invisible here until a manual refresh.
  useFindingsRefresh(selectedClusterId, load);

  if (!selectedClusterId) return <EmptyClusterState />;

  const criticalFindings = findings.filter((f) => f.severity === "critical").slice(0, 3);
  const openCount = findings.filter((f) => !["applied", "rejected", "dismissed"].includes(f.status)).length;
  const pendingSafe = findings.filter((f) => f.action_type === "safe_auto" && f.status === "pending_approval").length;

  return (
    <div style={{ padding: 24, display: "flex", flexDirection: "column", gap: 20 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h1 style={{ fontSize: 20, margin: 0 }}>Dashboard</h1>
          <div style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 4 }}>
            {snapshot ? `${snapshot.queries_examined} queries examined in the most recent lookback window` : "Loading..."}
          </div>
        </div>
        <button className="cb-btn cb-btn-primary" disabled={loading} onClick={load}>
          {loading ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14 }}>
        <StatCard label="Open findings" value={openCount} />
        <StatCard label="Pending approval" value={pendingSafe} sub="Safe, no app code change" />
        <StatCard label="Indexes examined" value={snapshot?.index_catalog.length ?? 0} />
        <StatCard label="Queries examined" value={snapshot?.queries_examined ?? 0} />
      </div>

      {snapshot && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
          <DurationDistributionChart distribution={snapshot.duration_distribution} />
          <ScanTypeChart breakdown={snapshot.scan_type_breakdown} />
        </div>
      )}

      <div>
        <div style={{ fontWeight: 700, marginBottom: 10 }}>Highest severity findings</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {criticalFindings.length === 0 && (
            <div style={{ fontSize: 13, color: "var(--text-muted)" }}>No critical findings right now.</div>
          )}
          {criticalFindings.map((f) => (
            <FindingCard key={f.finding_id} finding={f} onChanged={load} />
          ))}
        </div>
      </div>
    </div>
  );
}
