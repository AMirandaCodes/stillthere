import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { ShieldCheck, LogOut } from "lucide-react";
import { useAuth } from "@/context/AuthContext";

const guestNavLinks = [{ to: "/", label: "Verify", end: true }];

export default function Layout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  async function handleLogout() {
    await logout();
    navigate("/login");
  }

  const navLinks = user
    ? [
        { to: "/",        label: "Verify",      end: true },
        { to: "/history", label: "History" },
        { to: "/batch",   label: "Batch Upload" },
        { to: "/jobs",    label: "Batch Jobs" },
        ...(user.is_admin ? [{ to: "/admin", label: "Admin" }] : []),
      ]
    : guestNavLinks;

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="border-b border-gray-200 bg-white">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-5">
            <Link to="/" className="flex items-center gap-2 text-brand-600 hover:text-brand-700">
              <ShieldCheck className="h-5 w-5" />
              <span className="text-base font-semibold tracking-tight">StillThere</span>
            </Link>

            <div className="h-5 w-px bg-gray-200" />

            <nav className="flex items-center gap-0.5">
              {navLinks.map(({ to, label, end }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={end}
                  className={({ isActive }) =>
                    `rounded-md px-3 py-1.5 text-sm transition-colors ${
                      isActive
                        ? "bg-brand-50 font-medium text-brand-700"
                        : "text-gray-500 hover:bg-gray-100 hover:text-gray-900"
                    }`
                  }
                >
                  {label}
                </NavLink>
              ))}
            </nav>
          </div>

          <div className="flex items-center gap-4">
            {user ? (
              <>
                <span className="hidden text-xs text-gray-400 sm:block">{user.email}</span>
                <button
                  onClick={handleLogout}
                  className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-900"
                >
                  <LogOut className="h-4 w-4" />
                  <span className="hidden sm:inline">Logout</span>
                </button>
              </>
            ) : (
              <>
                <Link
                  to="/login"
                  className="text-sm text-gray-500 hover:text-gray-900"
                >
                  Log in
                </Link>
                <Link
                  to="/register"
                  className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700"
                >
                  Sign up
                </Link>
              </>
            )}
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-4xl px-6 py-10">
        <ErrorBoundary>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  );
}
