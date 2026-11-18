import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ArrowRight, ShieldAlert } from "lucide-react";
import { api } from "@/lib/api";
import { compactMoney, dt, money, n } from "@/lib/format";
import type { Account, Alert, DetectionRun } from "@/lib/types";
import { Card, CardBody, CardHeader, Table, Td, Th, Tr } from "@/components/ui/primitives";
import { Empty, QueryState } from "@/components/ui/states";
import { AccountLink, CaseStatusBadge, KindBadge, PageHeader, PriorityBadge, RiskBar, SeverityBadge, Stat } from "@/components/domain";
import { C, ChartTooltip, axisProps } from "@/components/charts";
import { Button } from "@/components/ui/button";

interface DashboardData {
  totals: { transactions: number; transaction_value: number; flagged: number; flagged_value: number; high_risk_accounts: number; accounts: number; active_cases: number };
  last_run: DetectionRun | null;
  activity: { date: string; count: number; flagged: number; value: number }[];
  by_pattern: { pattern: string; label: string; kind: "rule" | "model"; count: number }[];
  recent_alerts: Alert[];
  recent_cases: { id: number; case_number: string; title: string; status: string; priority: string; updated_at: string }[];
  account_risk_distribution: Record<string, number>;
  top_accounts: Account[];
}

function weekly(rows: DashboardData["activity"]) {
  const m = new Map<string, { week: string; count: number; flagged: number; value: number }>();
  for (const r of rows) {
    const d = new Date(r.date + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
    const k = d.toISOString().slice(0, 10);
    const w = m.get(k) ?? { week: k, count: 0, flagged: 0, value: 0 };
    w.count += r.count; w.flagged += r.flagged; w.value += r.value;
    m.set(k, w);
  }
  return [...m.values()].sort((a, b) => a.week.localeCompare(b.week));
}

export default function Dashboard() {
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => api<DashboardData>("/dashboard") });
  const nav = useNavigate();
  return (
    <>
      <PageHeader title="Dashboard" subtitle="Live figures from the transaction database and the latest detection run." />
      <QueryState q={q}>
        {(d) => d.totals.transactions === 0 ? (
          <Card><Empty title="No transactions yet">Import a dataset on the <Link className="text-accent" to="/data">Data Import</Link> page, then run detection.</Empty></Card>
        ) : <DashboardBody d={d} onAlert={(a) => nav(`/detection?alert=${a.id}`)} />}
      </QueryState>
    </>
  );
}

function DashboardBody({ d, onAlert }: { d: DashboardData; onAlert: (a: Alert) => void }) {
  const weeks = useMemo(() => weekly(d.activity), [d.activity]);
  const rules = d.by_pattern.filter((p) => p.kind === "rule").sort((a, b) => b.count - a.count);
  const model = d.by_pattern.find((p) => p.kind === "model");
  const run = d.last_run;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Transactions analysed" value={n(d.totals.transactions)} sub={`${compactMoney(d.totals.transaction_value)} total value`} />
        <Stat label="Flagged for review" value={n(d.totals.flagged)} tone={d.totals.flagged ? "danger" : undefined}
          sub={d.totals.transactions ? `${((d.totals.flagged / d.totals.transactions) * 100).toFixed(2)}% of transactions` : undefined} />
        <Stat label="Flagged value" value={compactMoney(d.totals.flagged_value)} sub="sum of flagged transactions" />
        <Stat label="High-risk accounts" value={n(d.totals.high_risk_accounts)} tone={d.totals.high_risk_accounts ? "warning" : undefined} sub={`of ${n(d.totals.accounts)} accounts (risk ≥ 60)`} />
        <Stat label="Active investigations" value={n(d.totals.active_cases)} sub="cases not closed" />
      </div>
      {!run && (
        <Card className="border-warning/40"><CardBody className="flex items-center justify-between text-xs">
          <span className="text-warning">Detection has not been run on the imported data yet.</span>
          <Link to="/detection"><Button size="sm" variant="primary">Open Detection Center</Button></Link>
        </CardBody></Card>
      )}
      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Transaction activity" subtitle="Transactions per week (all imported data)" />
          <CardBody>
            <div className="h-52">
              <ResponsiveContainer>
                <AreaChart data={weeks} margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="week" {...axisProps} tickFormatter={(v) => v.slice(5)} minTickGap={24} />
                  <YAxis {...axisProps} width={44} />
                  <Tooltip cursor={{ stroke: C.axis, strokeDasharray: "3 3" }} content={<ChartTooltip labelFmt={(l) => `Week of ${l}`} />} />
                  <Area type="monotone" dataKey="count" name="Transactions" stroke={C.s1} strokeWidth={2} fill={C.s1} fillOpacity={0.15} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
            <div className="mt-3 text-2xs font-medium uppercase tracking-wide text-subtle">Flagged transactions per week</div>
            <div className="h-24">
              <ResponsiveContainer>
                <BarChart data={weeks} margin={{ top: 4, right: 8, left: -12, bottom: 0 }} barCategoryGap={2}>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="week" {...axisProps} tickFormatter={(v) => v.slice(5)} minTickGap={24} />
                  <YAxis {...axisProps} width={44} allowDecimals={false} />
                  <Tooltip cursor={{ fill: "rgb(var(--raised))" }} content={<ChartTooltip labelFmt={(l) => `Week of ${l}`} />} />
                  <Bar dataKey="flagged" name="Flagged" fill={C.danger} radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Detection pattern distribution" subtitle={run ? `Rule-based indicators · run #${run.id}` : "No detection run"} />
          <CardBody>
            {rules.length ? (
              <div className="h-64">
                <ResponsiveContainer>
                  <BarChart data={rules} layout="vertical" margin={{ top: 0, right: 28, left: 0, bottom: 0 }} barCategoryGap={4}>
                    <XAxis type="number" hide />
                    <YAxis type="category" dataKey="label" {...axisProps} width={150} tickFormatter={(v: string) => v.split(" (")[0]} />
                    <Tooltip cursor={{ fill: "rgb(var(--raised))" }} content={<ChartTooltip labelFmt={(l) => String(l)} fmt={(v) => `${v} alerts`} />} />
                    <Bar dataKey="count" name="Alerts" fill={C.s1} radius={[0, 3, 3, 0]} label={{ position: "right", fill: "rgb(var(--muted))", fontSize: 11 }} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : <Empty title="No alerts" />}
            {model && <p className="mt-2 text-xs text-muted"><KindBadge kind="model" /> <span className="num">{model.count}</span> transactions ranked as statistical outliers by the anomaly model (not counted above).</p>}
          </CardBody>
        </Card>
      </div>
      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Recent high-severity alerts" subtitle="Automated indicators, most recent activity first"
            actions={<Link to="/detection" className="text-xs text-accent hover:underline">All alerts <ArrowRight className="inline h-3 w-3" /></Link>} />
          {d.recent_alerts.length ? (
            <Table>
              <thead><tr><Th>Indicator</Th><Th>Account</Th><Th>Severity</Th><Th className="text-right">Amount</Th><Th>Last activity</Th></tr></thead>
              <tbody>
                {d.recent_alerts.map((a) => (
                  <Tr key={a.id} onClick={() => onAlert(a)}>
                    <Td><div className="font-medium">{a.pattern_label.split(" (")[0]}</div><div className="max-w-[340px] truncate text-xs text-muted">{a.title}</div></Td>
                    <Td>{a.account && <AccountLink id={a.account.id} number={a.account.account_number} holder={a.account.holder} />}</Td>
                    <Td><SeverityBadge severity={a.severity} /></Td>
                    <Td className="num text-right">{money(a.total_amount, 0)}</Td>
                    <Td className="text-xs text-muted">{dt(a.last_txn_at)}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          ) : <Empty title="No high-severity alerts" icon={<ShieldAlert className="h-6 w-6" />} />}
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader title="Recent investigations" actions={<Link to="/cases" className="text-xs text-accent hover:underline">All cases</Link>} />
            {d.recent_cases.length ? (
              <ul className="divide-y divide-border">
                {d.recent_cases.map((c) => (
                  <li key={c.id}><Link to={`/cases/${c.id}`} className="block px-4 py-2.5 hover:bg-raised">
                    <div className="flex items-center gap-2"><span className="font-mono text-2xs text-subtle">{c.case_number}</span><CaseStatusBadge status={c.status} /><PriorityBadge priority={c.priority} /></div>
                    <div className="mt-0.5 truncate text-[13px]">{c.title}</div>
                  </Link></li>
                ))}
              </ul>
            ) : <Empty title="No cases yet">Open a case from an alert in the Detection Center.</Empty>}
          </Card>
          <Card>
            <CardHeader title="Highest-risk accounts" subtitle="Automated account risk" />
            <ul className="divide-y divide-border">
              {d.top_accounts.map((a) => (
                <li key={a.id} className="flex items-center justify-between gap-3 px-4 py-2">
                  <AccountLink id={a.id} number={a.account_number} holder={a.holder_name} />
                  <RiskBar score={a.risk_score} />
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}
