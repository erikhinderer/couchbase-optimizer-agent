export function AgentWordmark() {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div
        style={{
          width: 34, height: 34, borderRadius: 9,
          background: "linear-gradient(135deg, var(--cb-red), var(--cb-red-bright))",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontWeight: 800, fontSize: 17, color: "white",
        }}
      >
        C
      </div>
      <div style={{ lineHeight: 1.1 }}>
        <div style={{ fontWeight: 700, fontSize: 20, color: "var(--text-primary)" }}>Couchbase</div>
        <div style={{ fontSize: 20, color: "var(--text-primary)", fontWeight: 700, letterSpacing: 0.02 }}>
          Optimizer Agent
        </div>
      </div>
    </div>
  );
}
