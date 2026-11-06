import { useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { devLogin, setAccessToken, type Session } from "./api/client";
import Dashboard from "./pages/Dashboard";
import Playground from "./pages/Playground";
import Decisions from "./pages/Decisions";
import Policies from "./pages/Policies";
import Agents from "./pages/Agents";
import AuditTrail from "./pages/AuditTrail";

const NAV = [
  { to: "/", label: "Overview" },
  { to: "/playground", label: "Playground" },
  { to: "/decisions", label: "Decisions & reviews" },
  { to: "/policies", label: "Policies" },
  { to: "/agents", label: "Agents" },
  { to: "/audit", label: "Audit trail" },
];

const ROLES = ["admin", "security_analyst", "developer", "policy_author"];

function Login({ onLogin }: { onLogin: (s: Session) => void }) {
  const [role, setRole] = useState("admin");
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="login card">
      <h1>Sovereign AI Gateway</h1>
      <p className="muted">Development sign-in. Production uses your OIDC provider.</p>
      <label>
        Role{" "}
        <select value={role} onChange={(e) => setRole(e.target.value)}>
          {ROLES.map((r) => <option key={r}>{r}</option>)}
        </select>
      </label>
      <button onClick={() => devLogin(role).then(onLogin).catch((e: Error) => setError(e.message))}>
        Sign in
      </button>
      {error && <p className="bad">{error}</p>}
    </div>
  );
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  if (!session) return <Login onLogin={setSession} />;
  return (
    <div className="layout">
      <nav className="sidebar">
        <h1>Sovereign AI Gateway</h1>
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end>
            {n.label}
          </NavLink>
        ))}
        <div className="whoami muted">
          {session.role}
          <button className="link" onClick={() => { setAccessToken(null); setSession(null); }}>
            Sign out
          </button>
        </div>
      </nav>
      <main className="content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/playground" element={<Playground session={session} />} />
          <Route path="/decisions" element={<Decisions />} />
          <Route path="/policies" element={<Policies />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/audit" element={<AuditTrail />} />
        </Routes>
      </main>
    </div>
  );
}
