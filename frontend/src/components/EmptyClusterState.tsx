import { Link } from "react-router-dom";
import { Server } from "lucide-react";

export default function EmptyClusterState() {
  return (
    <div style={{
      padding: 60, display: "flex", flexDirection: "column", alignItems: "center", gap: 12, textAlign: "center",
    }}>
      <Server size={28} color="var(--text-muted)" />
      <div style={{ fontWeight: 700 }}>No cluster selected</div>
      <div style={{ fontSize: 13, color: "var(--text-secondary)", maxWidth: 360 }}>
        Register a Couchbase Enterprise or Capella cluster to start analysis, or upload a support
        bundle to analyze an offline snapshot instead -- no live connection required.
      </div>
      <div style={{ display: "flex", gap: 10 }}>
        <Link to="/clusters" className="cb-btn cb-btn-primary" style={{ display: "inline-block" }}>
          Go to Clusters
        </Link>
        <Link to="/clusters?register=bundle" className="cb-btn" style={{ display: "inline-block" }}>
          Upload a support bundle
        </Link>
      </div>
    </div>
  );
}
