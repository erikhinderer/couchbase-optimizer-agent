import { useEffect, useState } from "react";
import { CheckCircle2, ExternalLink, FlaskConical, Lock, ShieldAlert, XCircle } from "lucide-react";
import { api, ApiError } from "@/api/client";
import type { AccessMode, Finding } from "@/api/types";
import ApprovalModal from "./ApprovalModal";

const SEVERITY_CLASS: Record<string, string> = {
  critical: "cb-badge cb-badge--critical",
  warning: "cb-badge cb-badge--warning",
  info: "cb-badge cb-badge--info",
};

export default function FindingCard({ finding, onChanged }: { finding: Finding; onChanged: () => void }) {
  const [showApproval, setShowApproval] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Only matters for gating the Approve/Apply buttons below -- the backend
  // enforces this regardless of what we render here (see core/optimizer.py).
  const [clusterAccessMode, setClusterAccessMode] = useState<AccessMode | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.getCluster(finding.cluster_id).then(
      (c) => { if (!cancelled) setClusterAccessMode(c.access_mode); },
      () => { if (!cancelled) setClusterAccessMode(null); },
    );
    return () => { cancelled = true; };
  }, [finding.cluster_id]);

  const readOnly = clusterAccessMode === "read_only";

  async function reject() {
    const rejectedBy = window.prompt("Your name (for the rejection record):");
    if (!rejectedBy) return;
    setBusy(true);
    setError(null);
    try {
      await api.rejectFinding(finding.finding_id, rejectedBy);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Reject failed.");
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    setBusy(true);
    setError(null);
    try {
      await api.applyFinding(finding.finding_id);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Apply failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`finding-card finding-card--${finding.severity}`}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
        <div>
          <div style={{ fontWeight: 700, fontSize: 14 }}>{finding.title}</div>
          <div style={{ display: "flex", gap: 6, marginTop: 6, flexWrap: "wrap" }}>
            <span className={SEVERITY_CLASS[finding.severity]}>{finding.severity}</span>
            <span className="cb-badge cb-badge--muted">{finding.category}</span>
            <span className="cb-badge cb-badge--muted">
              {finding.action_type === "safe_auto" ? "Safe auto-fix" : "Needs code change"}
            </span>
            <span className="cb-badge cb-badge--muted">{finding.status.replace(/_/g, " ")}</span>
          </div>
        </div>
      </div>

      <div style={{ fontSize: 12.5, color: "var(--text-secondary)", lineHeight: 1.5 }}>{finding.description}</div>

      {finding.suggested_action && (
        <div style={{
          fontFamily: "monospace", fontSize: 11.5, background: "var(--bg-2)", border: "1px solid var(--border-subtle)",
          borderRadius: 8, padding: 9, whiteSpace: "pre-wrap", wordBreak: "break-word",
        }}>
          {(finding.suggested_action["statement"] as string) || (finding.suggested_action["description"] as string)}
        </div>
      )}

      {finding.code_change_guidance && (
        <div style={{ fontSize: 12.5, color: "var(--text-primary)", background: "var(--bg-2)", borderRadius: 8, padding: 10 }}>
          <strong style={{ fontSize: 11, textTransform: "uppercase", color: "var(--cb-amber)" }}>App change needed: </strong>
          {finding.code_change_guidance}
        </div>
      )}

      {finding.suggested_query && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={{ fontSize: 11, textTransform: "uppercase", color: "var(--cb-amber)", fontWeight: 700 }}>
            Suggested optimized query
          </div>
          <div style={{
            fontFamily: "monospace", fontSize: 11.5, background: "var(--bg-2)", border: "1px solid var(--border-subtle)",
            borderRadius: 8, padding: 9, whiteSpace: "pre-wrap", wordBreak: "break-word",
          }}>
            {finding.suggested_query}
          </div>
          <div style={{ fontSize: 10.5, color: "var(--text-muted)" }}>
            Drafted by the agent -- review before use. The application team still owns making this change.
          </div>
        </div>
      )}

      {finding.sandbox_test_result && (
        <div style={{ display: "flex", alignItems: "flex-start", gap: 6, fontSize: 11.5, color: "var(--text-secondary)" }}>
          <FlaskConical size={13} style={{ marginTop: 1, flexShrink: 0 }} />
          {finding.sandbox_test_result.summary}
        </div>
      )}

      {finding.apply_result && (
        <div style={{ fontSize: 11.5, color: finding.status === "applied" ? "var(--cb-green)" : "var(--cb-red-bright)" }}>
          {finding.apply_result}
        </div>
      )}

      {error && <div style={{ fontSize: 11.5, color: "var(--cb-red-bright)" }}>{error}</div>}

      {finding.status === "pending_approval" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              className="cb-btn cb-btn-primary"
              disabled={busy || readOnly}
              title={readOnly ? "This cluster is read-only -- switch it to read/write on the Clusters page to approve changes." : undefined}
              onClick={() => setShowApproval(true)}
            >
              <CheckCircle2 size={13} style={{ marginRight: 6, verticalAlign: -2 }} />
              Approve
            </button>
            <button className="cb-btn" disabled={busy} onClick={reject}>
              <XCircle size={13} style={{ marginRight: 6, verticalAlign: -2 }} />
              Reject
            </button>
          </div>
          {readOnly && (
            <div style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11, color: "var(--text-muted)" }}>
              <Lock size={11} />
              Cluster is read-only -- switch it to read/write on the Clusters page to approve this.
            </div>
          )}
        </div>
      )}

      {finding.status === "approved" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button
              className="cb-btn cb-btn-primary"
              disabled={busy || readOnly}
              title={readOnly ? "This cluster is read-only -- switch it to read/write on the Clusters page to apply changes." : undefined}
              onClick={apply}
            >
              <ShieldAlert size={13} style={{ marginRight: 6, verticalAlign: -2 }} />
              {busy ? "Applying..." : "Apply now"}
            </button>
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Approved by {finding.approved_by}</span>
          </div>
          {readOnly && (
            <div style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11, color: "var(--text-muted)" }}>
              <Lock size={11} />
              Cluster is read-only -- switch it to read/write on the Clusters page to apply this.
            </div>
          )}
        </div>
      )}

      {finding.doc_references.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {finding.doc_references.map((d) => (
            <a key={d.url} href={d.url} target="_blank" rel="noreferrer" className="doc-ref-link">
              <ExternalLink size={12} />
              {d.title}
            </a>
          ))}
        </div>
      )}

      {showApproval && (
        <ApprovalModal
          finding={finding}
          onClose={() => setShowApproval(false)}
          onApprove={async (approvedBy, note) => {
            await api.approveFinding(finding.finding_id, approvedBy, note);
            onChanged();
          }}
        />
      )}
    </div>
  );
}
