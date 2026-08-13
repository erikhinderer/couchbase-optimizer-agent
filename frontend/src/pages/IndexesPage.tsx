import { useEffect, useState } from "react";
import { api } from "@/api/client";
import type { ClusterSnapshot } from "@/api/types";
import { useAppStore } from "@/store/appStore";
import EmptyClusterState from "@/components/EmptyClusterState";

export default function IndexesPage() {
  const { selectedClusterId } = useAppStore();
  const [snapshot, setSnapshot] = useState<ClusterSnapshot | null>(null);

  useEffect(() => {
    if (selectedClusterId) api.snapshot(selectedClusterId).then(setSnapshot);
  }, [selectedClusterId]);

  if (!selectedClusterId) return <EmptyClusterState />;

  const indexes = (snapshot?.index_catalog ?? []) as Array<Record<string, any>>;

  return (
    <div style={{ padding: 24, display: "flex", flexDirection: "column", gap: 18 }}>
      <div>
        <h1 style={{ fontSize: 20, margin: 0 }}>Indexes</h1>
        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 4 }}>
          Live index catalog from system:indexes.
        </div>
      </div>

      <div className="cb-card" style={{ padding: 0, overflow: "hidden" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border-subtle)" }}>
              {["Name", "Keyspace", "Primary", "State", "Replicas"].map((h) => (
                <th key={h} style={{ padding: "10px 14px", fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {indexes.map((idx, i) => (
              <tr key={i} style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                <td style={{ padding: "10px 14px", fontWeight: 600 }}>{idx.name}</td>
                <td style={{ padding: "10px 14px", color: "var(--text-secondary)" }}>{idx.keyspace_id || idx.bucket_id}</td>
                <td style={{ padding: "10px 14px" }}>{idx.is_primary ? "yes" : "no"}</td>
                <td style={{ padding: "10px 14px" }}>{idx.state}</td>
                <td style={{ padding: "10px 14px" }}>{idx.num_replica ?? 0}</td>
              </tr>
            ))}
            {indexes.length === 0 && (
              <tr><td colSpan={5} style={{ padding: 20, textAlign: "center", color: "var(--text-muted)" }}>No index data yet -- run an analysis pass.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
