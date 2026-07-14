import { useState } from "react";
import { Navigate, NavLink, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import {
  ArrowRight,
  Boxes,
  Database,
  FileCode2,
  GitCompareArrows,
  LayoutDashboard,
  LifeBuoy,
  ListChecks,
  MessageSquare,
  Radar,
  RefreshCw,
  ScrollText,
  Table2,
  type LucideIcon,
} from "lucide-react";
import { useAuth } from "./auth";
import AgentSidebar from "./components/AgentSidebar";
import JobDrawer from "./components/JobDrawer";
import AuditLog from "./pages/AuditLog";
import DataBrowser from "./pages/DataBrowser";
import Login from "./pages/Login";
import MappingReview from "./pages/MappingReview";
import Migrations from "./pages/Migrations";
import NightlyRebuild from "./pages/NightlyRebuild";
import Onboarding from "./pages/Onboarding";
import Overview from "./pages/Overview";
import Recovery from "./pages/Recovery";
import SchemaChanges from "./pages/SchemaChanges";
import Tables from "./pages/Tables";
import WorkerQueues from "./pages/WorkerQueues";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
}

export interface NavGroup {
  title: string;
  items: NavItem[];
}

/**
 * Grouped sidebar navigation. Labels use manager vocabulary (see labels.ts);
 * routes keep their internal paths. Exported so the nav can be asserted on
 * without standing up the auth/query providers.
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    title: "Monitor",
    items: [
      { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
      { to: "/data", label: "Data Browser", icon: Table2 },
      { to: "/worker", label: "Sync Queue", icon: ListChecks },
    ],
  },
  {
    title: "Set up",
    items: [
      { to: "/tables", label: "Tables", icon: Boxes },
      { to: "/mappings", label: "Review Queue", icon: GitCompareArrows },
    ],
  },
  {
    title: "Maintain",
    items: [
      { to: "/schema", label: "Schema Changes", icon: Radar },
      { to: "/migrations", label: "Database Updates (SQL)", icon: FileCode2 },
      { to: "/rebuild", label: "Nightly Rebuild", icon: RefreshCw },
      { to: "/recovery", label: "Recovery", icon: LifeBuoy },
      { to: "/audit", label: "Audit Log", icon: ScrollText },
    ],
  },
];

function Shell() {
  const { user, loading, logout } = useAuth();
  const navigate = useNavigate();
  // Closed by default; survives page navigation within the session.
  const [agentOpen, setAgentOpen] = useState(false);

  if (loading) {
    return (
      <div className="loading-screen">
        <span className="mono dim">connecting…</span>
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;

  return (
    <div className="app">
      <nav className="sidebar">
        <div className="sidebar-brand">
          <span className="brand-glyph">
            <Database size={22} strokeWidth={1.75} aria-hidden="true" />
          </span>
          <div>
            <div className="brand-name mono">schema_mapper</div>
            <div className="brand-sub dim">
              IRIMSV <ArrowRight size={11} strokeWidth={2} aria-hidden="true" /> LRMIS sync
            </div>
          </div>
        </div>
        <div className="nav-list">
          {NAV_GROUPS.map((group) => (
            <div className="nav-group" key={group.title}>
              <div className="nav-group-title">{group.title}</div>
              <ul className="nav-group-items">
                {group.items.map((item) => {
                  const Icon = item.icon;
                  return (
                    <li key={item.to}>
                      <NavLink
                        to={item.to}
                        end={item.end}
                        className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
                      >
                        <Icon size={16} strokeWidth={1.75} aria-hidden="true" />
                        <span>{item.label}</span>
                      </NavLink>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
        <div className="sidebar-footer dim mono">admin console</div>
      </nav>

      <div className="main">
        <header className="topbar">
          <div className="topbar-title mono dim">
            irimsv <ArrowRight size={11} strokeWidth={2} aria-hidden="true" /> lrmis
          </div>
          <div className="topbar-user">
            <button
              type="button"
              className="btn btn-sm"
              title="Ask the migration assistant"
              aria-label="Toggle assistant"
              onClick={() => setAgentOpen((v) => !v)}
            >
              <MessageSquare size={14} aria-hidden="true" /> Assistant
            </button>
            <span className="user-chip">
              <span className="mono">{user.username}</span>
              <span className="role-badge">{user.role}</span>
            </span>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => {
                void logout().then(() => navigate("/login"));
              }}
            >
              Log out
            </button>
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>

      <JobDrawer />
      <AgentSidebar open={agentOpen} onClose={() => setAgentOpen(false)} />
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<Shell />}>
        <Route path="/" element={<Overview />} />
        <Route path="/data" element={<DataBrowser />} />
        <Route path="/worker" element={<WorkerQueues />} />
        <Route path="/tables" element={<Tables />} />
        <Route path="/mappings" element={<MappingReview />} />
        <Route path="/mappings/:proposalId" element={<MappingReview />} />
        <Route path="/schema" element={<SchemaChanges />} />
        <Route path="/migrations" element={<Migrations />} />
        <Route path="/rebuild" element={<NightlyRebuild />} />
        <Route path="/recovery" element={<Recovery />} />
        <Route path="/audit" element={<AuditLog />} />
        {/* Preserve existing entry points */}
        <Route path="/onboarding" element={<Onboarding />} />
        {/* Redirects for pages merged/renamed this phase */}
        <Route path="/scanner" element={<Navigate to="/schema" replace />} />
        <Route path="/drift" element={<Navigate to="/schema" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
