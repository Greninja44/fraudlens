import { useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { GitBranch, Lock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/primitives";
import { useAuth } from "@/lib/auth";

export default function Login() {
  const { user, login } = useAuth();
  const loc = useLocation() as { state?: { from?: string } };
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (user) return <Navigate to={loc.state?.from ?? "/"} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-full items-center justify-center bg-bg px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-2">
          <GitBranch className="h-5 w-5 text-accent" />
          <span className="text-base font-semibold tracking-tight">FraudLens</span>
        </div>
        <div className="rounded-lg border border-border bg-surface p-6">
          <h1 className="text-sm font-semibold">Sign in</h1>
          <p className="mt-1 text-xs text-muted">Financial fraud investigation platform. Access is logged.</p>
          <form onSubmit={submit} className="mt-5 space-y-3">
            <Field label="Email"><Input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus /></Field>
            <Field label="Password"><Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
            {error && <p className="text-xs text-danger" role="alert">{error}</p>}
            <Button type="submit" variant="primary" className="w-full justify-center" loading={busy}><Lock className="h-3.5 w-3.5" />Sign in</Button>
          </form>
        </div>
        <p className="mt-4 text-center text-2xs text-subtle">Development data is synthetic. Do not load real customer data without authorisation.</p>
      </div>
    </div>
  );
}
