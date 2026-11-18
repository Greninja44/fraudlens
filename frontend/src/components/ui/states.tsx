import { AlertTriangle, Inbox, Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Button } from "./button";

export function Loading({ label = "Loading…", className }: { label?: string; className?: string }) {
  return (
    <div className={cn("flex items-center justify-center gap-2 py-10 text-xs text-muted", className)} role="status">
      <Loader2 className="h-4 w-4 animate-spin" /> {label}
    </div>
  );
}
export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  const msg = error instanceof Error ? error.message : "Something went wrong";
  return (
    <div className={cn("flex flex-col items-center justify-center gap-2 py-10 text-center", className)} role="alert">
      <AlertTriangle className="h-5 w-5 text-danger" />
      <p className="max-w-md text-xs text-muted">{msg}</p>
      {onRetry && <Button size="sm" onClick={onRetry}>Retry</Button>}
    </div>
  );
}
export function Empty({ title, children, icon, className }: { title: string; children?: ReactNode; icon?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-2 py-12 text-center", className)}>
      <div className="text-subtle">{icon ?? <Inbox className="h-6 w-6" />}</div>
      <p className="text-[13px] font-medium text-fg">{title}</p>
      {children && <div className="max-w-md text-xs text-muted">{children}</div>}
    </div>
  );
}
/** Render a react-query result with consistent loading / error states. */
export function QueryState<T>({ q, children, loadingLabel }: { q: { isLoading: boolean; error: unknown; data: T | undefined; refetch: () => unknown }; children: (d: T) => ReactNode; loadingLabel?: string }) {
  if (q.isLoading) return <Loading label={loadingLabel} />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (q.data === undefined) return null;
  return <>{children(q.data)}</>;
}
