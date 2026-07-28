import {
  Activity,
  BookOpenCheck,
  ExternalLink,
  FilePlus2,
  FileText,
  LayoutDashboard,
  Menu,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { api } from "../api/client";

type ConnectionState = "checking" | "online" | "offline";

const navigation = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/prepare", label: "Prepare an export", icon: FileText },
  { to: "/review", label: "New review", icon: FilePlus2 },
  { to: "/search", label: "Evidence search", icon: Search },
];

export function AppShell() {
  const [menuOpen, setMenuOpen] = useState(false);
  const [connection, setConnection] =
    useState<ConnectionState>("checking");

  useEffect(() => {
    let active = true;
    Promise.all([api.health(), api.databaseHealth()])
      .then(() => active && setConnection("online"))
      .catch(() => active && setConnection("offline"));
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="app-shell">
      <aside className={`sidebar ${menuOpen ? "sidebar--open" : ""}`}>
        <div className="brand">
          <span className="brand__mark">
            <ShieldCheck aria-hidden="true" size={21} />
          </span>
          <div>
            <strong>CACE</strong>
            <span>Operations console</span>
          </div>
          <button
            className="icon-button sidebar__close"
            type="button"
            onClick={() => setMenuOpen(false)}
            aria-label="Close navigation"
          >
            <X size={18} />
          </button>
        </div>

        <nav className="sidebar__nav" aria-label="Primary navigation">
          <p className="nav-label">Workspace</p>
          {navigation.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              onClick={() => setMenuOpen(false)}
              className={({ isActive }) =>
                `nav-item ${isActive ? "nav-item--active" : ""}`
              }
            >
              <Icon aria-hidden="true" size={17} />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="sidebar__footer">
          <div className="scope-note">
            <BookOpenCheck aria-hidden="true" size={17} />
            <div>
              <strong>Five-PCT scope</strong>
              <span>Curated legal snapshot · 22 Jul 2026</span>
            </div>
          </div>
          <a href="/docs" target="_blank" rel="noreferrer">
            API documentation
            <ExternalLink aria-hidden="true" size={14} />
          </a>
        </div>
      </aside>

      {menuOpen ? (
        <button
          className="sidebar-backdrop"
          type="button"
          aria-label="Close navigation"
          onClick={() => setMenuOpen(false)}
        />
      ) : null}

      <div className="app-shell__main">
        <header className="topbar">
          <button
            className="icon-button mobile-menu"
            type="button"
            onClick={() => setMenuOpen(true)}
            aria-label="Open navigation"
          >
            <Menu size={19} />
          </button>
          <div className="topbar__context">
            <Activity aria-hidden="true" size={16} />
            Customs pre-submission audit
          </div>
          <div
            className={`connection-state connection-state--${connection}`}
            role="status"
          >
            <span />
            {connection === "checking"
              ? "Checking service"
              : connection === "online"
                ? "API and database online"
                : "API or database unavailable"}
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
