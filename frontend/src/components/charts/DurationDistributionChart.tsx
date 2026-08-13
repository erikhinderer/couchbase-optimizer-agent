import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export default function DurationDistributionChart({ distribution }: { distribution: Record<string, number> }) {
  const data = Object.entries(distribution).map(([bucket, count]) => ({ bucket, count }));
  return (
    <div className="cb-card">
      <div style={{ fontWeight: 700, marginBottom: 12 }}>Query Duration Distribution</div>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" />
          <XAxis dataKey="bucket" tick={{ fill: "var(--text-secondary)", fontSize: 11 }} />
          <YAxis tick={{ fill: "var(--text-secondary)", fontSize: 11 }} />
          <Tooltip contentStyle={{ background: "var(--bg-2)", border: "1px solid var(--border-subtle)" }} />
          <Bar dataKey="count" fill="var(--cb-teal)" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
