import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip, Legend } from "recharts";

const COLORS: Record<string, string> = { primary: "var(--cb-red)", index: "var(--cb-teal)", other: "var(--bg-3)" };

export default function ScanTypeChart({ breakdown }: { breakdown: { primary: number; index: number; other: number } }) {
  const data = [
    { name: "Primary scan", value: breakdown.primary, key: "primary" },
    { name: "Index scan", value: breakdown.index, key: "index" },
    { name: "Other", value: breakdown.other, key: "other" },
  ].filter((d) => d.value > 0);

  const primaryPct = breakdown.primary + breakdown.index + breakdown.other > 0
    ? Math.round((100 * breakdown.primary) / (breakdown.primary + breakdown.index + breakdown.other))
    : 0;

  return (
    <div className="cb-card" style={{ position: "relative" }}>
      <div style={{ fontWeight: 700, marginBottom: 12 }}>Index Type Usage</div>
      <ResponsiveContainer width="100%" height={220}>
        <PieChart>
          <Pie data={data} dataKey="value" innerRadius={55} outerRadius={85} paddingAngle={2}>
            {data.map((d) => (
              <Cell key={d.key} fill={COLORS[d.key]} />
            ))}
          </Pie>
          <Tooltip contentStyle={{ background: "var(--bg-2)", border: "1px solid var(--border-subtle)" }} />
          <Legend wrapperStyle={{ fontSize: 11, color: "var(--text-secondary)" }} />
        </PieChart>
      </ResponsiveContainer>
      {primaryPct >= 20 && (
        <div style={{
          position: "absolute", top: 44, right: 16, maxWidth: 160, fontSize: 11, color: "var(--cb-amber)",
          background: "rgba(242,169,0,0.1)", border: "1px solid var(--cb-amber)", borderRadius: 8, padding: 8,
        }}>
          {primaryPct}% of scans are primary-index scans -- see Insights for index recommendations.
        </div>
      )}
    </div>
  );
}
