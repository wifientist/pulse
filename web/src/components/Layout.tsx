import { Activity, LogOut } from "lucide-react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { useAuthStore } from "../store/auth";

import FilterControl from "./FilterControl";
import LiveIndicator from "./LiveIndicator";
import ReconnectingBanner from "./ReconnectingBanner";

export default function Layout() {
  const clearToken = useAuthStore((s) => s.clearToken);
  const navigate = useNavigate();

  const onLogout = () => {
    clearToken();
    navigate("/login", { replace: true });
  };

  return (
    <div className="min-h-screen flex flex-col">
      <header className="bg-white border-b border-slate-200">
        <div className="max-w-7xl mx-auto px-4 h-14 flex items-center gap-6">
          <div className="flex items-center gap-2 text-slate-900">
            <Activity className="w-5 h-5 text-sky-500" />
            <span className="font-semibold">PULSE</span>
          </div>
          <nav className="flex gap-4 text-sm">
            <NavLink
              to="/"
              end
              className={({ isActive }) =>
                isActive
                  ? "text-slate-900 font-medium"
                  : "text-slate-500 hover:text-slate-900"
              }
            >
              Dashboard
            </NavLink>
            <NavLink
              to="/agents"
              className={({ isActive }) =>
                isActive
                  ? "text-slate-900 font-medium"
                  : "text-slate-500 hover:text-slate-900"
              }
            >
              Agents
            </NavLink>
            <NavLink
              to="/trends"
              className={({ isActive }) =>
                isActive
                  ? "text-slate-900 font-medium"
                  : "text-slate-500 hover:text-slate-900"
              }
            >
              Trends
            </NavLink>
            <NavLink
              to="/access-points"
              className={({ isActive }) =>
                isActive
                  ? "text-slate-900 font-medium"
                  : "text-slate-500 hover:text-slate-900"
              }
            >
              Access Points
            </NavLink>
          </nav>
          <div className="ml-auto flex items-center gap-4">
            <FilterControl />
            <LiveIndicator />
            <button
              onClick={onLogout}
              className="text-sm text-slate-500 hover:text-slate-900 inline-flex items-center gap-1"
            >
              <LogOut className="w-4 h-4" /> Log out
            </button>
          </div>
        </div>
      </header>
      <ReconnectingBanner />
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
