import { useState } from "react";
import { X } from "lucide-react";
import type { Finding } from "@/api/types";

interface Props {
  finding: Finding;
  onClose: () => void;
  onApprove: (approvedBy: string, note: string) => Promise<void>;
}

export default function ApprovalModal({ finding, onClose, onApprove }: Props) {
  const [approvedBy, setApprovedBy] = useState("");
  const [note, setNote] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const statement = (finding.suggested_action?.["statement"] as string) || (finding.suggested_action?.["description"] as string) || "";

  async function submit() {
    if (!approvedBy.trim() || !confirmed) return;
    setSubmitting(true);
    setError(null);
    try {
      await onApprove(approvedBy.trim(), note.trim());
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Approval failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", display: "flex",
      alignItems: "center", justifyContent: "center", zIndex: 100,
    }}>
      <div className="cb-card" style={{ width: 480, maxWidth: "90vw" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <div style={{ fontWeight: 700, fontSize: 15 }}>Approve optimization</div>
          <button onClick={onClose} style={{ background: "none", border: "none", color: "var(--text-muted)" }}>
            <X size={18} />
          </button>
        </div>

        <div style={{ fontSize: 13, color: "var(--text-primary)", marginBottom: 12 }}>{finding.title}</div>

        {statement && (
          <div style={{
            fontFamily: "monospace", fontSize: 12, background: "var(--bg-2)", border: "1px solid var(--border-subtle)",
            borderRadius: 8, padding: 10, marginBottom: 14, whiteSpace: "pre-wrap", wordBreak: "break-word",
          }}>
            {statement}
          </div>
        )}

        {finding.sandbox_test_result && (
          <div style={{
            fontSize: 12, marginBottom: 14, padding: 10, borderRadius: 8,
            background: finding.sandbox_test_result.passed ? "rgba(46,204,113,0.1)" : "rgba(242,169,0,0.1)",
            border: `1px solid ${finding.sandbox_test_result.passed ? "var(--cb-green)" : "var(--cb-amber)"}`,
          }}>
            Sandbox tested: {finding.sandbox_test_result.summary}
          </div>
        )}

        <label style={{ display: "block", fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Your name</label>
        <input value={approvedBy} onChange={(e) => setApprovedBy(e.target.value)} placeholder="e.g. Jamie Chen" style={{ width: "100%", marginBottom: 12 }} />

        <label style={{ display: "block", fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Note (optional)</label>
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} style={{ width: "100%", marginBottom: 12, resize: "vertical" }} />

        <label style={{ display: "flex", alignItems: "flex-start", gap: 8, fontSize: 12, color: "var(--text-secondary)", marginBottom: 16 }}>
          <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} style={{ marginTop: 2 }} />
          I've reviewed this change and confirm it's safe to apply to this cluster with no application code change required.
        </label>

        {error && <div style={{ fontSize: 12, color: "var(--cb-red-bright)", marginBottom: 12 }}>{error}</div>}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
          <button className="cb-btn" onClick={onClose}>Cancel</button>
          <button
            className="cb-btn cb-btn-primary"
            disabled={!approvedBy.trim() || !confirmed || submitting}
            onClick={submit}
          >
            {submitting ? "Approving..." : "Approve"}
          </button>
        </div>
      </div>
    </div>
  );
}
