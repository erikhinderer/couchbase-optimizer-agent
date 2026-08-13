import { useEffect, useState } from "react";
import { Route, Routes, useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import type { Finding } from "@/api/types";
import { AgentWordmark } from "@/assets/Logo";
import NavTree from "@/components/nav/NavTree";
import AgentPanel from "@/components/agent/AgentPanel";
import AgentStatusIndicator from "@/components/agent/AgentStatusIndicator";
import AccessModeBadge from "@/components/agent/AccessModeBadge";
import { useAppStore } from "@/store/appStore";
import { useAgentSocket } from "@/hooks/useAgentSocket";

import DashboardPage from "@/pages/DashboardPage";
import InsightsPage from "@/pages/InsightsPage";
import QueryAnalysisPage from "@/pages/QueryAnalysisPage";
import IndexesPage from "@/pages/IndexesPage";
import OptimizationsPendingPage from "@/pages/OptimizationsPendingPage";
import OptimizationsHistoryPage from "@/pages/OptimizationsHistoryPage";
import OptimizationsSuggestedPage from "@/pages/OptimizationsSuggestedPage";
import MemoryPage from "@/pages/MemoryPage";
import ClustersPage from "@/pages/ClustersPage";

export default function App() {
  const { selectedClusterId, setSelectedClusterId, clusters, refreshClusters } = useAppStore();
  const [findings, setFindings] = useState<Finding[]>([]);
  const { lastEvent } = useAgentSocket();
  const navigate = useNavigate();

  async function loadFindings() {
    if (!selectedClusterId) return;
    setFindings(await api.listFindings(selectedClusterId));
  }

  useEffect(() => {
    refreshClusters();
  }, []);

  useEffect(() => {
    loadFindings();
  }, [selectedClusterId]);

  useEffect(() => {
    if (lastEvent && ["finding", "finding_updated", "finding_approved", "finding_applied", "finding_rejected", "analysis_complete"].includes(lastEvent.type)) {
      loadFindings();
    }
    // A cluster registered/deleted from another tab or a previous session's
    // stale localStorage selection both self-heal here too: any analysis
    // activity is a reasonable trigger to make sure the sidebar's cluster
    // list hasn't drifted from what the backend actually has.
    if (lastEvent && lastEvent.type === "analysis_complete") {
      refreshClusters();
      // Surface results as soon as a scan (manual or scheduled) finishes,
      // rather than leaving the user on whatever page they happened to be
      // on -- the chat panel explains what was found, this makes sure the
      // findings themselves are the first thing they see.
      const eventClusterId = lastEvent.payload?.cluster_id;
      if (!selectedClusterId || eventClusterId === selectedClusterId) {
        navigate("/");
      }
    }
  }, [lastEvent]);

  const openFindings = findings.filter((f) => !["applied", "rejected", "dismissed"].includes(f.status));
  const pendingApproval = findings.filter((f) => f.action_type === "safe_auto" && f.status === "pending_approval");

  return (
    <div style={{ display: "flex", height: "100vh", background: "var(--bg-0)" }}>
      <aside style={{
        width: 250, borderRight: "1px solid var(--border-subtle)", background: "var(--bg-1)",
        display: "flex", flexDirection: "column", padding: "18px 14px", gap: 4, overflowY: "auto",
      }} className="cb-scrollbar">
        <div style={{ padding: "4px 8px 16px" }}>
          <AgentWordmark />
        </div>

        <div style={{ padding: "0 8px 14px" }}>
          <label style={{ display: "block", fontSize: 10.5, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", marginBottom: 6 }}>
            Active cluster
          </label>
          <select
            value={selectedClusterId ?? ""}
            onChange={(e) => setSelectedClusterId(e.target.value || null)}
            style={{ width: "100%" }}
          >
            <option value="">Select a cluster...</option>
            {clusters.map((c) => (
              <option key={c.cluster_id} value={c.cluster_id}>{c.name} ({c.kind})</option>
            ))}
          </select>
        </div>

        <NavTree insightsCount={openFindings.length} pendingApprovalCount={pendingApproval.length} />

        <div style={{ marginTop: "auto", padding: "8px", borderTop: "1px solid var(--border-subtle)", paddingTop: 12 }}>
          <AgentStatusIndicator />
          <AccessModeBadge cluster={clusters.find((c) => c.cluster_id === selectedClusterId)} />
        </div>
      </aside>

      <main style={{ flex: 1, overflow: "auto" }} className="cb-scrollbar">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/insights" element={<InsightsPage />} />
          <Route path="/query-analysis" element={<QueryAnalysisPage />} />
          <Route path="/indexes" element={<IndexesPage />} />
          <Route path="/optimizations/pending" element={<OptimizationsPendingPage />} />
          <Route path="/optimizations/history" element={<OptimizationsHistoryPage />} />
          <Route path="/optimizations/suggested" element={<OptimizationsSuggestedPage />} />
          <Route path="/memory" element={<MemoryPage />} />
          <Route path="/clusters" element={<ClustersPage />} />
        </Routes>
      </main>

      <AgentPanel />
    </div>
  );
}
