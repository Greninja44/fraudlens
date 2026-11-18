import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Building2, Check, GitMerge, RefreshCw, User as UserIcon, X } from "lucide-react";
import { api } from "@/lib/api";
import { humanize, money, pct } from "@/lib/format";
import { qs } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import type { Account, Paged } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Badge, Card, Field, Input, KV, Select, Table, Tabs, Td, Th, Tr } from "@/components/ui/primitives";
import { Empty, ErrorState, QueryState } from "@/components/ui/states";
import { AccountLink, PageHeader, Pagination, RiskBadge } from "@/components/domain";
import { Dialog, Drawer } from "@/components/ui/dialog";

interface Entity { id: number; entity_type: string; name: string; attributes: { aliases?: string[]; merged_from?: { name: string }[] }; risk_score: number; merged_into_id: number | null; account_count?: number; link_count?: number }
interface Link { id: number; entity_a_id: number; entity_b_id: number; link_type: string; confidence: number; status: string; note: string | null; signals: { signal: string; weight: number; detail: string }[];
  entity_a: { id: number; name: string; entity_type: string; risk_score: number }; entity_b: { id: number; name: string; entity_type: string; risk_score: number } }

const TypeIcon = ({ t }: { t: string }) => (t === "business" ? <Building2 className="h-3.5 w-3.5 text-[#9085e9]" /> : <UserIcon className="h-3.5 w-3.5 text-muted" />);

export default function Entities() {
  const [sp, setSp] = useSearchParams();
  const [tab, setTab] = useState<"links" | "entities">(sp.get("entity") ? "entities" : "links");
  const { can } = useAuth();
  const qc = useQueryClient();
  const recompute = useMutation({ mutationFn: () => api<Record<string, number>>("/entities/links/recompute", { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["links"] }) });
  const entityId = sp.get("entity");
  return (
    <>
      <PageHeader title="Entity Explorer" subtitle="Each account holder is an entity. The linking engine suggests relationships with explainable signals; only investigators can verify them, and entities are never merged automatically."
        actions={can("entities:merge") && <Button loading={recompute.isPending} onClick={() => recompute.mutate()}><RefreshCw className="h-3.5 w-3.5" />Recompute links</Button>} />
      {recompute.data && <p className="mb-2 text-xs text-muted">Link analysis: {recompute.data.suggested_created} new, {recompute.data.suggested_updated} updated, {recompute.data.stale_removed} stale removed. Investigator decisions were preserved.</p>}
      <Tabs value={tab} onChange={setTab} className="mb-3" items={[{ value: "links", label: "Relationship suggestions" }, { value: "entities", label: "Entities" }]} />
      {tab === "links" ? <LinksTab onOpen={(id) => setSp({ entity: String(id) })} /> : <EntitiesTab onOpen={(id) => setSp({ entity: String(id) })} />}
      {entityId && <EntityDrawer id={Number(entityId)} onClose={() => setSp({})} onOpen={(id) => setSp({ entity: String(id) })} />}
    </>
  );
}

function EntitiesTab({ onOpen }: { onOpen: (id: number) => void }) {
  const [f, setF] = useState({ search: "", entity_type: "", min_risk: "" });
  const [applied, setApplied] = useState(f);
  const [page, setPage] = useState(1);
  const q = useQuery({ queryKey: ["entities", applied, page], queryFn: () => api<Paged<Entity>>(`/entities${qs({ ...applied, page, page_size: 50 })}`), placeholderData: (p) => p });
  return (
    <Card>
      <form className="grid grid-cols-2 items-end gap-2 border-b border-border p-3 md:grid-cols-5" onSubmit={(e) => { e.preventDefault(); setApplied(f); setPage(1); }}>
        <Field label="Name" className="col-span-2"><Input value={f.search} onChange={(e) => setF({ ...f, search: e.target.value })} /></Field>
        <Field label="Type"><Select value={f.entity_type} onChange={(e) => setF({ ...f, entity_type: e.target.value })}><option value="">Any</option><option value="individual">Individual</option><option value="business">Business</option></Select></Field>
        <Field label="Min risk"><Input type="number" value={f.min_risk} onChange={(e) => setF({ ...f, min_risk: e.target.value })} /></Field>
        <Button type="submit" variant="primary">Search</Button>
      </form>
      <QueryState q={q}>
        {(d) => d.items.length === 0 ? <Empty title="No entities" /> : (
          <>
            <Table><thead><tr><Th>Entity</Th><Th>Type</Th><Th className="text-right">Accounts</Th><Th className="text-right">Links</Th><Th>Automated risk</Th></tr></thead>
              <tbody>{d.items.map((e) => (
                <Tr key={e.id} onClick={() => onOpen(e.id)}>
                  <Td><div className="flex items-center gap-2"><TypeIcon t={e.entity_type} /><span className="font-medium">{e.name}</span>{e.attributes.aliases?.length ? <Badge>aka {e.attributes.aliases.join(", ")}</Badge> : null}</div></Td>
                  <Td className="text-xs text-muted">{humanize(e.entity_type)}</Td><Td className="num text-right">{e.account_count}</Td><Td className="num text-right">{e.link_count}</Td>
                  <Td><RiskBadge score={e.risk_score} /></Td>
                </Tr>))}</tbody></Table>
            <Pagination page={page} pageSize={50} total={d.total} onPage={setPage} />
          </>
        )}
      </QueryState>
    </Card>
  );
}

function LinksTab({ onOpen }: { onOpen: (id: number) => void }) {
  const [f, setF] = useState({ status: "suggested", link_type: "", min_confidence: "0" });
  const [page, setPage] = useState(1);
  const q = useQuery({ queryKey: ["links", f, page], queryFn: () => api<Paged<Link>>(`/entities/links${qs({ ...f, page, page_size: 30 })}`), placeholderData: (p) => p });
  return (
    <Card>
      <div className="grid grid-cols-2 items-end gap-2 border-b border-border p-3 md:grid-cols-4">
        <Field label="Status"><Select value={f.status} onChange={(e) => { setF({ ...f, status: e.target.value }); setPage(1); }}><option value="">Any</option>{["suggested", "verified", "rejected"].map((s) => <option key={s}>{s}</option>)}</Select></Field>
        <Field label="Type"><Select value={f.link_type} onChange={(e) => { setF({ ...f, link_type: e.target.value }); setPage(1); }}><option value="">Any</option><option value="possible_duplicate">Possible duplicate (same party)</option><option value="related">Related parties</option></Select></Field>
        <Field label="Min confidence"><Select value={f.min_confidence} onChange={(e) => { setF({ ...f, min_confidence: e.target.value }); setPage(1); }}>{["0", "0.4", "0.6", "0.7"].map((v) => <option key={v} value={v}>{pct(Number(v), 0)}</option>)}</Select></Field>
      </div>
      <QueryState q={q}>
        {(d) => d.items.length === 0 ? <Empty title="No links match">Run "Recompute links" after importing data.</Empty> : (
          <>
            <div className="divide-y divide-border">{d.items.map((l) => <LinkRow key={l.id} l={l} onOpen={onOpen} />)}</div>
            <Pagination page={page} pageSize={30} total={d.total} onPage={setPage} />
          </>
        )}
      </QueryState>
    </Card>
  );
}

function LinkRow({ l, onOpen }: { l: Link; onOpen: (id: number) => void }) {
  const qc = useQueryClient();
  const { can } = useAuth();
  const [merging, setMerging] = useState(false);
  const decide = useMutation({ mutationFn: (status: string) => api(`/entities/links/${l.id}/decision`, { method: "POST", json: { status } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["links"] }); qc.invalidateQueries({ queryKey: ["entity"] }); } });
  return (
    <div className="flex flex-wrap items-start gap-4 px-4 py-3">
      <div className="min-w-[260px] flex-1">
        <div className="flex flex-wrap items-center gap-2 text-[13px]">
          <button className="flex items-center gap-1 font-medium hover:text-accent" onClick={() => onOpen(l.entity_a.id)}><TypeIcon t={l.entity_a.entity_type} />{l.entity_a.name}</button>
          <span className="text-subtle">↔</span>
          <button className="flex items-center gap-1 font-medium hover:text-accent" onClick={() => onOpen(l.entity_b.id)}><TypeIcon t={l.entity_b.entity_type} />{l.entity_b.name}</button>
        </div>
        <ul className="mt-1 space-y-0.5 text-xs text-muted">{l.signals.map((s, i) => <li key={i}><Badge className="mr-1.5">{humanize(s.signal)}</Badge>{s.detail}</li>)}</ul>
      </div>
      <div className="flex items-center gap-2">
        <Badge tone={l.link_type === "possible_duplicate" ? "warning" : "accent"}>{l.link_type === "possible_duplicate" ? "possible duplicate" : "related"}</Badge>
        <div className="w-20"><div className="h-1.5 rounded-full bg-raised"><div className="h-full rounded-full bg-accent" style={{ width: `${l.confidence * 100}%` }} /></div><div className="num mt-0.5 text-2xs text-muted">{pct(l.confidence, 0)} confidence</div></div>
        <Badge tone={l.status === "verified" ? "success" : l.status === "rejected" ? "neutral" : "warning"}>{l.status}</Badge>
      </div>
      {can("entities:verify") && (
        <div className="flex gap-1">
          {l.status !== "verified" && <Button size="sm" variant="success" onClick={() => decide.mutate("verified")} loading={decide.isPending && decide.variables === "verified"}><Check className="h-3 w-3" />Verify</Button>}
          {l.status !== "rejected" && <Button size="sm" variant="ghost" onClick={() => decide.mutate("rejected")} loading={decide.isPending && decide.variables === "rejected"}><X className="h-3 w-3" />Reject</Button>}
          {l.status === "verified" && l.link_type === "possible_duplicate" && can("entities:merge") && <Button size="sm" onClick={() => setMerging(true)}><GitMerge className="h-3 w-3" />Merge…</Button>}
        </div>
      )}
      {merging && <MergeDialog l={l} onClose={() => setMerging(false)} />}
    </div>
  );
}

function MergeDialog({ l, onClose }: { l: Link; onClose: () => void }) {
  const [keep, setKeep] = useState(l.entity_a.id);
  const qc = useQueryClient();
  const m = useMutation({ mutationFn: () => api(`/entities/links/${l.id}/merge`, { method: "POST", json: { keep_entity_id: keep } }),
    onSuccess: () => { qc.invalidateQueries(); onClose(); } });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Merge verified duplicate"
      description="All accounts move to the entity you keep; the other name is recorded as an alias. The merge is audited."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={m.isPending} onClick={() => m.mutate()}>Merge</Button></>}>
      <div className="space-y-2 text-[13px]">
        {[l.entity_a, l.entity_b].map((e) => <label key={e.id} className="flex items-center gap-2"><input type="radio" checked={keep === e.id} onChange={() => setKeep(e.id)} />Keep <b>{e.name}</b></label>)}
      </div>
      {m.error && <ErrorState error={m.error} className="py-2" />}
    </Dialog>
  );
}

function EntityDrawer({ id, onClose, onOpen }: { id: number; onClose: () => void; onOpen: (id: number) => void }) {
  const q = useQuery({ queryKey: ["entity", id], queryFn: () => api<Entity & { accounts: Account[]; links: Link[] }>(`/entities/${id}`) });
  return (
    <Drawer open onOpenChange={(o) => !o && onClose()} title={q.data?.name ?? "Entity"}>
      <QueryState q={q}>
        {(e) => (
          <div className="space-y-5">
            <KV items={[["Type", humanize(e.entity_type)], ["Automated risk", <RiskBadge score={e.risk_score} />], ["Aliases (verified)", e.attributes.aliases?.join(", ") || "none"],
              ["Merged into", e.merged_into_id ? <button className="text-accent" onClick={() => onOpen(e.merged_into_id!)}>entity #{e.merged_into_id}</button> : "—"]]} />
            <div><h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-subtle">Accounts</h4>
              <Table><thead><tr><Th>Account</Th><Th>Bank</Th><Th className="text-right">In / out</Th><Th>Risk</Th></tr></thead>
                <tbody>{e.accounts.map((a) => <tr key={a.id} className="border-b border-border/50"><Td><AccountLink id={a.id} number={a.account_number} /></Td><Td className="text-xs">{a.bank}</Td><Td className="num text-right text-xs">{money(a.total_in, 0)} / {money(a.total_out, 0)}</Td><Td><RiskBadge score={a.risk_score} level={a.risk_level} /></Td></tr>)}</tbody></Table>
            </div>
            <div><h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-subtle">Relationships</h4>
              {e.links.length ? <div className="divide-y divide-border rounded border border-border">{e.links.map((l) => <LinkRow key={l.id} l={l} onOpen={onOpen} />)}</div> : <p className="text-xs text-muted">No suggested relationships.</p>}
            </div>
          </div>
        )}
      </QueryState>
    </Drawer>
  );
}
