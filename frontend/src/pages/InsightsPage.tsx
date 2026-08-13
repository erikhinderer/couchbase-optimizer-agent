import { useEffect, useMemo, useState } from "react";
import { api } from "@/api/client";
import type { Finding, FindingCategory, FindingSeverity } from "@/api/types";
import { useAppStore } from "@/store/appStore";
import { useFindingsRefresh } from "@/hooks/useFindingsRefresh";
import FindingCard from "@/components/optimizations/FindingCard";
import EmptyClusterState from "@/components/EmptyClusterState";

const CATEGORIES: FindingCategory[] = ["index", "query", "resource", "configuration", "storage"];
const SEVERITIES: FindingSeverity[] = ["critical", "warning", "info"];

export default function InsightsPage() {
  const { selectedClusterId } = useAppStore();
  const [findings, setFindings] = useState<Finding[]>([]);
  const [categoryFilter, setCategoryFilter] = useState<FindingCategory | "all">("all");
  const [severityFilter, setSeverityFilter] = useState<FindingSeverity | "all">("all");

  async function load() {
    if (!selectedClusterId) return;
    setFindings(await api.listFindings(selectedClusterId));
  }

  useEffect(() => {
    load();
  }, [selectedClusterId]);

  useFindingsRefresh(selectedClusterId, load);

  const filtered = useMemo(() => {
    return findings
      .filter((f) => categoryFilter === "all" || f.category === categoryFilter)
      .filter((f) => severityFilter === "all" || f.severity === severityFilter)
      .sort((a, b) => SEVERITIES.indexOf(a.severity) - SEVERITIES.indexOf(b.severity));
  }, [findings, categoryFilter, severityFilter]);

  if (!selectedClusterId) return <EmptyClusterState />;

  return (
    <div style={{ padding: 24, display: "flex", flexDirection: "column", gap: 18 }}>
      <div>
        <h1 style={{ fontSize: 20, margin: 0 }}>Insights</h1>
        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 4 }}>
          Every optimization opportunity the agent has detected for this cluster, safe auto-fixes and
          code-change suggestions together.
        </div>
      </div>

      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Category</span>
          <select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value as any)}>
            <option value="all">All</option>
            {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Severity</span>
          <select value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value as any)}>
            <option value="all">All</option>
            {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {filtered.length === 0 && (
          <div style={{ fontSize: 13, color: "var(--text-muted)" }}>No findings match these filters.</div>
        )}
        {filtered.map((f) => (
          <FindingCard key={f.finding_id} finding={f} onChanged={load} />
        ))}
      </div>
    </div>
  );
}
