import { ChevronLeft, ChevronRight, ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { humanize } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Button } from "./ui/button";
import { Badge, type Tone } from "./ui/primitives";

const RISK_TONE: Record<string, Tone> = { critical: "danger", high: "orange", medium: "warning", low: "neutral" };

export function RiskBadge({ score, level, className }: { score?: number; level?: string | null; className?: string }) {
  const lv = level ?? (score == null ? "low" : score >= 80 ? "critical" : score >= 60 ? "high" : score >= 30 ? "medium" : "low");
  return (
    <Badge tone={RISK_TONE[lv] ?? "neutral"} className={className} title="Automated risk score (0-100) - an indicator, not a finding">
      {score != null && <span className="num font-semibold">{Math.round(score)}</span>}
      <span className="capitalize">{lv}</span>
    </Badge>
  );
}
export function SeverityBadge({ severity }: { severity: string }) {
  return <Badge tone={RISK_TONE[severity] ?? "neutral"} className="capitalize">{severity}</Badge>;
}

export function RiskBar({ score }: { score: number }) {
  const color = score >= 80 ? "bg-danger" : score >= 60 ? "bg-[#d95926]" : score >= 30 ? "bg-warning" : "bg-subtle";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-raised"><div className={cn("h-full rounded-full", color)} style={{ width: `${Math.max(2, score)}%` }} /></div>
      <span className="num w-6 text-right text-xs text-muted">{Math.round(score)}</span>
    </div>
  );
}

export const KIND_LABEL = { rule: "Rule indicator", model: "Model prediction" } as const;
export function KindBadge({ kind }: { kind: "rule" | "model" }) {
  return kind === "model"
    ? <Badge tone="accent" title="Unsupervised anomaly model - statistical unusualness, not evidence">Model prediction</Badge>
    : <Badge title="Explainable rule-based indicator">Rule indicator</Badge>;
}

const CASE_TONE: Record<string, Tone> = { open: "accent", under_investigation: "warning", pending_review: "orange", escalated: "danger", closed: "neutral" };
export function CaseStatusBadge({ status }: { status: string }) {
  return <Badge tone={CASE_TONE[status] ?? "neutral"}>{humanize(status)}</Badge>;
}
export function PriorityBadge({ priority }: { priority: string }) {
  return <Badge tone={RISK_TONE[priority] ?? "neutral"} className="capitalize">{priority}</Badge>;
}
export function VerificationBadge({ status }: { status: string }) {
  if (status === "verified") return <Badge tone="success"><ShieldCheck className="h-3 w-3" />Verified</Badge>;
  if (status === "failed") return <Badge tone="danger"><ShieldAlert className="h-3 w-3" />Integrity failed</Badge>;
  return <Badge><ShieldQuestion className="h-3 w-3" />Unverified</Badge>;
}

export function AccountLink({ id, number, holder, className }: { id: number; number?: string | null; holder?: string | null; className?: string }) {
  return (
    <Link to={`/accounts/${id}`} className={cn("group inline-flex min-w-0 flex-col leading-tight", className)} onClick={(e) => e.stopPropagation()}>
      {holder && <span className="truncate text-fg group-hover:text-accent">{holder}</span>}
      <span className={cn("truncate font-mono text-2xs", holder ? "text-subtle" : "text-fg group-hover:text-accent")}>{number ?? `#${id}`}</span>
    </Link>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="flex items-center justify-between gap-2 border-t border-border px-3 py-2 text-xs text-muted">
      <span className="num">{total === 0 ? "0 results" : `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)} of ${total.toLocaleString()}`}</span>
      <div className="flex items-center gap-1">
        <Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page"><ChevronLeft className="h-3.5 w-3.5" /></Button>
        <span className="num px-1">{page} / {pages}</span>
        <Button size="sm" variant="ghost" disabled={page >= pages} onClick={() => onPage(page + 1)} aria-label="Next page"><ChevronRight className="h-3.5 w-3.5" /></Button>
      </div>
    </div>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "danger" | "warning" | "success" }) {
  return (
    <div className="rounded-lg border border-border bg-surface px-4 py-3">
      <div className="text-2xs font-medium uppercase tracking-wide text-subtle">{label}</div>
      <div className={cn("num mt-1 text-xl font-semibold tracking-tight", tone === "danger" && "text-danger", tone === "warning" && "text-warning", tone === "success" && "text-success")}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
    </div>
  );
}

/** A labelled callout separating automated output from investigator conclusions. */
export function Provenance({ kind, children }: { kind: "automated" | "model" | "investigator"; children?: ReactNode }) {
  const map = {
    automated: { tone: "border-warning/30 bg-warning/5 text-warning", label: "Automated indicator", text: "Generated by detection rules. Indicates activity that may warrant review - not proof of wrongdoing." },
    model: { tone: "border-accent/30 bg-accent/5 text-accent", label: "Model prediction", text: "Statistical unusualness from an unsupervised model. Not evidence of fraud." },
    investigator: { tone: "border-success/30 bg-success/5 text-success", label: "Investigator conclusion", text: "Recorded by an investigator." },
  }[kind];
  return (
    <div className={cn("rounded-md border px-3 py-2 text-xs", map.tone)}>
      <span className="font-semibold">{map.label}.</span> <span className="text-muted">{children ?? map.text}</span>
    </div>
  );
}
