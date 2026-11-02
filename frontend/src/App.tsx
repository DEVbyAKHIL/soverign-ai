import { NavLink, Route, Routes } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import Decisions from "./pages/Decisions";
import Policies from "./pages/Policies";
import Agents from "./pages/Agents";
import AuditTrail from "./pages/AuditTrail";

const NAV = [
  { to: "/", label: "Overview" },
  { to: "/decisions", label: "Decisions" },
  { to: "/policies", label: "Policies" },
  { to: "/agents", label: "Agents" },
  { to: "/audit", label: "Audit trail" },
];

export default function App() {
  return (
    <div className="layout">
      <nav className="sidebar">
        <h1>Sovereign AI Gateway</h1>
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end>
            {n.label}
          </NavLink>
        ))}
      </nav>
      <main className="content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/decisions" element={<Decisions />} />
          <Route path="/policies" element={<Policies />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/audit" element={<AuditTrail />} />
        </Routes>
      </main>
    </div>
  );
}
