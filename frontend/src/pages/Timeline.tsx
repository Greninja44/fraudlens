import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { Download, Link2, X } from "lucide-react";
import { api, download } from "@/lib/api";
import { dt, money } from "@/lib/format";
import { cn, qs } from "@/lib/utils";
import type { Account, CaseSummary, Paged, TimelineEvent } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Badge, Card, CardHeader, Field, Input, Select } from "@/components/ui/primitives";
import { Empty, QueryState } from "@/components/ui/states";
import { PageHeader, RiskBadge, SeverityBadge } from "@/components/domain";
import { C, axisProps } from "@/components/charts";
import { TransactionDrawer } from "./Transactions";

const KINDS = [
  { key: "transaction", label: "Transactions", color: C.s1 },
  { key: "detection", label: "Detection events", color: C.s2 },
  { key: "evidence", label: "Evidence", color: C.s3 },
  { key: "note", label: "Notes", color: C.s4 },
  { key: "case", label: "Case activity", color: C.s7 },
] as const;
const LANE = Object.fromEntries(KINDS.map((k, i) => [k.key, KINDS.length - i]));

export default function Timeline() {
  const [sp, setSp] = useSearchParams();
  const caseId = sp.get("case");
  const accountId = sp.get("account");
  const [range, setRange] = useState({ from: "", to: "" });
  const [kinds, setKinds] = useState<string[]>(KINDS.map((k) => k.key));
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const [focus, setFocus] = useState<TimelineEvent | null>(null);
  const [windowH, setWindowH] = useState(48);
  const [txn, setTxn] = useState<number | null>(null);
  const [fit, setFit] = useState<"activity" | "all">("activity");
  const cases = useQuery({ queryKey: ["cases", "all-lite"], queryFn: () => api<Paged<CaseSummary>>("/cases?page_size=200") });
  const acct = useQuery({ queryKey: ["account-lite", accountId], queryFn: () => api<Account>(`/accounts/${accountId}`), enabled: !!accountId });
  const params = { case_id: caseId ?? undefined, account_id: accountId ?? undefined, kinds: kinds.join(","), flagged_only: flaggedOnly,
    date_from: range.from ? `${range.from}T00:00:00Z` : "", date_to: range.to ? `${range.to}T23:59:59Z` : "" };
  const q = useQuery({ queryKey: ["timeline", params], queryFn: () => api<{ events: TimelineEvent[]; truncated: boolean; count: number }>(`/timeline${qs(params)}`), enabled: !!(caseId || accountId) });

  const correlated = useMemo(() => {
    if (!focus || !q.data) return null;
    const t0 = new Date(focus.timestamp).getTime();
    const accts = new Set(focus.account_ids ?? []);
    const ids = new Set<string>();
    for (const e of q.data.events) {
      const close = Math.abs(new Date(e.timestamp).getTime() - t0) <= windowH * 3600_000;
      const shared = (e.account_ids ?? []).some((a) => accts.has(a)) || e.kind === "case" || e.kind === "evidence" || e.kind === "note";
      if (close && (shared || accts.size === 0)) ids.add(e.id);
    }
    return ids;
  }, [focus, q.data, windowH]);

  const allPoints = useMemo(() => (q.data?.events ?? []).map((e) => ({ ...e, x: new Date(e.timestamp).getTime(), y: LANE[e.kind], z: e.amount ? Math.log10(1 + e.amount) : 1 })), [q.data]);
  // Investigation activity (today) can be far from the financial activity (past); by default fit the map to the money.
  const financial = allPoints.filter((p) => p.kind === "transaction" || p.kind === "detection");
  const domain: [number, number] | null = fit === "activity" && financial.length
    ? [Math.min(...financial.map((p) => p.x)), Math.max(...financial.map((p) => p.x))] : null;
  const points = domain ? allPoints.filter((p) => p.x >= domain[0] - 1 && p.x <= domain[1] + 1) : allPoints;
  const hidden = allPoints.length - points.length;
  const byDay = useMemo(() => {
    const m = new Map<string, TimelineEvent[]>();
    for (const e of q.data?.events ?? []) { const d = e.timestamp.slice(0, 10); m.set(d, [...(m.get(d) ?? []), e]); }
    return [...m.entries()];
  }, [q.data]);

  return (
    <>
      <PageHeader title="Investigation Timeline" subtitle="Transactions, detection events, evidence, notes and case activity in one chronological stream."
        actions={(caseId || accountId) && <>
          <Button onClick={() => download(`/timeline/export${qs({ ...params, format: "csv" })}`, "timeline.csv")}><Download className="h-3.5 w-3.5" />CSV</Button>
          <Button onClick={() => download(`/timeline/export${qs({ ...params, format: "json" })}`, "timeline.json")}><Download className="h-3.5 w-3.5" />JSON</Button>
        </>} />
      <Card className="mb-3 p-3">
        <div className="grid grid-cols-2 items-end gap-2 md:grid-cols-4 xl:grid-cols-7">
          <Field label="Case" className="col-span-2">
            <Select value={caseId ?? ""} onChange={(e) => setSp(e.target.value ? { case: e.target.value } : {})}>
              <option value="">{accountId ? `Account: ${acct.data?.holder_name ?? accountId}` : "Select a case…"}</option>
              {cases.data?.items.map((c) => <option key={c.id} value={c.id}>{c.case_number} · {c.title}</option>)}
            </Select>
          </Field>
          <Field label="From"><Input type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} /></Field>
          <Field label="To"><Input type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} /></Field>
          <Field label="Correlation window"><Select value={windowH} onChange={(e) => setWindowH(Number(e.target.value))}>{[6, 24, 48, 168].map((h) => <option key={h} value={h}>± {h < 48 ? `${h} h` : `${h / 24} days`}</option>)}</Select></Field>
          <label className="flex h-8 items-center gap-1.5 text-xs"><input type="checkbox" checked={flaggedOnly} onChange={(e) => setFlaggedOnly(e.target.checked)} />Flagged transactions only</label>
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {KINDS.map((k) => {
            const on = kinds.includes(k.key);
            return (
              <button key={k.key} onClick={() => setKinds(on ? kinds.filter((x) => x !== k.key) : [...kinds, k.key])}
                className={cn("flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs", on ? "border-border-strong text-fg" : "border-border text-subtle line-through")}>
                <span className="h-2 w-2 rounded-sm" style={{ background: k.color }} />{k.label}
              </button>
            );
          })}
        </div>
      </Card>
      {!(caseId || accountId) ? <Card><Empty title="Choose a case or an account">Open a timeline from a case, or from an account profile.</Empty></Card> : (
        <QueryState q={q}>
          {(d) => d.events.length === 0 ? <Card><Empty title="No events in this range" /></Card> : (
            <div className="space-y-3">
              <Card>
                <CardHeader title="Event map" subtitle={`Each lane is an event type; transaction markers are sized by amount. Click a marker to correlate events around it.${hidden ? ` ${hidden} investigation event(s) outside this period are listed below.` : ""}`}
                  actions={<Select className="w-48" value={fit} onChange={(e) => setFit(e.target.value as "activity" | "all")}><option value="activity">Fit to financial activity</option><option value="all">Fit to all events</option></Select>} />
                <div className="h-56 px-2 py-3">
                  <ResponsiveContainer>
                    <ScatterChart margin={{ left: 8, right: 16, top: 4, bottom: 0 }}>
                      <CartesianGrid horizontal={false} />
                      <XAxis type="number" dataKey="x" domain={["dataMin", "dataMax"]} scale="time" {...axisProps} tickFormatter={(v) => new Date(v).toISOString().slice(0, 10)} />
                      <YAxis type="number" dataKey="y" domain={[0.5, KINDS.length + 0.5]} ticks={KINDS.map((_, i) => KINDS.length - i)} {...axisProps} width={110}
                        tickFormatter={(v) => KINDS[KINDS.length - v]?.label ?? ""} />
                      <ZAxis type="number" dataKey="z" range={[30, 160]} />
                      <Tooltip cursor={false} content={({ active, payload }) => {
                        if (!active || !payload?.length) return null;
                        const e = payload[0].payload as TimelineEvent;
                        return <div className="max-w-xs rounded-md border border-border-strong bg-overlay px-3 py-2 text-xs shadow-xl"><div className="text-muted">{dt(e.timestamp)}</div><div className="font-medium">{e.title}</div>{e.amount != null && <div className="num">{money(e.amount)}</div>}</div>;
                      }} />
                      {KINDS.map((k) => (
                        <Scatter key={k.key} name={k.label} data={points.filter((p) => p.kind === k.key)} fill={k.color} stroke="var(--chart-grid)" strokeWidth={1}
                          fillOpacity={correlated ? 0.25 : 0.85} onClick={(p) => setFocus(p as unknown as TimelineEvent)} cursor="pointer" />
                      ))}
                      {correlated && <Scatter data={points.filter((p) => correlated.has(p.id))} fill="rgb(var(--fg))" stroke="rgb(var(--accent))" strokeWidth={2} />}
                    </ScatterChart>
                  </ResponsiveContainer>
                </div>
              </Card>
              {focus && (
                <Card className="flex flex-wrap items-center gap-2 border-accent/40 px-4 py-2 text-xs">
                  <Link2 className="h-3.5 w-3.5 text-accent" />
                  <span>Correlating <b>{correlated?.size ?? 0}</b> events within ± {windowH} h of <b>{focus.title}</b> ({dt(focus.timestamp)}) that share accounts, plus case activity.</span>
                  <Button size="sm" variant="ghost" className="ml-auto" onClick={() => setFocus(null)}><X className="h-3 w-3" />Clear</Button>
                </Card>
              )}
              <Card>
                <CardHeader title={`${d.count} events`} subtitle={d.truncated ? "Truncated - narrow the date range" : "Chronological (UTC)"} />
                <div className="divide-y divide-border">
                  {byDay.map(([day, evs]) => (
                    <div key={day} className="grid grid-cols-[96px_1fr] gap-3 px-4 py-2">
                      <div className="num pt-1 text-xs font-medium text-muted">{day}</div>
                      <div className="space-y-1">
                        {evs.map((e) => {
                          const k = KINDS.find((x) => x.key === e.kind)!;
                          const dim = correlated && !correlated.has(e.id);
                          return (
                            <button key={e.id} onClick={() => (e.kind === "transaction" ? setTxn(Number(e.ref.transaction_id)) : setFocus(e))} onDoubleClick={() => setFocus(e)}
                              className={cn("flex w-full items-start gap-2.5 rounded px-2 py-1 text-left hover:bg-raised", dim && "opacity-30", focus?.id === e.id && "bg-raised ring-1 ring-accent")}>
                              <span className="num w-10 shrink-0 pt-0.5 text-2xs text-subtle">{e.timestamp.slice(11, 16)}</span>
                              <span className="mt-1 h-2 w-2 shrink-0 rounded-sm" style={{ background: k.color }} />
                              <span className="min-w-0 flex-1">
                                <span className="text-[13px]">{e.title}</span>
                                {e.description && <span className="block truncate text-xs text-muted">{e.description}</span>}
                              </span>
                              <span className="flex shrink-0 items-center gap-1">
                                {e.in_case && <Badge tone="accent">in case</Badge>}
                                {e.kind === "transaction" && (e.flagged || (e.risk_score ?? 0) >= 30) && <RiskBadge score={e.risk_score} level={e.risk_level} />}
                                {e.severity && <SeverityBadge severity={e.severity} />}
                                {e.kind === "transaction" && <span className="num w-24 text-right text-xs">{money(e.amount)}</span>}
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
                <p className="border-t border-border px-4 py-2 text-2xs text-subtle">Click a transaction for details; double-click any event (or click a non-transaction event) to correlate events around it.</p>
              </Card>
            </div>
          )}
        </QueryState>
      )}
      {txn && <TransactionDrawer id={txn} onClose={() => setTxn(null)} />}
    </>
  );
}
