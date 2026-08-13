import { NavLink } from "react-router-dom";
import {
  LayoutDashboard, Lightbulb, Activity, ListTree, ShieldCheck,
  ClipboardList, BrainCircuit, Server,
} from "lucide-react";

interface NavTreeProps {
  insightsCount: number;
  pendingApprovalCount: number;
  needsCodeChangeCount: number;
}

interface Item {
  to: string;
  icon: React.ReactNode;
  label: string;
  end?: boolean;
  count?: number;
}

function Group({ label, items }: { label: string; items: Item[] }) {
  return (
    <div className="nav-tree-group">
      <div className="nav-tree-group-label">{label}</div>
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          className={({ isActive }) => `nav-tree-item${isActive ? " active" : ""}`}
        >
          {item.icon}
          {item.label}
          {!!item.count && <span className="count">{item.count}</span>}
        </NavLink>
      ))}
    </div>
  );
}

export default function NavTree({ insightsCount, pendingApprovalCount, needsCodeChangeCount }: NavTreeProps) {
  return (
    <nav style={{ display: "flex", flexDirection: "column" }}>
      <Group
        label="Overview"
        items={[
          { to: "/", icon: <LayoutDashboard size={16} />, label: "Dashboard", end: true },
          { to: "/insights", icon: <Lightbulb size={16} />, label: "Insights", count: insightsCount },
        ]}
      />
      <Group
        label="Analysis"
        items={[
          { to: "/query-analysis", icon: <Activity size={16} />, label: "Query Analysis" },
          { to: "/indexes", icon: <ListTree size={16} />, label: "Indexes" },
        ]}
      />
      <Group
        label="Optimizations"
        items={[
          { to: "/optimizations/pending", icon: <ShieldCheck size={16} />, label: "Pending Approval", count: pendingApprovalCount },
          { to: "/optimizations/history", icon: <ClipboardList size={16} />, label: "Applied History" },
          { to: "/optimizations/suggested", icon: <Lightbulb size={16} />, label: "Needs Code Change", count: needsCodeChangeCount },
        ]}
      />
      <Group
        label="Agent"
        items={[
          { to: "/memory", icon: <BrainCircuit size={16} />, label: "Memory" },
          { to: "/clusters", icon: <Server size={16} />, label: "Clusters" },
        ]}
      />
    </nav>
  );
}
