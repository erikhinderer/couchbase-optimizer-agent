import { useState } from "react";
import { api, ApiError } from "@/api/client";
import type { AccessMode, ClusterKind } from "@/api/types";

export default function ClusterForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<ClusterKind>("enterprise");
  const [connectionString, setConnectionString] = useState("");
  const [username, setUsername] = useState("Administrator");
  const [password, setPassword] = useState("");
  const [accessMode, setAccessMode] = useState<AccessMode>("read_only");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await api.registerCluster({
        name,
        kind,
        connection_string: connectionString,
        username,
        password,
        access_mode: accessMode,
      });
      setName("");
      setConnectionString("");
      setPassword("");
      setAccessMode("read_only");
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not register cluster.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} className="cb-card" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ fontWeight: 700 }}>Register a cluster</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div>
          <label style={{ display: "block", fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="prod-cluster" required style={{ width: "100%" }} />
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
        <label style={{ display: "block", fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Connection string</label>
        <input
          value={connectionString}
          onChange={(e) => setConnectionString(e.target.value)}
          placeholder="couchbases://cb.xxxx.cloud.couchbase.com or couchbase://10.0.0.5"
          required
          style={{ width: "100%" }}
        />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div>
          <label style={{ display: "block", fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Username</label>
          <input value={username} onChange={(e) => setUsername(e.target.value)} required style={{ width: "100%" }} />
        </div>
        <div>
          <label style={{ display: "block", fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Password</label>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required style={{ width: "100%" }} />
        </div>
      </div>
      <div>
        <label style={{ display: "block", fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Access mode</label>
        <select value={accessMode} onChange={(e) => setAccessMode(e.target.value as AccessMode)} style={{ width: "100%" }}>
          <option value="read_only">Read-only -- analyze and suggest only (recommended to start)</option>
          <option value="read_write">Read/write -- may approve and apply SAFE_AUTO changes</option>
        </select>
        <div style={{ fontSize: 11, color: "var(--cb-text-dim)", marginTop: 6, lineHeight: 1.5 }}>
          {accessMode === "read_write" ? (
            <>
              Requires a credential with write-capable Couchbase roles (<code>query_manage_index</code>,{" "}
              <code>bucket_admin</code> or <code>cluster_admin</code>) in addition to read access -- the
              agent will refuse to apply changes server-side if the credential doesn't actually have
              them, regardless of this setting. See README &ldquo;Cluster access &amp; permissions&rdquo;.
            </>
          ) : (
            <>
              Only needs read roles (<code>query_system_catalog</code>, <code>ro_admin</code> or{" "}
              <code>data_reader</code>/<code>query_select</code>). The agent can analyze and raise
              findings but will never approve or apply anything against this cluster.
            </>
          )}
        </div>
      </div>
      {error && <div style={{ fontSize: 12, color: "var(--cb-red-bright)" }}>{error}</div>}
      <div>
        <button className="cb-btn cb-btn-primary" disabled={submitting} type="submit">
          {submitting ? "Registering..." : "Register cluster"}
        </button>
      </div>
    </form>
  );
}
