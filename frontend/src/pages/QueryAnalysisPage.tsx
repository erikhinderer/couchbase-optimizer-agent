import { useEffect, useState } from "react";
import { api } from "@/api/client";
import type { ClusterSnapshot } from "@/api/types";
import { useAppStore } from "@/store/appStore";
import DurationDistributionChart from "@/components/charts/DurationDistributionChart";
import EmptyClusterState from "@/components/EmptyClusterState";

export default function QueryAnalysisPage() {
  const { selectedClusterId } = useAppStore();
  const [snapshot, setSnapshot] = useState<ClusterSnapshot | null>(null);

  useEffect(() => {
    if (selectedClusterId) api.snapshot(selectedClusterId).then(setSnapshot);
  }, [selectedClusterId]);

  if (!selectedClusterId) return <EmptyClusterState />;

  return (
    <div style={{ padding: 24, display: "flex", flexDirection: "column", gap: 18 }}>
      <div>
        <h1 style={{ fontSize: 20, margin: 0 }}>Query Analysis</h1>
        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 4 }}>
          Top query shapes from system:completed_requests, grouped by normalized statement.
        </div>
      </div>

      {snapshot && <DurationDistributionChart distribution={snapshot.duration_distribution} />}

      <div className="cb-card" style={{ padding: 0, overflow: "hidden" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border-subtle)" }}>
              {["Normalized statement", "Count", "Avg elapsed (ms)"].map((h) => (
                <th key={h} style={{ padding: "10px 14px", fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(snapshot?.top_statements ?? []).map((s, i) => (
              <tr key={i} style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                <td style={{ padding: "10px 14px", fontFamily: "monospace", maxWidth: 520, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {s.normalized_statement}
                </td>
                <td style={{ padding: "10px 14px" }}>{s.count}</td>
                <td style={{ padding: "10px 14px" }}>{s.avg_elapsed_ms}</td>
              </tr>
            ))}
            {(!snapshot || snapshot.top_statements.length === 0) && (
              <tr><td colSpan={3} style={{ padding: 20, textAlign: "center", color: "var(--text-muted)" }}>No query history yet -- run an analysis pass.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
