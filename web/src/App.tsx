import { Navigate, NavLink, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { useAuth } from "./auth";
import JobDrawer from "./components/JobDrawer";
import AuditLog from "./pages/AuditLog";
import DriftReports from "./pages/DriftReports";
import Login from "./pages/Login";
import MappingReview from "./pages/MappingReview";
import Migrations from "./pages/Migrations";
import Onboarding from "./pages/Onboarding";
import Overview from "./pages/Overview";
import SchemaScanner from "./pages/SchemaScanner";
import WorkerQueues from "./pages/WorkerQueues";

const NAV_ITEMS = [
  { to: "/", label: "Overview", end: true },
  { to: "/worker", label: "Worker & Queues" },
  { to: "/scanner", label: "Schema Scanner" },
  { to: "/onboarding", label: "Onboarding Wizard" },
  { to: "/mappings", label: "Mapping Review" },
  { to: "/migrations", label: "Migrations" },
  { to: "/drift", label: "Drift Reports" },
  { to: "/audit", label: "Audit Log" },
];

function Shell() {
  const { user, loading, logout } = useAuth();
  const navigate = useNavigate();

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
          <span className="brand-glyph">▤</span>
          <div>
            <div className="brand-name mono">schema_mapper</div>
            <div className="brand-sub dim">IRIMSV → LRMIS sync</div>
          </div>
        </div>
        <ul className="nav-list">
          {NAV_ITEMS.map((item) => (
            <li key={item.to}>
              <NavLink
                to={item.to}
                end={item.end}
                className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
              >
                {item.label}
              </NavLink>
            </li>
          ))}
        </ul>
        <div className="sidebar-footer dim mono">admin console</div>
      </nav>

      <div className="main">
        <header className="topbar">
          <div className="topbar-title mono dim">irimsv → lrmis</div>
          <div className="topbar-user">
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
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<Shell />}>
        <Route path="/" element={<Overview />} />
        <Route path="/worker" element={<WorkerQueues />} />
        <Route path="/scanner" element={<SchemaScanner />} />
        <Route path="/onboarding" element={<Onboarding />} />
        <Route path="/mappings" element={<MappingReview />} />
        <Route path="/mappings/:proposalId" element={<MappingReview />} />
        <Route path="/migrations" element={<Migrations />} />
        <Route path="/drift" element={<DriftReports />} />
        <Route path="/audit" element={<AuditLog />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
