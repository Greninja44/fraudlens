import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Loading } from "@/components/ui/states";
import { useAuth } from "@/lib/auth";
import Login from "@/pages/Login";

const Dashboard = lazy(() => import("@/pages/Dashboard"));
const DataImport = lazy(() => import("@/pages/DataImport"));
const Transactions = lazy(() => import("@/pages/Transactions"));
const Detection = lazy(() => import("@/pages/Detection"));
const NetworkPage = lazy(() => import("@/pages/Network"));
const Timeline = lazy(() => import("@/pages/Timeline"));
const Entities = lazy(() => import("@/pages/Entities"));
const AccountDetail = lazy(() => import("@/pages/AccountDetail"));
const Cases = lazy(() => import("@/pages/Cases"));
const CaseDetail = lazy(() => import("@/pages/CaseDetail"));
const EvidenceVault = lazy(() => import("@/pages/Evidence"));
const Reports = lazy(() => import("@/pages/Reports"));
const UsersPage = lazy(() => import("@/pages/Users"));
const SettingsPage = lazy(() => import("@/pages/Settings"));

function Protected({ children }: { children: JSX.Element }) {
  const { user, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <Loading label="Restoring session…" className="h-full" />;
  if (!user) return <Navigate to="/login" state={{ from: loc.pathname + loc.search }} replace />;
  return children;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<Protected><AppShell /></Protected>}>
        {[
          ["/", Dashboard], ["/data", DataImport], ["/transactions", Transactions], ["/detection", Detection], ["/network", NetworkPage],
          ["/timeline", Timeline], ["/entities", Entities], ["/accounts/:id", AccountDetail], ["/cases", Cases], ["/cases/:id", CaseDetail],
          ["/evidence", EvidenceVault], ["/reports", Reports], ["/users", UsersPage], ["/settings", SettingsPage],
        ].map(([path, C]) => {
          const Comp = C as React.ComponentType;
          return <Route key={path as string} path={path as string} element={<Suspense fallback={<Loading />}><Comp /></Suspense>} />;
        })}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
