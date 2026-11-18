import { Activity, Boxes, Briefcase, FileText, FolderLock, GitBranch, LayoutDashboard, ListTree, LogOut, Network, Settings, ShieldAlert, Upload, Users } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { humanize } from "@/lib/format";

const NAV = [
  { group: "Overview", items: [{ to: "/", label: "Dashboard", icon: LayoutDashboard, end: true }] },
  { group: "Analysis", items: [
    { to: "/data", label: "Data Import", icon: Upload },
    { to: "/transactions", label: "Transactions", icon: ListTree },
    { to: "/detection", label: "Detection Center", icon: ShieldAlert },
    { to: "/network", label: "Network Graph", icon: Network },
    { to: "/timeline", label: "Timeline", icon: Activity },
    { to: "/entities", label: "Entities", icon: Boxes },
  ] },
  { group: "Investigation", items: [
    { to: "/cases", label: "Cases", icon: Briefcase },
    { to: "/evidence", label: "Evidence Vault", icon: FolderLock },
    { to: "/reports", label: "Reports", icon: FileText },
  ] },
  { group: "Administration", items: [
    { to: "/users", label: "Users & Access", icon: Users },
    { to: "/settings", label: "Settings", icon: Settings },
  ] },
];

export function AppShell() {
  const { user, logout } = useAuth();
  return (
    <div className="flex h-full">
      <aside className="flex w-56 shrink-0 flex-col border-r border-border bg-surface max-md:hidden">
        <div className="flex h-12 items-center gap-2 border-b border-border px-4">
          <GitBranch className="h-4 w-4 text-accent" />
          <span className="text-sm font-semibold tracking-tight">FraudLens</span>
          <span className="ml-auto rounded border border-border px-1 text-2xs text-subtle">FIU</span>
        </div>
        <nav className="flex-1 overflow-y-auto px-2 py-3">
          {NAV.map((g) => (
            <div key={g.group} className="mb-4">
              <div className="px-2 pb-1 text-2xs font-semibold uppercase tracking-wider text-subtle">{g.group}</div>
              {g.items.map((it) => (
                <NavLink key={it.to} to={it.to} end={"end" in it ? Boolean(it.end) : false}
                  className={({ isActive }) => cn("flex items-center gap-2.5 rounded-md px-2 py-1.5 text-[13px] transition-colors",
                    isActive ? "bg-raised text-fg" : "text-muted hover:bg-raised/60 hover:text-fg")}>
                  <it.icon className="h-4 w-4 shrink-0" />
                  {it.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="border-t border-border p-3">
          <div className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-full bg-raised text-xs font-semibold">{user?.full_name.slice(0, 1)}</div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-medium">{user?.full_name}</div>
              <div className="text-2xs text-subtle">{humanize(user?.role)}</div>
            </div>
            <button onClick={logout} className="rounded p-1.5 text-muted hover:bg-raised hover:text-fg" aria-label="Sign out" title="Sign out"><LogOut className="h-3.5 w-3.5" /></button>
          </div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <MobileNav />
        <main className="min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[1600px] px-4 py-5 md:px-6">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}

function MobileNav() {
  return (
    <div className="flex gap-1 overflow-x-auto border-b border-border bg-surface px-2 py-2 md:hidden">
      {NAV.flatMap((g) => g.items).map((it) => (
        <NavLink key={it.to} to={it.to} end={"end" in it ? Boolean(it.end) : false}
          className={({ isActive }) => cn("flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-xs", isActive ? "bg-raised text-fg" : "text-muted")}>
          <it.icon className="h-3.5 w-3.5" />{it.label}
        </NavLink>
      ))}
    </div>
  );
}
