import { useRef, useState } from "react";
import { UploadCloud } from "lucide-react";
import { api, ApiError } from "@/api/client";
import type { ClusterKind } from "@/api/types";

export default function BundleUploadForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<ClusterKind>("enterprise");
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setSubmitting(true);
    setError(null);
    setNote(null);
    try {
      const cluster = await api.uploadSupportBundle(name, kind, file);
      setNote(cluster.bundle_parse_note ?? null);
      setName("");
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not upload support bundle.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} className="cb-card" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ fontWeight: 700, display: "flex", alignItems: "center", gap: 8 }}>
        <UploadCloud size={16} />
        Upload a support bundle
      </div>
      <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5 }}>
        No live cluster needed -- upload a Couchbase support bundle (<code>cbcollect_info</code> output,
        .zip/.tar.gz/.tgz) and the agent analyzes that static snapshot instead. Always read-only: there's
        no live connection to apply changes to. Findings depend on what the bundle actually captured --
        see the parse summary after upload.
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div>
          <label style={{ display: "block", fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="prod-cluster-bundle" required style={{ width: "100%" }} />
        </div>
        <div>
          <label style={{ display: "block", fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Kind</label>
          <select value={kind} onChange={(e) => setKind(e.target.value as ClusterKind)} style={{ width: "100%" }}>
            <option value="enterprise">Couchbase Enterprise (self-hosted)</option>
            <option value="capella">Couchbase Capella</option>
          </select>
        </div>
      </div>

      <div>
        <label style={{ display: "block", fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Support bundle file</label>
        <input
          ref={fileInputRef}
          type="file"
          accept=".zip,.tar.gz,.tgz,.tar"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          required
          style={{ width: "100%" }}
        />
      </div>

      {error && <div style={{ fontSize: 12, color: "var(--cb-red-bright)" }}>{error}</div>}
      {note && (
        <div style={{ fontSize: 12, color: "var(--text-secondary)", background: "var(--bg-2)", borderRadius: 8, padding: 10 }}>
          {note}
        </div>
      )}

      <div>
        <button className="cb-btn cb-btn-primary" disabled={submitting || !file} type="submit">
          {submitting ? "Uploading + parsing..." : "Upload bundle"}
        </button>
      </div>
    </form>
  );
}
