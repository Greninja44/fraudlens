import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Activity, ArrowDownLeft, ArrowUpRight, Briefcase, Network } from "lucide-react";
import { api } from "@/lib/api";
import { compactMoney, dt, humanize, money, n } from "@/lib/format";
import { qs } from "@/lib/utils";
import type { Account, Alert } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Badge, Card, CardBody, CardHeader, KV, Select, Table, Td, Th, Tr } from "@/components/ui/primitives";
import { Empty, QueryState } from "@/components/ui/states";
import { AccountLink, CaseStatusBadge, KindBadge, PageHeader, Provenance, RiskBadge, SeverityBadge, Stat } from "@/components/domain";
import { C, ChartTooltip, axisProps } from "@/components/charts";
import { AddToCaseDialog } from "@/components/AddToCase";
import { useAuth } from "@/lib/auth";

type Counterparty = { account_id: number; account_number: string; holder: string | null; count: number; total: number; max_risk: number; risk_score: number };
type AccountFull = Account & {
  entity: { id: number; name: string; entity_type: string; risk_score: number; aliases: string[] } | null;
  sibling_accounts: { id: number; account_number: string; bank: string; risk_score: number }[];
  alerts: Alert[]; top_sources: Counterparty[]; top_destinations: Counterparty[];
  monthly: { month: string; in: number; out: number; count: number; flagged: number }[];
  cases: { id: number; case_number: string; title: string; status: string }[];
};
type Flow = { account_id: number; upstream: FlowRow[]; downstream: FlowRow[]; accounts: Record<string, { account_number: string; holder: string | null; risk_score: number; risk_level: string }> };
type FlowRow = { level: number; from: number; to: number; count: number; total: number; max_risk: number };

export default function AccountDetail() {
  const id = Number(useParams().id);
  const q = useQuery({ queryKey: ["account", id], queryFn: () => api<AccountFull>(`/accounts/${id}`) });
  const [adding, setAdding] = useState(false);
  const { can } = useAuth();
  const nav = useNavigate();
  return (
    <QueryState q={q}>
      {(a) => (
        <>
          <PageHeader title={<span className="flex items-center gap-2">{a.holder_name ?? a.account_number}<RiskBadge score={a.risk_score} level={a.risk_level} /></span>}
            subtitle={<span className="font-mono">{a.account_number} · {a.bank ?? "unknown bank"} · {humanize(a.entity?.entity_type)}</span>}
            actions={<>
              <Link to={`/network?account=${a.id}`}><Button><Network className="h-3.5 w-3.5" />Network</Button></Link>
              <Link to={`/timeline?account=${a.id}`}><Button><Activity className="h-3.5 w-3.5" />Timeline</Button></Link>
              {can("cases:write") && <Button variant="primary" onClick={() => setAdding(true)}><Briefcase className="h-3.5 w-3.5" />Add to case</Button>}
            </>} />
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <Stat label="Money in" value={compactMoney(a.total_in)} />
            <Stat label="Money out" value={compactMoney(a.total_out)} />
            <Stat label="Transactions" value={n(a.txn_count)} sub={`${dt(a.first_seen, false)} → ${dt(a.last_seen, false)}`} />
            <Stat label="Rule alerts" value={n(a.alerts.filter((x) => x.kind === "rule").length)} tone={a.alert_count ? "warning" : undefined} />
            <Stat label="Automated risk" value={Math.round(a.risk_score)} tone={a.risk_score >= 60 ? "danger" : undefined} sub="indicator, not a finding" />
          </div>
          <div className="mt-4 grid gap-4 xl:grid-cols-3">
            <Card className="xl:col-span-2">
              <CardHeader title="Monthly flows" subtitle="Money in vs. out per month" />
              <CardBody>
                <div className="h-56">
                  <ResponsiveContainer>
                    <BarChart data={a.monthly} margin={{ left: 0, right: 8 }} barGap={2}>
                      <CartesianGrid vertical={false} />
                      <XAxis dataKey="month" {...axisProps} />
                      <YAxis {...axisProps} width={56} tickFormatter={(v) => compactMoney(v)} />
                      <Tooltip cursor={{ fill: "rgb(var(--raised))" }} content={<ChartTooltip fmt={(v) => money(v, 0)} />} />
                      <Legend iconType="square" iconSize={8} wrapperStyle={{ fontSize: 11 }} />
                      <Bar dataKey="in" name="Money in" fill={C.s1} radius={[3, 3, 0, 0]} />
                      <Bar dataKey="out" name="Money out" fill={C.s2} radius={[3, 3, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </CardBody>
            </Card>
            <Card>
              <CardHeader title="Holder" />
              <CardBody className="space-y-3">
                {a.entity ? <KV items={[["Entity", <Link className="text-accent" to={`/entities?entity=${a.entity.id}`}>{a.entity.name}</Link>], ["Type", humanize(a.entity.entity_type)],
                  ["Aliases", a.entity.aliases.length ? a.entity.aliases.join(", ") : "none verified"], ["Other accounts", a.sibling_accounts.length ? a.sibling_accounts.map((s) => <div key={s.id}><AccountLink id={s.id} number={s.account_number} /></div>) : "none"],
                  ["Indicators", a.patterns.length ? <div className="flex flex-wrap gap-1">{a.patterns.map((p) => <Badge key={p}>{humanize(p)}</Badge>)}</div> : "none"]]} /> : <p className="text-xs text-muted">No entity.</p>}
                {a.cases.length > 0 && <div><div className="mb-1 text-2xs font-semibold uppercase text-subtle">Cases</div>{a.cases.map((c) => <Link key={c.id} to={`/cases/${c.id}`} className="flex items-center gap-2 py-0.5 text-xs text-accent">{c.case_number} <CaseStatusBadge status={c.status} /></Link>)}</div>}
              </CardBody>
            </Card>
          </div>
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            {([["Where money came from", a.top_sources, ArrowDownLeft], ["Where money went", a.top_destinations, ArrowUpRight]] as const).map(([title, rows, Icon]) => (
              <Card key={title}>
                <CardHeader title={<span className="flex items-center gap-1.5"><Icon className="h-3.5 w-3.5 text-muted" />{title}</span>} subtitle="Top 10 counterparties by value" />
                {rows.length ? <Table><thead><tr><Th>Counterparty</Th><Th className="text-right">Txns</Th><Th className="text-right">Total</Th><Th>Their risk</Th></tr></thead>
                  <tbody>{rows.map((r) => <Tr key={r.account_id} onClick={() => nav(`/accounts/${r.account_id}`)}><Td><AccountLink id={r.account_id} number={r.account_number} holder={r.holder} /></Td><Td className="num text-right">{r.count}</Td><Td className="num text-right">{money(r.total, 0)}</Td><Td><RiskBadge score={r.risk_score} /></Td></Tr>)}</tbody></Table>
                  : <Empty title="None" />}
              </Card>
            ))}
          </div>
          <MoneyFlow id={a.id} />
          <Card className="mt-4">
            <CardHeader title="Detection alerts on this account" />
            {a.alerts.length ? (
              <div className="divide-y divide-border">
                {a.alerts.map((x) => (
                  <Link key={x.id} to={`/detection?alert=${x.id}`} className="block px-4 py-3 hover:bg-raised">
                    <div className="flex flex-wrap items-center gap-2"><KindBadge kind={x.kind} /><SeverityBadge severity={x.severity} /><span className="text-[13px] font-medium">{x.title}</span><span className="text-xs text-muted">{dt(x.last_txn_at, false)}</span></div>
                    <div className="mt-1 text-xs text-muted">{x.reasons.slice(0, 2).join(" · ")}</div>
                  </Link>
                ))}
                <div className="p-3"><Provenance kind="automated" /></div>
              </div>
            ) : <Empty title="No alerts" />}
          </Card>
          {adding && <AddToCaseDialog open onClose={() => setAdding(false)} accountIds={[a.id]} defaultTitle={`Investigation: ${a.holder_name ?? a.account_number}`} />}
        </>
      )}
    </QueryState>
  );
}

function MoneyFlow({ id }: { id: number }) {
  const [depth, setDepth] = useState(2);
  const [minAmount, setMinAmount] = useState(1000);
  const q = useQuery({ queryKey: ["flow", id, depth, minAmount], queryFn: () => api<Flow>(`/accounts/${id}/flow${qs({ depth, min_amount: minAmount })}`) });
  const label = (f: Flow, aid: number) => f.accounts[String(aid)];
  return (
    <Card className="mt-4">
      <CardHeader title="Follow the money" subtitle="Multi-hop trace: upstream sources of funds and downstream destinations, aggregated by counterparty"
        actions={<>
          <Select className="w-28" value={depth} onChange={(e) => setDepth(Number(e.target.value))}>{[1, 2, 3].map((d) => <option key={d} value={d}>{d} hop{d > 1 ? "s" : ""}</option>)}</Select>
          <Select className="w-32" value={minAmount} onChange={(e) => setMinAmount(Number(e.target.value))}>{[0, 1000, 5000, 10000].map((d) => <option key={d} value={d}>≥ {money(d, 0)}</option>)}</Select>
        </>} />
      <QueryState q={q}>
        {(f) => (
          <div className="grid gap-px bg-border lg:grid-cols-2">
            {(["upstream", "downstream"] as const).map((dir) => (
              <div key={dir} className="bg-surface p-4">
                <div className="mb-2 text-2xs font-semibold uppercase tracking-wide text-subtle">{dir === "upstream" ? "Sources (who paid in, and who paid them)" : "Destinations (who received, and where it went next)"}</div>
                {f[dir].length === 0 ? <p className="text-xs text-muted">No flows above the threshold.</p> : (
                  <div className="space-y-1">
                    {f[dir].slice(0, 40).map((r, i) => {
                      const other = label(f, r.to);
                      const via = label(f, r.from);
                      return (
                        <div key={i} className="flex items-center gap-2 text-xs" style={{ paddingLeft: (r.level - 1) * 18 }}>
                          <span className="text-subtle">{dir === "upstream" ? "←" : "→"}</span>
                          <Link to={`/accounts/${r.to}`} className="min-w-0 truncate hover:text-accent">{other?.holder ?? other?.account_number}</Link>
                          {r.level > 1 && <span className="truncate text-2xs text-subtle">via {via?.holder}</span>}
                          <span className="num ml-auto shrink-0">{money(r.total, 0)}</span>
                          <span className="num w-10 shrink-0 text-right text-2xs text-subtle">×{r.count}</span>
                          {r.max_risk >= 50 ? <Badge tone="danger">risk {Math.round(r.max_risk)}</Badge> : <span className="w-[52px]" />}
                        </div>
                      );
                    })}
                    {f[dir].length > 40 && <p className="pt-1 text-2xs text-subtle">+{f[dir].length - 40} more - use the network graph for the full picture.</p>}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </QueryState>
    </Card>
  );
}
