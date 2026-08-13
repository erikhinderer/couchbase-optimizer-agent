import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { RefreshCw, Trash2, Zap } from "lucide-react";
import { api } from "@/api/client";
import ClusterForm from "@/components/clusters/ClusterForm";
import BundleUploadForm from "@/components/clusters/BundleUploadForm";
import { useAppStore } from "@/store/appStore";

const STATUS_CLASS: Record<string, string> = {
  connected: "cb-badge cb-badge--success",
  unreachable: "cb-badge cb-badge--critical",
  unknown: "cb-badge cb-badge--muted",
};

export default function ClustersPage() {
  // Shared with the sidebar (App.tsx) via the store -- registering/deleting
  // here now keeps the "Active cluster" dropdown and status badge in sync
  // instead of only updating this page's own copy of the list.
  const { selectedClusterId, setSelectedClusterId, clusters, refreshClusters } = useAppStore();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [mode, setMode] = useState<"live" | "bundle">(searchParams.get("register") === "bundle" ? "bundle" : "live");

  async function refresh() {
    await refreshClusters();
  }

  useEffect(() => {
    refresh();
  }, []);

  return (
    <div style={{ padding: 24, display: "flex", flexDirection: "column", gap: 20 }}>
      <div>
        <h1 style={{ fontSize: 20, margin: 0 }}>Clusters</h1>
        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 4 }}>
          Register the Couchbase Enterprise or Capella clusters you want continuously analyzed, or
          upload a support bundle to analyze an offline snapshot instead.
        </div>
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <button
          className={mode === "live" ? "cb-btn cb-btn-primary" : "cb-btn"}
          style={{ padding: "6px 14px" }}
          onClick={() => setMode("live")}
        >
          Connect a live cluster
        </button>
        <button
          className={mode === "bundle" ? "cb-btn cb-btn-primary" : "cb-btn"}
          style={{ padding: "6px 14px" }}
          onClick={() => setMode("bundle")}
        >
          Upload a support bundle
        </button>
      </div>

      {mode === "live" ? <ClusterForm onCreated={refresh} /> : <BundleUploadForm onCreated={refresh} />}

      <div className="cb-card" style={{ padding: 0, overflow: "hidden" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border-subtle)" }}>
              {["Name", "Kind", "Source", "Access", "Status", "Last analyzed", ""].map((h) => (
                <th key={h} style={{ padding: "10px 14px", fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {clusters.map((c) => (
              <tr
                key={c.cluster_id}
                style={{
                  borderBottom: "1px solid var(--border-subtle)",
                  background: c.cluster_id === selectedClusterId ? "var(--bg-2)" : "transparent",
                  cursor: "pointer",
                }}
                onClick={() => setSelectedClusterId(c.cluster_id)}
              >
                <td style={{ padding: "10px 14px", fontWeight: 600 }}>{c.name}</td>
                <td style={{ padding: "10px 14px", color: "var(--text-secondary)" }}>{c.kind}</td>
                <td style={{ padding: "10px 14px" }} title={c.bundle_parse_note ?? undefined}>
                  {c.source_type === "support_bundle" ? (
                    <span className="cb-badge cb-badge--muted">bundle: {c.bundle_filename ?? "uploaded"}</span>
                  ) : (
                    <span className="cb-badge cb-badge--muted">live</span>
                  )}
                </td>
                <td style={{ padding: "10px 14px" }} title={c.access_mode_note ?? undefined}>
                  <span className={c.access_mode === "read_write" ? "cb-badge cb-badge--warning" : "cb-badge cb-badge--info"}>
                    {c.access_mode === "read_write" ? "read/write" : "read-only"}
                  </span>
                </td>
                <td style={{ padding: "10px 14px" }}><span className={STATUS_CLASS[c.status]}>{c.status}</span></td>
                <td style={{ padding: "10px 14px", color: "var(--text-secondary)" }}>
                  {c.last_analyzed_at ? new Date(c.last_analyzed_at).toLocaleString() : "never"}
                </td>
                <td style={{ padding: "10px 14px" }} onClick={(e) => e.stopPropagation()}>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button
                      className="cb-btn"
                      style={{ padding: "6px 10px" }}
                      disabled={busyId === c.cluster_id}
                      title="Run analysis now"
                      onClick={async () => {
                        setBusyId(c.cluster_id);
                        try {
                          await api.runAnalysis(c.cluster_id);
                          await refresh();
                          // Jump straight to the results rather than waiting on the
                          // websocket broadcast to drive this -- we already have a
                          // direct, reliable signal here (the awaited HTTP response),
                          // so there's no reason to depend on a second round-trip for
                          // an action the user just explicitly asked for. Also make
                          // sure we're viewing the cluster that was actually analyzed,
                          // in case it wasn't already the active one.
                          setSelectedClusterId(c.cluster_id);
                          navigate("/");
                        } finally {
                          setBusyId(null);
                        }
                      }}
                    >
                      <Zap size={13} />
                    </button>
                    <button
                      className="cb-btn"
                      style={{ padding: "6px 10px" }}
                      title={c.source_type === "support_bundle" ? "Re-check cached bundle snapshot" : "Test connection"}
                      onClick={async () => {
                        await api.testConnection(c.cluster_id);
                        await refresh();
                      }}
                    >
                      <RefreshCw size={13} />
                    </button>
                    <button
                      className="cb-btn"
                      style={{ padding: "6px 10px" }}
                      onClick={async () => {
                        await api.deleteCluster(c.cluster_id);
                        await refresh();
                      }}
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {clusters.length === 0 && (
              <tr><td colSpan={7} style={{ padding: 20, textAlign: "center", color: "var(--text-muted)" }}>No clusters registered yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
