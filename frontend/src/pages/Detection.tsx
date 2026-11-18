import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Briefcase, Info, Network, Play } from "lucide-react";
import { api } from "@/lib/api";
import { dt, humanize, money, n, pct } from "@/lib/format";
import { qs } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import type { Alert, DetectionRun, Paged, PRF } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Badge, Card, CardBody, CardHeader, Field, KV, Select, Table, Td, Th, Tr } from "@/components/ui/primitives";
import { Empty, ErrorState, Loading, QueryState } from "@/components/ui/states";
import { AccountLink, KindBadge, PageHeader, Pagination, Provenance, SeverityBadge, Stat } from "@/components/domain";
import { Drawer } from "@/components/ui/dialog";
import { C, ChartTooltip, axisProps } from "@/components/charts";
import { AddToCaseDialog } from "@/components/AddToCase";

type AlertDetail = Alert & {
  transactions: { id: number; txn_id: string; timestamp: string; amount: number; sender_account_id: number; receiver_account_id: number; sender: string; receiver: string; sender_name: string | null; receiver_name: string | null; risk_score: number; reference: string | null; ip_address: string | null }[];
  involved_accounts: { id: number; account_number: string; holder_name: string | null; risk_score: number; risk_level: string }[];
};

export default function Detection() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [sp, setSp] = useSearchParams();
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api<DetectionRun[]>("/detection/runs"),
    refetchInterval: (q) => (q.state.data?.[0]?.status === "running" ? 1500 : false) });
  const latest = runs.data?.find((r) => r.status === "completed");
  const running = runs.data?.[0]?.status === "running";
  const start = useMutation({ mutationFn: () => api<DetectionRun>("/detection/runs", { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["runs"] }) });
  useEffect(() => {
    if (runs.data?.[0]?.status === "completed") ["alerts", "alert-summary", "dashboard", "transactions"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  }, [runs.data?.[0]?.id, runs.data?.[0]?.status, qc]);
  const alertId = sp.get("alert");

  return (
    <>
      <PageHeader title="Fraud Detection Center"
        subtitle="Explainable rules plus an Isolation Forest anomaly model. Alerts are indicators for review, not findings."
        actions={can("detection:run") && <Button variant="primary" loading={running || start.isPending} onClick={() => start.mutate()}><Play className="h-3.5 w-3.5" />{running ? "Running…" : "Run detection"}</Button>} />
      {start.error && <ErrorState error={start.error} className="py-3" />}
      {runs.data?.[0]?.status === "failed" && <Card className="mb-3 border-danger/40"><CardBody className="text-xs text-danger">Last run failed: {runs.data[0].error}</CardBody></Card>}
      <QueryState q={runs}>
        {() => !latest ? (
          <Card><Empty title="No detection run yet">{running ? <Loading label="Analysing transactions…" /> : "Import transactions, then run detection to score them."}</Empty></Card>
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
              <Stat label="Last run" value={`#${latest.id}`} sub={`${dt(latest.completed_at)} · ${latest.stats.duration_sec}s`} />
              <Stat label="Transactions analysed" value={n(latest.stats.analysed)} sub={latest.stats.excluded_by_status ? `${n(latest.stats.excluded_by_status)} failed/declined excluded` : undefined} />
              <Stat label="Rule indicators" value={n(latest.stats.rule_alerts)} />
              <Stat label="Model predictions" value={n(latest.stats.model_alerts)} sub="statistical outliers" />
              <Stat label="Flagged transactions" value={n(latest.stats.flagged_transactions)} tone="danger" sub={`risk ≥ ${latest.params.flag_threshold}`} />
            </div>
            <div className="grid gap-4 xl:grid-cols-3">
              <AlertsPanel runId={latest.id} onOpen={(id) => setSp({ alert: String(id) })} />
              <div className="space-y-4">
                <MethodCard run={latest} />
                {latest.evaluation && <EvaluationCard run={latest} />}
              </div>
            </div>
          </div>
        )}
      </QueryState>
      {alertId && <AlertDrawer id={Number(alertId)} onClose={() => setSp({})} />}
    </>
  );
}

function AlertsPanel({ runId, onOpen }: { runId: number; onOpen: (id: number) => void }) {
  const [f, setF] = useState({ pattern: "", kind: "rule", severity: "", review_status: "", sort: "score" });
  const [page, setPage] = useState(1);
  const patterns = useQuery({ queryKey: ["patterns"], queryFn: () => api<Record<string, string>>("/detection/patterns") });
  const params = { ...f, run_id: runId, page, page_size: 25 };
  const q = useQuery({ queryKey: ["alerts", params], queryFn: () => api<Paged<Alert>>(`/alerts${qs(params)}`), placeholderData: (p) => p });
  const upd = (k: string, v: string) => { setF({ ...f, [k]: v }); setPage(1); };
  return (
    <Card className="xl:col-span-2">
      <CardHeader title="Alerts" subtitle="Click an alert for its reasons, transactions and review actions" />
      <div className="grid grid-cols-2 gap-2 border-b border-border p-3 md:grid-cols-5">
        <Field label="Source"><Select value={f.kind} onChange={(e) => upd("kind", e.target.value)}><option value="">All</option><option value="rule">Rule indicators</option><option value="model">Model predictions</option></Select></Field>
        <Field label="Pattern"><Select value={f.pattern} onChange={(e) => upd("pattern", e.target.value)}><option value="">All</option>{Object.entries(patterns.data ?? {}).map(([k, v]) => <option key={k} value={k}>{v.split(" (")[0]}</option>)}</Select></Field>
        <Field label="Severity"><Select value={f.severity} onChange={(e) => upd("severity", e.target.value)}><option value="">All</option><option value="critical,high">High + critical</option>{["critical", "high", "medium", "low"].map((s) => <option key={s}>{s}</option>)}</Select></Field>
        <Field label="Review"><Select value={f.review_status} onChange={(e) => upd("review_status", e.target.value)}><option value="">All</option>{["new", "acknowledged", "escalated", "dismissed"].map((s) => <option key={s}>{s}</option>)}</Select></Field>
        <Field label="Sort"><Select value={f.sort} onChange={(e) => upd("sort", e.target.value)}><option value="score">Score</option><option value="recent">Most recent</option><option value="amount">Amount</option></Select></Field>
      </div>
      <QueryState q={q}>
        {(d) => d.items.length === 0 ? <Empty title="No alerts match" /> : (
          <>
            <Table>
              <thead><tr><Th>Indicator</Th><Th>Primary account</Th><Th>Severity</Th><Th className="text-right">Conf.</Th><Th className="text-right">Amount</Th><Th>Period</Th><Th>Review</Th></tr></thead>
              <tbody className={q.isFetching ? "opacity-60" : ""}>
                {d.items.map((a) => (
                  <Tr key={a.id} onClick={() => onOpen(a.id)}>
                    <Td><div className="flex items-center gap-1.5"><span className="font-medium">{a.pattern_label.split(" (")[0]}</span>{a.kind === "model" && <KindBadge kind="model" />}</div><div className="max-w-[320px] truncate text-xs text-muted">{a.title}</div></Td>
                    <Td>{a.account && <AccountLink id={a.account.id} number={a.account.account_number} holder={a.account.holder} />}</Td>
                    <Td><SeverityBadge severity={a.severity} /></Td>
                    <Td className="num text-right text-xs">{pct(a.confidence, 0)}</Td>
                    <Td className="num text-right">{money(a.total_amount, 0)}</Td>
                    <Td className="whitespace-nowrap text-xs text-muted">{dt(a.first_txn_at, false)}{a.last_txn_at && a.last_txn_at.slice(0, 10) !== a.first_txn_at?.slice(0, 10) && ` → ${dt(a.last_txn_at, false)}`}</Td>
                    <Td><Badge tone={a.review_status === "escalated" ? "danger" : a.review_status === "dismissed" ? "neutral" : a.review_status === "acknowledged" ? "accent" : "warning"}>{a.review_status}</Badge></Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
            <Pagination page={page} pageSize={25} total={d.total} onPage={setPage} />
          </>
        )}
      </QueryState>
    </Card>
  );
}

function MethodCard({ run }: { run: DetectionRun }) {
  const data = Object.entries(run.stats.alerts_by_pattern ?? {}).filter(([k]) => k !== "ml_anomaly").map(([k, v]) => ({ pattern: humanize(k), count: v })).sort((a, b) => b.count - a.count);
  return (
    <Card>
      <CardHeader title="How risk is scored" />
      <CardBody className="space-y-3 text-xs text-muted">
        <p>Each rule produces an alert with a severity, a confidence and written reasons. A transaction's risk (0–100) combines all rule alerts involving it, plus a capped anomaly-model contribution (max {Math.round((run.params.ml_weight ?? 0.35) * 100)} points). Transactions at or above <b className="text-fg">{run.params.flag_threshold}</b> are flagged for review.</p>
        <Provenance kind="model">The anomaly model alone can never flag a transaction - it can only raise priority alongside rule evidence.</Provenance>
        <div className="h-44">
          <ResponsiveContainer>
            <BarChart data={data} layout="vertical" margin={{ left: 0, right: 24 }} barCategoryGap={3}>
              <XAxis type="number" hide /><YAxis type="category" dataKey="pattern" {...axisProps} width={130} />
              <Tooltip cursor={{ fill: "rgb(var(--raised))" }} content={<ChartTooltip fmt={(v) => `${v} alerts`} />} />
              <Bar dataKey="count" name="Rule alerts" fill={C.s1} radius={[0, 3, 3, 0]} label={{ position: "right", fill: "rgb(var(--muted))", fontSize: 11 }} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <p>Thresholds are adjustable in <Link to="/settings" className="text-accent">Settings</Link>.</p>
      </CardBody>
    </Card>
  );
}

function PrfRow({ label, m }: { label: string; m: PRF }) {
  return <tr className="border-b border-border/50"><td className="py-1.5 pr-2">{label}</td><td className="num text-right">{pct(m.precision)}</td><td className="num text-right">{pct(m.recall)}</td><td className="num text-right">{m.f1.toFixed(2)}</td><td className="num text-right text-subtle">{m.tp}/{m.fp}/{m.fn}</td></tr>;
}

function EvaluationCard({ run }: { run: DetectionRun }) {
  const e = run.evaluation!;
  return (
    <Card>
      <CardHeader title="Evaluation against ground truth" subtitle="Only available for synthetic datasets that ship labels" />
      <CardBody className="space-y-3 text-xs">
        <div className="flex gap-2 rounded border border-border bg-raised p-2 text-muted"><Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{e.note} The synthetic generator and the detectors were written by the same team, so these figures are a regression check, not a measure of real-world performance.</span></div>
        <table className="w-full">
          <thead className="text-2xs uppercase text-subtle"><tr><th className="pb-1 text-left font-medium">Decision</th><th className="text-right font-medium">Precision</th><th className="text-right font-medium">Recall</th><th className="text-right font-medium">F1</th><th className="text-right font-medium">TP/FP/FN</th></tr></thead>
          <tbody><PrfRow label="Flagged (risk ≥ threshold)" m={e.flagged} /><PrfRow label="Any rule indicator" m={e.any_rule_hit} /><PrfRow label="Model outlier only" m={e.model_outlier_only} /></tbody>
        </table>
        <KV items={[["Labelled", `${n(e.labelled_transactions)} (${n(e.positives)} suspicious)`], ["Model ROC-AUC", e.model_roc_auc?.toFixed(3) ?? "—"], ["Risk-score ROC-AUC", e.risk_roc_auc?.toFixed(3) ?? "—"]]} />
        <div>
          <div className="mb-1 text-2xs font-semibold uppercase tracking-wide text-subtle">Recall by labelled pattern</div>
          {Object.entries(e.per_pattern_recall).map(([k, v]) => (
            <div key={k} className="flex items-center gap-2 py-0.5">
              <span className="w-36 truncate">{humanize(k)}</span>
              <div className="h-1.5 flex-1 rounded-full bg-raised"><div className="h-full rounded-full" style={{ width: `${v.recall * 100}%`, background: C.s1 }} /></div>
              <span className="num w-20 text-right text-muted">{v.flagged}/{v.labelled}</span>
            </div>
          ))}
        </div>
      </CardBody>
    </Card>
  );
}

function AlertDrawer({ id, onClose }: { id: number; onClose: () => void }) {
  const q = useQuery({ queryKey: ["alert", id], queryFn: () => api<AlertDetail>(`/alerts/${id}`) });
  const qc = useQueryClient();
  const { can } = useAuth();
  const [adding, setAdding] = useState(false);
  const review = useMutation({
    mutationFn: (s: string) => api<Alert>(`/alerts/${id}/review`, { method: "POST", json: { review_status: s } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["alert", id] }); qc.invalidateQueries({ queryKey: ["alerts"] }); },
  });
  return (
    <Drawer open onOpenChange={(o) => !o && onClose()} title={q.data ? q.data.pattern_label : "Alert"}
      actions={q.data && can("cases:write") && <Button size="sm" variant="primary" onClick={() => setAdding(true)}><Briefcase className="h-3.5 w-3.5" />Open case</Button>}>
      <QueryState q={q}>
        {(a) => (
          <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-2"><KindBadge kind={a.kind} /><SeverityBadge severity={a.severity} /><Badge>confidence {pct(a.confidence, 0)}</Badge><Badge>score {a.score}</Badge></div>
            <div><div className="text-base font-semibold">{a.title}</div><div className="mt-0.5 text-xs text-muted">{dt(a.first_txn_at)} → {dt(a.last_txn_at)} · {money(a.total_amount)} involved</div></div>
            <div>
              <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-subtle">Reasons</h4>
              <ul className="list-disc space-y-1 pl-4 text-[13px]">{a.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
            </div>
            <Provenance kind={a.kind === "model" ? "model" : "automated"} />
            {Array.isArray(a.details.path_account_ids) && (
              <div><h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-subtle">Flow path</h4>
                <div className="flex flex-wrap items-center gap-1 text-xs">{(a.details.path_account_ids as number[]).map((aid, i) => {
                  const acc = a.involved_accounts.find((x) => x.id === aid);
                  return <span key={i} className="flex items-center gap-1">{i > 0 && <span className="text-subtle">→</span>}<Link className="rounded border border-border px-1.5 py-0.5 hover:border-accent" to={`/accounts/${aid}`}>{acc?.holder_name ?? acc?.account_number}</Link></span>;
                })}</div></div>
            )}
            <div>
              <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-subtle">Transactions ({a.transactions.length})</h4>
              <div className="max-h-72 overflow-auto rounded border border-border">
                <Table><thead><tr><Th>Date</Th><Th>From → To</Th><Th className="text-right">Amount</Th><Th className="text-right">Risk</Th></tr></thead>
                  <tbody>{a.transactions.map((t) => (
                    <tr key={t.id} className="border-b border-border/50"><Td className="num whitespace-nowrap text-xs text-muted">{dt(t.timestamp)}</Td>
                      <Td className="text-xs">{t.sender_name ?? t.sender} → {t.receiver_name ?? t.receiver}</Td><Td className="num text-right">{money(t.amount)}</Td><Td className="num text-right text-xs">{Math.round(t.risk_score)}</Td></tr>
                  ))}</tbody></Table>
              </div>
            </div>
            <div>
              <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-subtle">Involved accounts</h4>
              <div className="flex flex-wrap gap-2">{a.involved_accounts.map((x) => (
                <Link key={x.id} to={`/accounts/${x.id}`} className="rounded border border-border px-2 py-1 text-xs hover:border-accent">{x.holder_name ?? x.account_number} <span className="num text-subtle">· {Math.round(x.risk_score)}</span></Link>
              ))}</div>
              {a.account && <Link to={`/network?account=${a.account.id}`} className="mt-2 inline-flex items-center gap-1 text-xs text-accent"><Network className="h-3 w-3" />Explore network around primary account</Link>}
            </div>
            {can("alerts:review") && (
              <div className="border-t border-border pt-4">
                <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-subtle">Review · currently {a.review_status}</h4>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" onClick={() => review.mutate("acknowledged")} loading={review.isPending && review.variables === "acknowledged"}>Acknowledge</Button>
                  <Button size="sm" variant="danger" onClick={() => review.mutate("escalated")} loading={review.isPending && review.variables === "escalated"}>Escalate</Button>
                  <Button size="sm" variant="ghost" onClick={() => review.mutate("dismissed")} loading={review.isPending && review.variables === "dismissed"}>Dismiss as benign</Button>
                </div>
                {review.error && <ErrorState error={review.error} className="py-2" />}
              </div>
            )}
          </div>
        )}
      </QueryState>
      {adding && q.data && <AddToCaseDialog open onClose={() => setAdding(false)} alertId={q.data.id} defaultTitle={`${q.data.pattern_label.split(" (")[0]} - ${q.data.account?.holder ?? ""}`.trim()} />}
    </Drawer>
  );
}
