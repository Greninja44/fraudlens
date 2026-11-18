import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ArrowDown, ArrowUp, Briefcase, Download, Filter, X } from "lucide-react";
import { api, download } from "@/lib/api";
import { dt, humanize, money } from "@/lib/format";
import { qs } from "@/lib/utils";
import type { Alert, Paged, Transaction } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Badge, Card, Field, Input, KV, Select, Table, Td, Th, Tr } from "@/components/ui/primitives";
import { Empty, QueryState } from "@/components/ui/states";
import { AccountLink, KindBadge, PageHeader, Pagination, Provenance, RiskBadge, SeverityBadge } from "@/components/domain";
import { Drawer } from "@/components/ui/dialog";
import { AddToCaseDialog } from "@/components/AddToCase";
import { useAuth } from "@/lib/auth";

const FILTER_KEYS = ["search", "date_from", "date_to", "min_amount", "max_amount", "min_risk", "flagged", "pattern", "txn_type", "status", "account_id", "import_batch_id"] as const;

export default function Transactions() {
  const [sp, setSp] = useSearchParams();
  const f = Object.fromEntries(FILTER_KEYS.map((k) => [k, sp.get(k) ?? ""])) as Record<(typeof FILTER_KEYS)[number], string>;
  const page = Number(sp.get("page") ?? 1);
  const sort = sp.get("sort") ?? "timestamp";
  const order = sp.get("order") ?? "desc";
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(patch)) (v ? next.set(k, v) : next.delete(k));
    if (!("page" in patch)) next.delete("page");
    setSp(next, { replace: true });
  };
  const [draft, setDraft] = useState(f);
  const [selected, setSelected] = useState<number | null>(null);
  const toIso = (d: string, end = false) => (d ? `${d}T${end ? "23:59:59" : "00:00:00"}Z` : "");
  const params = { ...f, date_from: toIso(f.date_from), date_to: toIso(f.date_to, true), page, page_size: 50, sort, order };
  const q = useQuery({ queryKey: ["transactions", params], queryFn: () => api<Paged<Transaction> & { page: number }>(`/transactions${qs(params)}`), placeholderData: (p) => p });
  const facets = useQuery({ queryKey: ["facets"], queryFn: () => api<{ types: string[]; statuses: string[]; min_date: string; max_date: string }>("/transactions/facets") });
  const patterns = useQuery({ queryKey: ["patterns"], queryFn: () => api<Record<string, string>>("/detection/patterns") });
  const active = FILTER_KEYS.filter((k) => f[k]);

  const sortHead = (key: string, label: string, right = false) => (
    <Th className={right ? "text-right" : ""}>
      <button className="inline-flex items-center gap-1 uppercase hover:text-fg" onClick={() => set({ sort: key, order: sort === key && order === "desc" ? "asc" : "desc" })}>
        {label}{sort === key && (order === "desc" ? <ArrowDown className="h-3 w-3" /> : <ArrowUp className="h-3 w-3" />)}
      </button>
    </Th>
  );

  return (
    <>
      <PageHeader title="Transaction Explorer" subtitle="Search, filter and drill into every imported transaction."
        actions={<Button onClick={() => download(`/transactions/export${qs({ ...params, page: undefined, page_size: undefined, sort: undefined, order: undefined })}`, "transactions.csv")}><Download className="h-3.5 w-3.5" />Export CSV</Button>} />
      <Card className="mb-3 p-3">
        <form className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8" onSubmit={(e) => { e.preventDefault(); set(draft); }}>
          <Field label="Search" className="col-span-2"><Input placeholder="ID, name, account, reference, IP" value={draft.search} onChange={(e) => setDraft({ ...draft, search: e.target.value })} /></Field>
          <Field label="From"><Input type="date" value={draft.date_from} min={facets.data?.min_date?.slice(0, 10)} onChange={(e) => setDraft({ ...draft, date_from: e.target.value })} /></Field>
          <Field label="To"><Input type="date" value={draft.date_to} onChange={(e) => setDraft({ ...draft, date_to: e.target.value })} /></Field>
          <Field label="Min amount"><Input type="number" min={0} value={draft.min_amount} onChange={(e) => setDraft({ ...draft, min_amount: e.target.value })} /></Field>
          <Field label="Max amount"><Input type="number" min={0} value={draft.max_amount} onChange={(e) => setDraft({ ...draft, max_amount: e.target.value })} /></Field>
          <Field label="Min risk"><Input type="number" min={0} max={100} value={draft.min_risk} onChange={(e) => setDraft({ ...draft, min_risk: e.target.value })} /></Field>
          <Field label="Flagged"><Select value={draft.flagged} onChange={(e) => setDraft({ ...draft, flagged: e.target.value })}><option value="">Any</option><option value="true">Flagged</option><option value="false">Not flagged</option></Select></Field>
          <Field label="Indicator"><Select value={draft.pattern} onChange={(e) => setDraft({ ...draft, pattern: e.target.value })}><option value="">Any</option>
            {Object.entries(patterns.data ?? {}).filter(([k]) => k !== "ml_anomaly").map(([k, v]) => <option key={k} value={k}>{v.split(" (")[0]}</option>)}</Select></Field>
          <Field label="Type"><Select value={draft.txn_type} onChange={(e) => setDraft({ ...draft, txn_type: e.target.value })}><option value="">Any</option>{facets.data?.types.map((t) => <option key={t}>{t}</option>)}</Select></Field>
          <Field label="Status"><Select value={draft.status} onChange={(e) => setDraft({ ...draft, status: e.target.value })}><option value="">Any</option>{facets.data?.statuses.map((t) => <option key={t}>{t}</option>)}</Select></Field>
          <div className="col-span-2 flex items-end gap-2 md:col-span-1 xl:col-span-3">
            <Button type="submit" variant="primary"><Filter className="h-3.5 w-3.5" />Apply</Button>
            {active.length > 0 && <Button type="button" variant="ghost" onClick={() => { const empty = Object.fromEntries(FILTER_KEYS.map((k) => [k, ""])) as typeof f; setDraft(empty); set(empty); }}><X className="h-3.5 w-3.5" />Clear</Button>}
          </div>
        </form>
      </Card>
      <Card>
        <QueryState q={q}>
          {(d) => d.items.length === 0 ? <Empty title="No transactions match these filters" /> : (
            <>
              <Table>
                <thead><tr>{sortHead("timestamp", "Date (UTC)")}{sortHead("txn_id", "Txn ID")}<Th>From</Th><Th>To</Th>{sortHead("amount", "Amount", true)}<Th>Type</Th><Th>Status</Th>{sortHead("risk_score", "Risk")}<Th>Indicators</Th></tr></thead>
                <tbody className={q.isFetching ? "opacity-60" : ""}>
                  {d.items.map((t) => (
                    <Tr key={t.id} onClick={() => setSelected(t.id)}>
                      <Td className="num whitespace-nowrap text-xs text-muted">{dt(t.timestamp)}</Td>
                      <Td className="font-mono text-xs">{t.txn_id}</Td>
                      <Td><AccountLink id={t.sender_account_id} number={t.sender_account} holder={t.sender_name} /></Td>
                      <Td><AccountLink id={t.receiver_account_id} number={t.receiver_account} holder={t.receiver_name} /></Td>
                      <Td className="num text-right font-medium">{money(t.amount)}</Td>
                      <Td className="text-xs text-muted">{t.txn_type}</Td>
                      <Td className="text-xs text-muted">{t.status}</Td>
                      <Td>{t.is_flagged || t.risk_score >= 30 ? <RiskBadge score={t.risk_score} level={t.risk_level} /> : <span className="num text-xs text-subtle">{Math.round(t.risk_score)}</span>}</Td>
                      <Td><div className="flex flex-wrap gap-1">{t.patterns.slice(0, 3).map((p) => <Badge key={p}>{patterns.data?.[p]?.split(" (")[0] ?? p}</Badge>)}</div></Td>
                    </Tr>
                  ))}
                </tbody>
              </Table>
              <Pagination page={page} pageSize={50} total={d.total} onPage={(p) => set({ page: String(p) })} />
            </>
          )}
        </QueryState>
      </Card>
      {selected && <TransactionDrawer id={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

type TxnDetail = Transaction & { alerts: Alert[]; cases: { id: number; case_number: string; title: string }[]; ground_truth: { label: boolean; pattern: string | null; note: string } | null };

export function TransactionDrawer({ id, onClose }: { id: number; onClose: () => void }) {
  const q = useQuery({ queryKey: ["transaction", id], queryFn: () => api<TxnDetail>(`/transactions/${id}`) });
  const [adding, setAdding] = useState(false);
  const { can } = useAuth();
  return (
    <Drawer open onOpenChange={(o) => !o && onClose()} title={q.data ? `Transaction ${q.data.txn_id}` : "Transaction"}
      actions={can("cases:write") && q.data && <Button size="sm" onClick={() => setAdding(true)}><Briefcase className="h-3.5 w-3.5" />Add to case</Button>}>
      <QueryState q={q}>
        {(t) => (
          <div className="space-y-5">
            <div className="flex items-baseline justify-between">
              <div className="num text-2xl font-semibold">{money(t.amount)} <span className="text-sm font-normal text-muted">{t.currency}</span></div>
              <RiskBadge score={t.risk_score} level={t.risk_level} />
            </div>
            <KV items={[
              ["Timestamp", `${dt(t.timestamp)} UTC`], ["From", <AccountLink id={t.sender_account_id} number={t.sender_account} holder={t.sender_name} />],
              ["To", <AccountLink id={t.receiver_account_id} number={t.receiver_account} holder={t.receiver_name} />], ["Type", t.txn_type], ["Status", t.status],
              ["Reference", t.reference ?? "—"], ["Bank", t.bank ?? "—"], ["IP address", <span className="font-mono">{t.ip_address ?? "—"}</span>],
              ["Anomaly percentile", t.anomaly_score != null ? `${(t.anomaly_score * 100).toFixed(1)} (model)` : "—"], ["Import batch", t.import_batch_id ?? "—"],
            ]} />
            {t.data_warnings.length > 0 && <div className="rounded border border-warning/30 bg-warning/5 p-2 text-xs text-warning">Data quality: {t.data_warnings.join("; ")}</div>}
            <div>
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-subtle">Why it was scored ({t.alerts.length} indicator{t.alerts.length === 1 ? "" : "s"})</h4>
              {t.alerts.length === 0 ? <p className="text-xs text-muted">No automated indicators involve this transaction.</p> : (
                <div className="space-y-2">
                  {t.alerts.map((a) => (
                    <div key={a.id} className="rounded-md border border-border p-3">
                      <div className="flex flex-wrap items-center gap-2"><KindBadge kind={a.kind} /><SeverityBadge severity={a.severity} /><span className="text-xs text-muted">confidence {(a.confidence * 100).toFixed(0)}%</span></div>
                      <div className="mt-1.5 text-[13px] font-medium">{a.title}</div>
                      <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-muted">{a.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
                    </div>
                  ))}
                  <Provenance kind="automated" />
                </div>
              )}
            </div>
            {t.cases.length > 0 && <div><h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-subtle">In cases</h4>{t.cases.map((c) => <a key={c.id} href={`/cases/${c.id}`} className="block text-xs text-accent">{c.case_number} · {c.title}</a>)}</div>}
            {t.ground_truth && (
              <div className="rounded border border-dashed border-border p-2 text-xs text-subtle">
                Synthetic ground truth: <b>{t.ground_truth.label ? `suspicious (${humanize(t.ground_truth.pattern)})` : "normal"}</b>. {t.ground_truth.note}.
              </div>
            )}
          </div>
        )}
      </QueryState>
      {adding && q.data && <AddToCaseDialog open onClose={() => setAdding(false)} transactionIds={[q.data.id]} accountIds={[q.data.sender_account_id]} />}
    </Drawer>
  );
}
