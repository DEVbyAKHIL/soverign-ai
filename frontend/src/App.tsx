import { useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { devLogin, setAccessToken, type Session } from "./api/client";
import Dashboard from "./pages/Dashboard";
import Playground from "./pages/Playground";
import Decisions from "./pages/Decisions";
import Policies from "./pages/Policies";
import Agents from "./pages/Agents";
import AuditTrail from "./pages/AuditTrail";
import LiveOverview from "./pages/live/LiveOverview";
import LivePolicies from "./pages/live/LivePolicies";
import LiveAgents from "./pages/live/LiveAgents";
import LivePii from "./pages/live/LivePii";
import LiveInjections from "./pages/live/LiveInjections";
import LiveLatency from "./pages/live/LiveLatency";
import LiveSovereignty from "./pages/live/LiveSovereignty";

const NAV = [
  { to: "/", label: "Overview" },
  { to: "/playground", label: "Playground" },
  { to: "/decisions", label: "Decisions & reviews" },
  { to: "/policies", label: "Policies" },
  { to: "/agents", label: "Agents" },
  { to: "/audit", label: "Audit trail" },
];

// Streaming aggregates from the reporting API (refreshed every 5 s).
const LIVE_NAV = [
  { to: "/live", label: "Real-time overview" },
  { to: "/live/policies", label: "Policy decisions" },
  { to: "/live/agents", label: "Agent risk" },
  { to: "/live/pii", label: "PII detections" },
  { to: "/live/injections", label: "Injection attempts" },
  { to: "/live/latency", label: "Latency & reliability" },
  { to: "/live/sovereignty", label: "Sovereignty" },
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
        <div className="nav-group muted">Real-time (streaming)</div>
        {LIVE_NAV.map((n) => (
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
          <Route path="/live" element={<LiveOverview />} />
          <Route path="/live/policies" element={<LivePolicies />} />
          <Route path="/live/agents" element={<LiveAgents />} />
          <Route path="/live/pii" element={<LivePii />} />
          <Route path="/live/injections" element={<LiveInjections />} />
          <Route path="/live/latency" element={<LiveLatency />} />
          <Route path="/live/sovereignty" element={<LiveSovereignty />} />
        </Routes>
      </main>
    </div>
  );
}
