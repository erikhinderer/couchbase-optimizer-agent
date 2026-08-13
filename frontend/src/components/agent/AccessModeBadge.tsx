import type { ClusterPublic } from "@/api/types";

/**
 * Shown directly below AgentStatusIndicator in the sidebar so it's always
 * visible alongside whatever the agent is doing: which mode the *selected*
 * cluster is registered in, and -- if a role check ran -- whether the
 * credential's actual Couchbase roles line up with that declared mode.
 * Purely informational; the real enforcement is server-side in
 * core/optimizer.py regardless of what this renders.
 */
export default function AccessModeBadge({ cluster }: { cluster: ClusterPublic | null | undefined }) {
  if (!cluster) {
    return (
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6 }}>
        No cluster selected
      </div>
    );
  }

  const isReadWrite = cluster.access_mode === "read_write";
  const label = isReadWrite ? "READ/WRITE" : "READ-ONLY";
  const color = isReadWrite ? "var(--cb-amber)" : "var(--cb-teal)";
  const mismatch = !!cluster.access_mode_note && /don't|doesn't|include write|include an index|obviously include/i.test(cluster.access_mode_note);

  return (
    <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }} title={cluster.access_mode_note ?? undefined}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10.5 }}>
        <span
          style={{
            display: "inline-block", padding: "2px 7px", borderRadius: 4, fontWeight: 700,
            letterSpacing: 0.4, color, border: `1px solid ${color}`, background: "transparent",
          }}
        >
          {label}
        </span>
        {mismatch && (
          <span style={{ color: "var(--cb-red-bright)", fontWeight: 700 }} title={cluster.access_mode_note ?? undefined}>
            !
          </span>
        )}
      </div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", lineHeight: 1.4 }}>
        {cluster.source_type === "support_bundle"
          ? `Static support-bundle snapshot${cluster.bundle_filename ? ` (${cluster.bundle_filename})` : ""} -- not a live connection`
          : isReadWrite
            ? "Agent may approve + apply SAFE_AUTO changes"
            : "Agent analyzes and suggests only"}
      </div>
    </div>
  );
}
