import { useQuery } from "@tanstack/react-query";
import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Briefcase, Crosshair, Download, Expand, Image as ImageIcon, Maximize2, Route, Search, X } from "lucide-react";
import { api, download } from "@/lib/api";
import { compactMoney, dt, money, n } from "@/lib/format";
import { qs } from "@/lib/utils";
import type { Account, Paged } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Badge, Card, Field, Input, KV, Select, Tabs } from "@/components/ui/primitives";
import { Empty, ErrorState, Loading } from "@/components/ui/states";
import { AccountLink, PageHeader, RiskBadge } from "@/components/domain";
import { AddToCaseDialog } from "@/components/AddToCase";
import { useAuth } from "@/lib/auth";

interface GNode { id: string; type: string; label: string; account_id?: number; entity_id?: number; holder?: string; bank?: string; entity_type?: string; risk_score: number; risk_level: string | null; patterns?: string[]; alert_count?: number; txn_count?: number; component?: number; total_in?: number; total_out?: number; is_focus?: boolean }
interface GEdge { id: string; source: string; target: string; type: "transfer" | "owns"; count?: number; total?: number; max_risk?: number; flagged?: number; first?: string; last?: string }
interface GraphData { nodes: GNode[]; edges: GEdge[]; truncated: boolean; stats: { accounts: number; entities: number; transfer_edges: number; components: number } }
interface PathRes { found: boolean; reason?: string; length?: number; path?: { account_id: number; account_number: string; holder: string | null; risk_score: number }[]; hops?: { from: number; to: number; direction: string; total: number; count: number }[] }
interface Cycle { alert_id: number; score: number; title: string; amounts: number[]; path: { account_id: number; account_number: string; holder: string | null }[] }
interface Component { component: number; size: number; edges: number; total_value: number; max_risk: number; account_ids: number[]; top_accounts: { account_id: number; holder: string | null; account_number: string; risk_score: number }[] }

function cssColor(name: string) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v.startsWith("#") ? v : `rgb(${v.split(" ").join(",")})`;
}
const riskColor = (s: number) => (s >= 80 ? "#e66767" : s >= 60 ? "#d95926" : s >= 30 ? "#c98500" : "#5b6170");

export function AccountPicker({ label, value, onChange }: { label: string; value: Account | null; onChange: (a: Account | null) => void }) {
  const [text, setText] = useState("");
  const q = useQuery({ queryKey: ["acct-search", text], queryFn: () => api<Paged<Account>>(`/accounts${qs({ search: text, page_size: 8 })}`), enabled: text.length >= 2 });
  if (value) return (
    <Field label={label}><div className="flex h-8 items-center justify-between rounded-md border border-border bg-bg px-2 text-xs">
      <span className="truncate">{value.holder_name} <span className="font-mono text-subtle">{value.account_number}</span></span>
      <button onClick={() => onChange(null)} aria-label="Clear" className="text-muted hover:text-fg"><X className="h-3.5 w-3.5" /></button></div></Field>
  );
  return (
    <Field label={label} className="relative">
      <Input placeholder="Name or account no." value={text} onChange={(e) => setText(e.target.value)} />
      {text.length >= 2 && q.data && (
        <div className="absolute z-20 mt-1 max-h-60 w-full overflow-auto rounded-md border border-border-strong bg-overlay shadow-xl">
          {q.data.items.length === 0 && <div className="px-3 py-2 text-xs text-muted">No matches</div>}
          {q.data.items.map((a) => (
            <button key={a.id} className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-xs hover:bg-raised" onClick={() => { onChange(a); setText(""); }}>
              <span className="truncate">{a.holder_name} <span className="font-mono text-subtle">{a.account_number}</span></span><RiskBadge score={a.risk_score} level={a.risk_level} />
            </button>
          ))}
        </div>
      )}
    </Field>
  );
}

export default function NetworkPage() {
  const [sp, setSp] = useSearchParams();
  const focusId = sp.get("account") ? Number(sp.get("account")) : null;
  const [filters, setFilters] = useState({ depth: "2", date_from: "", date_to: "", min_amount: "", min_risk: "", suspicious_only: !focusId, include_entities: true, limit: "600" });
  const [applied, setApplied] = useState(filters);
  const params = {
    account_id: focusId ?? undefined, depth: applied.depth, min_amount: applied.min_amount, min_risk: applied.min_risk, limit: applied.limit,
    suspicious_only: applied.suspicious_only, include_entities: applied.include_entities ? undefined : "false",
    date_from: applied.date_from ? `${applied.date_from}T00:00:00Z` : "", date_to: applied.date_to ? `${applied.date_to}T23:59:59Z` : "",
  };
  const g = useQuery({ queryKey: ["graph", params], queryFn: () => api<GraphData>(`/graph${qs(params)}`) });
  const focusAcct = useQuery({ queryKey: ["account-lite", focusId], queryFn: () => api<Account>(`/accounts/${focusId}`), enabled: !!focusId });
  const cyRef = useRef<Core | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const [sel, setSel] = useState<{ kind: "node"; data: GNode } | { kind: "edge"; data: GEdge } | null>(null);
  const [panel, setPanel] = useState<"inspect" | "path" | "cycles" | "components">("inspect");
  const [highlight, setHighlight] = useState<{ nodes: string[]; edges: string[] } | null>(null);
  const { can } = useAuth();
  const [adding, setAdding] = useState<number | null>(null);

  const elements = useMemo<ElementDefinition[]>(() => {
    if (!g.data) return [];
    return [...g.data.nodes.map((nd) => ({ data: { ...nd, display: nd.type === "account" ? `${nd.holder ?? nd.label}` : nd.label } })),
      ...g.data.edges.map((e) => ({ data: { ...e, display: e.type === "transfer" ? compactMoney(e.total ?? 0) : "" } }))];
  }, [g.data]);

  useEffect(() => {
    if (!boxRef.current || !g.data) return;
    const fg = cssColor("--fg"), muted = cssColor("--muted"), border = cssColor("--border-strong"), surface = cssColor("--surface"), accent = cssColor("--accent");
    const cy = cytoscape({
      container: boxRef.current, elements, wheelSensitivity: 0.25, minZoom: 0.05, maxZoom: 4,
      style: [
        { selector: "node", style: { label: "data(display)", "font-size": 9, color: muted, "text-valign": "bottom", "text-margin-y": 3, "text-max-width": "110px", "text-wrap": "ellipsis", "border-width": 1.5, "border-color": surface } },
        { selector: "node[type='account']", style: { shape: "round-rectangle", width: 22, height: 16, "background-color": (el: cytoscape.NodeSingular) => riskColor(el.data("risk_score")) } },
        { selector: "node[type='account'][risk_score >= 60]", style: { width: 28, height: 20, "font-weight": "bold", color: fg } },
        { selector: "node[type='individual']", style: { shape: "ellipse", width: 14, height: 14, "background-color": "#7d8590", "font-size": 8 } },
        { selector: "node[type='business']", style: { shape: "diamond", width: 18, height: 18, "background-color": "#9085e9", "font-size": 8 } },
        { selector: "node[type='unknown']", style: { shape: "ellipse", width: 12, height: 12, "background-color": "#555" } },
        { selector: "node[?is_focus]", style: { "border-width": 3, "border-color": accent, width: 34, height: 24 } },
        { selector: "edge", style: { width: 1, "line-color": border, "target-arrow-color": border, "target-arrow-shape": "triangle", "arrow-scale": 0.7, "curve-style": "bezier", opacity: 0.85 } },
        { selector: "edge[type='transfer']", style: { width: (el: cytoscape.EdgeSingular) => Math.min(7, 0.8 + Math.log10(1 + (el.data("total") ?? 0)) * 0.9), label: "data(display)", "font-size": 7, color: muted, "text-background-color": surface, "text-background-opacity": 0.85, "text-background-padding": "1px", "text-rotation": "autorotate" } },
        { selector: "edge[type='owns']", style: { "line-style": "dashed", "target-arrow-shape": "none", width: 0.8, opacity: 0.5 } },
        { selector: "edge[flagged > 0]", style: { "line-color": "#e66767", "target-arrow-color": "#e66767", opacity: 1 } },
        { selector: ":selected", style: { "border-color": accent, "border-width": 3, "line-color": accent, "target-arrow-color": accent } },
        { selector: ".faded", style: { opacity: 0.12 } },
        { selector: ".hl", style: { "border-color": accent, "border-width": 3, "line-color": accent, "target-arrow-color": accent, opacity: 1, "z-index": 10, width: 4 } },
        { selector: "node.hl", style: { width: 30, height: 22, color: fg } },
      ],
      layout: { name: "cose", animate: false, nodeRepulsion: () => 9000, idealEdgeLength: () => 70, numIter: elements.length > 1500 ? 400 : 1000, randomize: true, fit: true, padding: 30 } as cytoscape.LayoutOptions,
    });
    cy.on("tap", "node", (e) => setSel({ kind: "node", data: e.target.data() }));
    cy.on("tap", "edge", (e) => setSel({ kind: "edge", data: e.target.data() }));
    cy.on("tap", (e) => { if (e.target === cy) setSel(null); });
    cyRef.current = cy;
    setHighlight(null);
    return () => { cy.destroy(); cyRef.current = null; };
  }, [elements, g.data]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass("faded hl");
    if (!highlight) return;
    const ids = new Set([...highlight.nodes, ...highlight.edges]);
    cy.elements().forEach((el) => { if (ids.has(el.id())) el.addClass("hl"); else el.addClass("faded"); });
    const hl = cy.elements(".hl");
    if (hl.length) cy.animate({ fit: { eles: hl, padding: 60 }, duration: 300 });
  }, [highlight]);

  const focusOn = (accountId: number) => { setSp({ account: String(accountId) }); setFilters((f) => ({ ...f, suspicious_only: false })); setApplied((f) => ({ ...f, suspicious_only: false })); setSel(null); };
  const exportUrl = (fmt: string) => `/graph/export${qs({ ...params, format: fmt })}`;
  const highlightAccounts = (ids: number[], ordered = false) => {
    const cy = cyRef.current;
    const nodes = ids.map((i) => `a:${i}`);
    const edges: string[] = [];
    if (cy) {
      if (ordered) for (let i = 0; i < ids.length - 1; i++) { edges.push(`t:${ids[i]}-${ids[i + 1]}`, `t:${ids[i + 1]}-${ids[i]}`); }
      else cy.edges("[type='transfer']").forEach((e) => { if (nodes.includes(e.data("source")) && nodes.includes(e.data("target"))) edges.push(e.id()); });
    }
    const missing = cy ? nodes.filter((id) => cy.getElementById(id).empty()).length : 0;
    setHighlight({ nodes, edges });
    return missing;
  };

  return (
    <>
      <PageHeader title="Financial Network" subtitle="Accounts (rounded squares, coloured by automated risk), individuals (circles) and businesses (diamonds). Arrows show money direction; red edges contain flagged transfers."
        actions={<>
          <Button onClick={() => download(exportUrl("json"), "financial_network.json")}><Download className="h-3.5 w-3.5" />JSON</Button>
          <Button onClick={() => download(exportUrl("graphml"), "financial_network.graphml")}><Download className="h-3.5 w-3.5" />GraphML</Button>
          <Button onClick={() => { const cy = cyRef.current; if (!cy) return; const a = document.createElement("a"); a.href = cy.png({ full: true, scale: 2, bg: cssColor("--bg") }); a.download = "financial_network.png"; a.click(); }}><ImageIcon className="h-3.5 w-3.5" />PNG</Button>
        </>} />
      <Card className="mb-3 p-3">
        <div className="grid grid-cols-2 items-end gap-2 md:grid-cols-4 xl:grid-cols-9">
          <div className="col-span-2">
            <AccountPicker label="Focus account" value={focusId && focusAcct.data ? focusAcct.data : null} onChange={(a) => (a ? focusOn(a.id) : setSp({}))} />
          </div>
          <Field label="Depth (hops)"><Select value={filters.depth} disabled={!focusId} onChange={(e) => setFilters({ ...filters, depth: e.target.value })}>{[1, 2, 3, 4].map((d) => <option key={d}>{d}</option>)}</Select></Field>
          <Field label="From"><Input type="date" value={filters.date_from} onChange={(e) => setFilters({ ...filters, date_from: e.target.value })} /></Field>
          <Field label="To"><Input type="date" value={filters.date_to} onChange={(e) => setFilters({ ...filters, date_to: e.target.value })} /></Field>
          <Field label="Min amount"><Input type="number" min={0} value={filters.min_amount} onChange={(e) => setFilters({ ...filters, min_amount: e.target.value })} /></Field>
          <Field label="Min txn risk"><Input type="number" min={0} max={100} value={filters.min_risk} onChange={(e) => setFilters({ ...filters, min_risk: e.target.value })} /></Field>
          <div className="flex flex-col gap-1 text-xs">
            <label className="flex items-center gap-1.5"><input type="checkbox" checked={filters.suspicious_only} onChange={(e) => setFilters({ ...filters, suspicious_only: e.target.checked })} />Flagged only</label>
            <label className="flex items-center gap-1.5"><input type="checkbox" checked={filters.include_entities} onChange={(e) => setFilters({ ...filters, include_entities: e.target.checked })} />Show holders</label>
          </div>
          <Button variant="primary" onClick={() => setApplied(filters)}>Apply</Button>
        </div>
      </Card>
      <div className="grid gap-3 xl:grid-cols-[1fr_360px]">
        <Card className="relative overflow-hidden">
          <div className="flex items-center justify-between border-b border-border px-3 py-2 text-xs text-muted">
            <span className="num">{g.data ? `${n(g.data.stats.accounts)} accounts · ${n(g.data.stats.transfer_edges)} transfer links · ${n(g.data.stats.components)} components` : "…"}{g.data?.truncated && <Badge tone="warning" className="ml-2">truncated - refine filters</Badge>}</span>
            <div className="flex gap-1">
              {highlight && <Button size="sm" variant="ghost" onClick={() => setHighlight(null)}><X className="h-3 w-3" />Clear highlight</Button>}
              <Button size="sm" variant="ghost" onClick={() => cyRef.current?.fit(undefined, 30)}><Maximize2 className="h-3 w-3" />Fit</Button>
            </div>
          </div>
          {g.isLoading && <Loading label="Building network…" className="absolute inset-0 top-10 z-10 bg-surface/70" />}
          {g.error && <ErrorState error={g.error} onRetry={() => g.refetch()} />}
          {g.data && g.data.nodes.length === 0 && <Empty title="No transactions match these filters" className="absolute inset-0 top-10" />}
          <div ref={boxRef} className="h-[640px] w-full" data-testid="graph" />
        </Card>
        <Card className="flex max-h-[690px] flex-col">
          <Tabs value={panel} onChange={setPanel} className="px-2" items={[{ value: "inspect", label: "Inspect" }, { value: "path", label: "Path" }, { value: "cycles", label: "Cycles" }, { value: "components", label: "Clusters" }]} />
          <div className="flex-1 overflow-y-auto p-4">
            {panel === "inspect" && <Inspector sel={sel} onFocus={focusOn} onAdd={(id) => setAdding(id)} canAdd={can("cases:write")} />}
            {panel === "path" && <PathTool onHighlight={(ids) => highlightAccounts(ids, true)} />}
            {panel === "cycles" && <CyclesPanel onShow={(c) => { const ids = c.path.map((p) => p.account_id); const missing = highlightAccounts(ids, true); if (missing) focusOn(ids[0]); }} />}
            {panel === "components" && <ComponentsPanel onShow={(c) => highlightAccounts(c.account_ids)} />}
          </div>
        </Card>
      </div>
      {adding && <AddToCaseDialog open onClose={() => setAdding(null)} accountIds={[adding]} />}
    </>
  );
}

function Inspector({ sel, onFocus, onAdd, canAdd }: { sel: { kind: "node"; data: GNode } | { kind: "edge"; data: GEdge } | null; onFocus: (id: number) => void; onAdd: (id: number) => void; canAdd: boolean }) {
  if (!sel) return <Empty title="Select a node or edge" icon={<Crosshair className="h-6 w-6" />}>Click an account to see where its money came from and went; click an arrow to see the transfers it aggregates.</Empty>;
  if (sel.kind === "edge") {
    const e = sel.data;
    if (e.type === "owns") return <p className="text-xs text-muted">Ownership link: the holder entity owns this account.</p>;
    const [s, t] = [Number(e.source.slice(2)), Number(e.target.slice(2))];
    return (
      <div className="space-y-3">
        <h4 className="text-sm font-semibold">Transfers</h4>
        <KV items={[["From", <Link className="text-accent" to={`/accounts/${s}`}>account #{s}</Link>], ["To", <Link className="text-accent" to={`/accounts/${t}`}>account #{t}</Link>],
          ["Transactions", n(e.count)], ["Total", money(e.total)], ["Flagged", n(e.flagged)], ["Max risk", Math.round(e.max_risk ?? 0)], ["First", dt(e.first)], ["Last", dt(e.last)]]} />
        <Link className="text-xs text-accent" to={`/transactions?account_id=${s}&search=`}>View sender's transactions →</Link>
      </div>
    );
  }
  const d = sel.data;
  if (d.type !== "account") return (
    <div className="space-y-2"><Badge>{d.type}</Badge><h4 className="text-sm font-semibold">{d.label}</h4><p className="text-xs text-muted">Holder entity. Automated risk {Math.round(d.risk_score)}.</p>
      <Link className="text-xs text-accent" to={`/entities?entity=${d.entity_id}`}>Open entity profile →</Link></div>
  );
  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-2"><div><h4 className="text-sm font-semibold">{d.holder}</h4><div className="font-mono text-2xs text-subtle">{d.label} · {d.bank}</div></div><RiskBadge score={d.risk_score} level={d.risk_level} /></div>
      <KV items={[["Money in", money(d.total_in)], ["Money out", money(d.total_out)], ["Transactions", n(d.txn_count)], ["Alerts", n(d.alert_count)], ["Holder type", d.entity_type],
        ["Indicators", d.patterns?.length ? <div className="flex flex-wrap gap-1">{d.patterns.map((p) => <Badge key={p}>{p.replace(/_/g, " ")}</Badge>)}</div> : "none"]]} />
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={() => onFocus(d.account_id!)}><Expand className="h-3.5 w-3.5" />Expand from here</Button>
        <Link to={`/accounts/${d.account_id}`}><Button size="sm"><Search className="h-3.5 w-3.5" />Profile & money flow</Button></Link>
        {canAdd && <Button size="sm" onClick={() => onAdd(d.account_id!)}><Briefcase className="h-3.5 w-3.5" />Add to case</Button>}
      </div>
    </div>
  );
}

function PathTool({ onHighlight }: { onHighlight: (ids: number[]) => number }) {
  const [a, setA] = useState<Account | null>(null);
  const [b, setB] = useState<Account | null>(null);
  const [directed, setDirected] = useState(true);
  const q = useQuery({ queryKey: ["path", a?.id, b?.id, directed], queryFn: () => api<PathRes>(`/graph/path${qs({ source: a!.id, target: b!.id, directed })}`), enabled: false });
  const [missing, setMissing] = useState(0);
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted">Find the shortest chain of transfers between two accounts across all imported data.</p>
      <AccountPicker label="From" value={a} onChange={setA} />
      <AccountPicker label="To" value={b} onChange={setB} />
      <label className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={directed} onChange={(e) => setDirected(e.target.checked)} />Follow money direction only</label>
      <Button variant="primary" disabled={!a || !b || a.id === b.id} loading={q.isFetching} onClick={async () => { const r = await q.refetch(); if (r.data?.found) setMissing(onHighlight(r.data.path!.map((p) => p.account_id))); }}><Route className="h-3.5 w-3.5" />Find path</Button>
      {q.error && <ErrorState error={q.error} className="py-2" />}
      {q.data && (q.data.found ? (
        <div className="space-y-1">
          <div className="text-xs text-muted">{q.data.length} hop(s){missing > 0 && <span className="text-warning"> · {missing} account(s) on the path are outside the current view - widen filters or focus to see them</span>}</div>
          {q.data.path!.map((p, i) => (
            <div key={p.account_id}>
              {i > 0 && <div className="py-0.5 pl-3 text-2xs text-subtle">{q.data!.hops![i - 1].direction === "reverse" ? "←" : "↓"} {money(q.data!.hops![i - 1].total, 0)} in {q.data!.hops![i - 1].count} txn(s)</div>}
              <div className="flex items-center justify-between rounded border border-border px-2 py-1"><AccountLink id={p.account_id} number={p.account_number} holder={p.holder} /><RiskBadge score={p.risk_score} /></div>
            </div>
          ))}
        </div>
      ) : <p className="text-xs text-warning">{q.data.reason}</p>)}
    </div>
  );
}

function CyclesPanel({ onShow }: { onShow: (c: Cycle) => void }) {
  const q = useQuery({ queryKey: ["cycles"], queryFn: () => api<Cycle[]>("/graph/cycles") });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} />;
  if (!q.data?.length) return <Empty title="No circular flows detected" />;
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted">Temporal cycles found by the detection engine: funds leaving an account and returning to it in time order.</p>
      {q.data.map((c) => (
        <button key={c.alert_id} onClick={() => onShow(c)} className="block w-full rounded-md border border-border p-2.5 text-left hover:border-accent">
          <div className="text-xs font-medium">{c.title}</div>
          <div className="mt-1 text-2xs text-muted">{c.path.map((p) => p.holder ?? p.account_number).join(" → ")}</div>
        </button>
      ))}
    </div>
  );
}

function ComponentsPanel({ onShow }: { onShow: (c: Component) => void }) {
  const [minRisk, setMinRisk] = useState(50);
  const q = useQuery({ queryKey: ["components", minRisk], queryFn: () => api<Component[]>(`/graph/components?min_risk=${minRisk}`) });
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted">Connected clusters formed only by transactions with risk ≥ {minRisk}. Candidate suspicious networks.</p>
      <Select value={minRisk} onChange={(e) => setMinRisk(Number(e.target.value))}>{[30, 50, 60, 80].map((v) => <option key={v} value={v}>Risk ≥ {v}</option>)}</Select>
      {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} /> : q.data?.map((c) => (
        <button key={c.component} onClick={() => onShow(c)} className="block w-full rounded-md border border-border p-2.5 text-left hover:border-accent">
          <div className="flex items-center justify-between text-xs"><span className="font-medium">{c.size} accounts · {c.edges} links</span><span className="num text-muted">{compactMoney(c.total_value)}</span></div>
          <div className="mt-1 truncate text-2xs text-muted">{c.top_accounts.map((t) => t.holder).join(", ")}</div>
        </button>
      ))}
    </div>
  );
}
