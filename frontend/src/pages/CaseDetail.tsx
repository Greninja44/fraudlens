import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Activity, CheckCircle2, Circle, Database, Download, FileText, FileUp, Network, Pencil, Plus, Trash2, UserPlus } from "lucide-react";
import { api, download } from "@/lib/api";
import { dt, humanize, money } from "@/lib/format";
import { useAuth } from "@/lib/auth";
import type { Account, CaseDetail as CaseT, User } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Badge, Card, CardBody, CardHeader, Field, Input, KV, Select, Table, Tabs, Td, Textarea, Th, Tr } from "@/components/ui/primitives";
import { Empty, ErrorState, QueryState } from "@/components/ui/states";
import { AccountLink, CaseStatusBadge, PageHeader, PriorityBadge, Provenance, RiskBadge } from "@/components/domain";
import { Dialog } from "@/components/ui/dialog";
import { EvidenceDrawer, EvidenceTable, EvidenceUploadDialog } from "@/components/EvidenceViews";
import { AccountPicker } from "./Network";
import { TransactionDrawer } from "./Transactions";

type Tab = "overview" | "accounts" | "transactions" | "evidence" | "notes" | "findings" | "activity" | "reports";

export default function CaseDetail() {
  const id = Number(useParams().id);
  const q = useQuery({ queryKey: ["case", id], queryFn: () => api<CaseT>(`/cases/${id}`) });
  const [tab, setTab] = useState<Tab>("overview");
  return (
    <QueryState q={q}>
      {(c) => (
        <>
          <PageHeader
            title={<span className="flex flex-wrap items-center gap-2"><span className="font-mono text-sm text-subtle">{c.case_number}</span>{c.title}</span>}
            subtitle={<span className="flex flex-wrap items-center gap-2"><CaseStatusBadge status={c.status} /><PriorityBadge priority={c.priority} />
              <span>Opened {dt(c.created_at, false)} by {c.created_by} · assigned to {c.assignees.map((a) => a.full_name).join(", ") || "nobody"}</span></span>}
            actions={<>
              <Link to={`/timeline?case=${c.id}`}><Button><Activity className="h-3.5 w-3.5" />Timeline</Button></Link>
              {c.accounts[0] && <Link to={`/network?account=${c.accounts[0].id}`}><Button><Network className="h-3.5 w-3.5" />Network</Button></Link>}
              <StatusControl c={c} />
            </>} />
          <Tabs value={tab} onChange={setTab} className="mb-4" items={[
            { value: "overview", label: "Overview" }, { value: "accounts", label: `Accounts (${c.counts.accounts})` }, { value: "transactions", label: `Transactions (${c.counts.transactions})` },
            { value: "evidence", label: `Evidence (${c.counts.evidence})` }, { value: "notes", label: `Notes (${c.counts.notes})` }, { value: "findings", label: `Findings (${c.counts.findings})` },
            { value: "activity", label: "Activity" }, { value: "reports", label: `Reports (${c.counts.reports})` }]} />
          {tab === "overview" && <Overview c={c} onTab={setTab} />}
          {tab === "accounts" && <Accounts c={c} />}
          {tab === "transactions" && <Transactions c={c} />}
          {tab === "evidence" && <EvidenceTab c={c} />}
          {tab === "notes" && <Notes c={c} />}
          {tab === "findings" && <Findings c={c} />}
          {tab === "activity" && <Card><div className="divide-y divide-border">{c.events.map((e) => (
            <div key={e.id} className="flex gap-3 px-4 py-2 text-xs"><span className="num w-32 shrink-0 text-muted">{dt(e.created_at)}</span><Badge>{humanize(e.event_type)}</Badge><span className="flex-1">{e.description}</span><span className="text-muted">{e.actor}</span></div>))}</div></Card>}
          {tab === "reports" && <Reports c={c} />}
        </>
      )}
    </QueryState>
  );
}

function useCaseMutation<T>(caseId: number, fn: (v: T) => Promise<unknown>, after?: () => void) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: fn, onSuccess: () => { qc.invalidateQueries({ queryKey: ["case", caseId] }); qc.invalidateQueries({ queryKey: ["cases"] }); after?.(); } });
}

function StatusControl({ c }: { c: CaseT }) {
  const { can, user } = useAuth();
  const [target, setTarget] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const m = useCaseMutation<void>(c.id, () => api(`/cases/${c.id}/status`, { method: "POST", json: { status: target, comment } }), () => { setTarget(null); setComment(""); });
  const options = c.allowed_transitions.filter((s) => {
    const reviewing = c.status === "pending_review" || c.status === "closed" || s === "closed";
    return reviewing ? can("cases:review") : can("cases:write") && (user?.role === "admin" || c.assignees.some((a) => a.id === user?.id) || c.created_by === user?.full_name);
  });
  if (!options.length) return null;
  return (
    <>
      <Select className="w-48" value="" onChange={(e) => e.target.value && setTarget(e.target.value)} aria-label="Change status">
        <option value="">Change status…</option>
        {options.map((s) => <option key={s} value={s}>{s === "closed" ? "Close case" : s === "under_investigation" && c.status === "closed" ? "Reopen" : `Move to ${humanize(s).toLowerCase()}`}</option>)}
      </Select>
      {target && (
        <Dialog open onOpenChange={(o) => !o && setTarget(null)} title={`${humanize(c.status)} → ${humanize(target)}`}
          footer={<><Button onClick={() => setTarget(null)}>Cancel</Button><Button variant="primary" loading={m.isPending} onClick={() => m.mutate()}>Confirm</Button></>}>
          <Field label="Comment" hint="recorded in the case activity"><Textarea value={comment} onChange={(e) => setComment(e.target.value)} /></Field>
          {m.error && <ErrorState error={m.error} className="py-2" />}
        </Dialog>
      )}
    </>
  );
}

function Overview({ c, onTab }: { c: CaseT; onTab: (t: Tab) => void }) {
  const { can } = useAuth();
  const [edit, setEdit] = useState(false);
  const [form, setForm] = useState({ title: c.title, summary: c.summary, objectives: c.objectives, priority: c.priority });
  const save = useCaseMutation<void>(c.id, () => api(`/cases/${c.id}`, { method: "PATCH", json: form }), () => setEdit(false));
  const writable = can("cases:write") && c.status !== "closed";
  const flagged = c.transactions.filter((t) => t.is_flagged);
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <Card className="xl:col-span-2">
        <CardHeader title="Case brief" actions={writable && !edit && <Button size="sm" variant="ghost" onClick={() => setEdit(true)}><Pencil className="h-3.5 w-3.5" />Edit</Button>} />
        <CardBody>
          {edit ? (
            <div className="space-y-3">
              <Field label="Title"><Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
              <Field label="Summary"><Textarea rows={4} value={form.summary} onChange={(e) => setForm({ ...form, summary: e.target.value })} /></Field>
              <Field label="Objectives"><Textarea rows={3} value={form.objectives} onChange={(e) => setForm({ ...form, objectives: e.target.value })} /></Field>
              <Field label="Priority"><Select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>{["low", "medium", "high", "critical"].map((p) => <option key={p}>{p}</option>)}</Select></Field>
              <div className="flex gap-2"><Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}>Save</Button><Button onClick={() => setEdit(false)}>Cancel</Button></div>
              {save.error && <ErrorState error={save.error} className="py-2" />}
            </div>
          ) : (
            <div className="space-y-4 text-[13px]">
              <div><div className="mb-1 text-2xs font-semibold uppercase tracking-wide text-subtle">Summary</div><p className="whitespace-pre-wrap">{c.summary || <span className="text-muted">No summary yet.</span>}</p></div>
              <div><div className="mb-1 text-2xs font-semibold uppercase tracking-wide text-subtle">Objectives</div><p className="whitespace-pre-wrap">{c.objectives || <span className="text-muted">No objectives recorded.</span>}</p></div>
            </div>
          )}
        </CardBody>
      </Card>
      <div className="space-y-4">
        <Card>
          <CardHeader title="Progress" subtitle={`${c.progress.percent}% of investigation steps recorded`} />
          <CardBody className="space-y-1.5">
            {c.progress.steps.map((s) => <div key={s.label} className="flex items-center gap-2 text-xs">{s.done ? <CheckCircle2 className="h-3.5 w-3.5 text-success" /> : <Circle className="h-3.5 w-3.5 text-subtle" />}<span className={s.done ? "" : "text-muted"}>{s.label}</span></div>)}
          </CardBody>
        </Card>
        <Assignees c={c} />
        <Card>
          <CardHeader title="At a glance" />
          <CardBody><KV items={[["Accounts", `${c.accounts.length} (${c.accounts.filter((a) => a.case_role === "subject").length} subject)`], ["Transactions", `${c.transactions.length} (${flagged.length} flagged)`],
            ["Value in case", money(c.transactions.reduce((s, t) => s + t.amount, 0), 0)], ["Evidence items", <button className="text-accent" onClick={() => onTab("evidence")}>{c.evidence.length}</button>],
            ["Investigator findings", <button className="text-accent" onClick={() => onTab("findings")}>{c.findings.length}</button>]]} /></CardBody>
        </Card>
      </div>
    </div>
  );
}

function Assignees({ c }: { c: CaseT }) {
  const { can } = useAuth();
  const [open, setOpen] = useState(false);
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<User[]>("/users"), enabled: open });
  const [sel, setSel] = useState<number[]>(c.assignees.map((a) => a.id));
  const m = useCaseMutation<void>(c.id, () => api(`/cases/${c.id}/assignees`, { method: "PUT", json: { user_ids: sel } }), () => setOpen(false));
  return (
    <Card>
      <CardHeader title="Assigned investigators" actions={can("cases:write") && c.status !== "closed" && <Button size="sm" variant="ghost" onClick={() => setOpen(true)}><UserPlus className="h-3.5 w-3.5" />Assign</Button>} />
      <CardBody className="space-y-1 text-xs">{c.assignees.map((a) => <div key={a.id}>{a.full_name} <span className="text-subtle">· {a.role}</span></div>)}</CardBody>
      {open && (
        <Dialog open onOpenChange={(o) => !o && setOpen(false)} title="Assign investigators"
          footer={<><Button onClick={() => setOpen(false)}>Cancel</Button><Button variant="primary" disabled={!sel.length} loading={m.isPending} onClick={() => m.mutate()}>Save</Button></>}>
          <div className="space-y-1.5">{users.data?.filter((u) => u.role !== "reviewer" && u.is_active).map((u) => (
            <label key={u.id} className="flex items-center gap-2 text-[13px]"><input type="checkbox" checked={sel.includes(u.id)} onChange={(e) => setSel(e.target.checked ? [...sel, u.id] : sel.filter((x) => x !== u.id))} />{u.full_name} <span className="text-xs text-subtle">{u.role}</span></label>))}</div>
          {m.error && <ErrorState error={m.error} className="py-2" />}
        </Dialog>
      )}
    </Card>
  );
}

function Accounts({ c }: { c: CaseT }) {
  const { can } = useAuth();
  const writable = can("cases:write") && c.status !== "closed";
  const [pick, setPick] = useState<Account | null>(null);
  const [role, setRole] = useState("subject");
  const add = useCaseMutation<void>(c.id, () => api(`/cases/${c.id}/accounts`, { method: "POST", json: { account_ids: [pick!.id], role } }), () => setPick(null));
  const remove = useCaseMutation<number>(c.id, (aid) => api(`/cases/${c.id}/accounts/${aid}`, { method: "DELETE" }));
  return (
    <Card>
      {writable && (
        <div className="grid grid-cols-1 items-end gap-2 border-b border-border p-3 md:grid-cols-[1fr_160px_auto]">
          <AccountPicker label="Add account" value={pick} onChange={setPick} />
          <Field label="Role"><Select value={role} onChange={(e) => setRole(e.target.value)}><option value="subject">Subject</option><option value="counterparty">Counterparty</option><option value="related">Related</option></Select></Field>
          <Button variant="primary" disabled={!pick} loading={add.isPending} onClick={() => add.mutate()}><Plus className="h-3.5 w-3.5" />Add</Button>
        </div>
      )}
      {add.error && <ErrorState error={add.error} className="py-2" />}
      {c.accounts.length === 0 ? <Empty title="No accounts in this case" /> : (
        <Table><thead><tr><Th>Account</Th><Th>Bank</Th><Th>Role</Th><Th className="text-right">In</Th><Th className="text-right">Out</Th><Th>Automated risk</Th><Th>Indicators</Th><Th /></tr></thead>
          <tbody>{c.accounts.map((a) => (
            <tr key={a.id} className="border-b border-border/60">
              <Td><AccountLink id={a.id} number={a.account_number} holder={a.holder_name} /></Td><Td className="text-xs">{a.bank}</Td><Td><Badge>{a.case_role}</Badge></Td>
              <Td className="num text-right">{money(a.total_in, 0)}</Td><Td className="num text-right">{money(a.total_out, 0)}</Td><Td><RiskBadge score={a.risk_score} level={a.risk_level} /></Td>
              <Td><div className="flex flex-wrap gap-1">{a.patterns.map((p) => <Badge key={p}>{humanize(p)}</Badge>)}</div></Td>
              <Td>{writable && <Button size="icon" variant="ghost" aria-label="Remove" onClick={() => remove.mutate(a.id)}><Trash2 className="h-3.5 w-3.5" /></Button>}</Td>
            </tr>))}</tbody></Table>
      )}
    </Card>
  );
}

function Transactions({ c }: { c: CaseT }) {
  const { can } = useAuth();
  const writable = can("cases:write") && c.status !== "closed";
  const [txn, setTxn] = useState<number | null>(null);
  const remove = useCaseMutation<number>(c.id, (tid) => api(`/cases/${c.id}/transactions/${tid}`, { method: "DELETE" }));
  return (
    <Card>
      <CardHeader title="Transactions associated with the case" subtitle="Add more from the Transaction Explorer, alerts or the network graph."
        actions={<Button size="sm" onClick={() => download(`/cases/${c.id}/export?format=csv`)}><Download className="h-3.5 w-3.5" />CSV</Button>} />
      {c.transactions.length === 0 ? <Empty title="No transactions yet" /> : (
        <Table><thead><tr><Th>Date (UTC)</Th><Th>Txn ID</Th><Th>From</Th><Th>To</Th><Th className="text-right">Amount</Th><Th>Automated risk</Th><Th /></tr></thead>
          <tbody>{c.transactions.map((t) => (
            <Tr key={t.id} onClick={() => setTxn(t.id)}>
              <Td className="num whitespace-nowrap text-xs text-muted">{dt(t.timestamp)}</Td><Td className="font-mono text-xs">{t.txn_id}</Td>
              <Td><AccountLink id={t.sender_account_id} number={t.sender_account} holder={t.sender_name} /></Td><Td><AccountLink id={t.receiver_account_id} number={t.receiver_account} holder={t.receiver_name} /></Td>
              <Td className="num text-right">{money(t.amount)}</Td><Td><RiskBadge score={t.risk_score} level={t.risk_level} /></Td>
              <Td>{writable && <Button size="icon" variant="ghost" aria-label="Remove" onClick={(e) => { e.stopPropagation(); remove.mutate(t.id); }}><Trash2 className="h-3.5 w-3.5" /></Button>}</Td>
            </Tr>))}</tbody></Table>
      )}
      {txn && <TransactionDrawer id={txn} onClose={() => setTxn(null)} />}
    </Card>
  );
}

function EvidenceTab({ c }: { c: CaseT }) {
  const { can } = useAuth();
  const [up, setUp] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const extract = useCaseMutation<void>(c.id, () => api(`/cases/${c.id}/evidence/transaction-extract`, { method: "POST" }));
  const writable = can("evidence:upload") && c.status !== "closed";
  return (
    <Card>
      <CardHeader title="Evidence" subtitle="Originals are stored read-only with SHA-256 and a hash-chained custody log."
        actions={writable && <>
          <Button size="sm" disabled={!c.transactions.length} loading={extract.isPending} onClick={() => extract.mutate()} title="Preserve the case's transaction records as a CSV evidence item"><Database className="h-3.5 w-3.5" />Preserve transaction records</Button>
          <Button size="sm" variant="primary" onClick={() => setUp(true)}><FileUp className="h-3.5 w-3.5" />Attach file</Button>
        </>} />
      {extract.error && <ErrorState error={extract.error} className="py-2" />}
      <EvidenceTable items={c.evidence} onOpen={setOpen} />
      {up && <EvidenceUploadDialog caseId={c.id} open onClose={() => setUp(false)} />}
      {open && <EvidenceDrawer id={open} onClose={() => setOpen(null)} />}
    </Card>
  );
}

function Notes({ c }: { c: CaseT }) {
  const [body, setBody] = useState("");
  const add = useCaseMutation<void>(c.id, () => api(`/cases/${c.id}/notes`, { method: "POST", json: { body } }), () => setBody(""));
  return (
    <Card>
      <CardHeader title="Investigator observations" subtitle="Notes are timestamped, attributed and included in the report." />
      {c.status !== "closed" && (
        <div className="border-b border-border p-3">
          <Textarea value={body} onChange={(e) => setBody(e.target.value)} placeholder="Record an observation…" />
          <div className="mt-2 flex justify-end"><Button variant="primary" size="sm" disabled={!body.trim()} loading={add.isPending} onClick={() => add.mutate()}>Add note</Button></div>
          {add.error && <ErrorState error={add.error} className="py-2" />}
        </div>
      )}
      {c.notes.length === 0 ? <Empty title="No notes" /> : <div className="divide-y divide-border">{c.notes.map((n) => (
        <div key={n.id} className="px-4 py-3"><div className="text-xs text-muted"><b className="text-fg">{n.author}</b> · {dt(n.created_at)} UTC</div><p className="mt-1 whitespace-pre-wrap text-[13px]">{n.body}</p></div>))}</div>}
    </Card>
  );
}

function Findings({ c }: { c: CaseT }) {
  const { can } = useAuth();
  const writable = can("cases:write") && c.status !== "closed";
  const [form, setForm] = useState({ title: "", description: "", status: "draft" });
  const add = useCaseMutation<void>(c.id, () => api(`/cases/${c.id}/findings`, { method: "POST", json: form }), () => setForm({ title: "", description: "", status: "draft" }));
  const upd = useCaseMutation<{ id: number; status: string }>(c.id, (v) => api(`/cases/${c.id}/findings/${v.id}`, { method: "PATCH", json: { status: v.status } }));
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <Card className="xl:col-span-2">
        <CardHeader title="Investigator findings" subtitle="Conclusions recorded by people. Automated indicators are never shown here." />
        <div className="p-3"><Provenance kind="investigator">Findings are the only place where a conclusion is recorded. Mark a finding <b>confirmed</b> only when it is supported by evidence.</Provenance></div>
        {c.findings.length === 0 ? <Empty title="No findings recorded" /> : <div className="divide-y divide-border">{c.findings.map((f) => (
          <div key={f.id} className="px-4 py-3">
            <div className="flex flex-wrap items-center gap-2"><span className="text-[13px] font-semibold">{f.title}</span>
              <Badge tone={f.status === "confirmed" ? "success" : f.status === "refuted" ? "neutral" : "warning"}>{f.status}</Badge>
              <span className="text-xs text-muted">{f.author} · {dt(f.created_at, false)}</span>
              {writable && <Select className="ml-auto h-7 w-32 text-xs" value={f.status} onChange={(e) => upd.mutate({ id: f.id, status: e.target.value })}>{["draft", "confirmed", "refuted"].map((s) => <option key={s}>{s}</option>)}</Select>}
            </div>
            <p className="mt-1 whitespace-pre-wrap text-[13px]">{f.description}</p>
          </div>))}</div>}
      </Card>
      {writable && (
        <Card>
          <CardHeader title="Record a finding" />
          <CardBody className="space-y-3">
            <Field label="Title"><Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
            <Field label="Finding and supporting basis"><Textarea rows={6} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
            <Field label="Status"><Select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}><option value="draft">Draft</option><option value="confirmed">Confirmed</option><option value="refuted">Refuted</option></Select></Field>
            <Button variant="primary" disabled={form.title.length < 3 || !form.description} loading={add.isPending} onClick={() => add.mutate()}>Record finding</Button>
            {add.error && <ErrorState error={add.error} className="py-2" />}
          </CardBody>
        </Card>
      )}
    </div>
  );
}

function Reports({ c }: { c: CaseT }) {
  const { can } = useAuth();
  const gen = useCaseMutation<void>(c.id, () => api(`/cases/${c.id}/reports`, { method: "POST" }));
  return (
    <Card>
      <CardHeader title="Reports and exports" subtitle="PDF reports are stored with their SHA-256; each generation is recorded."
        actions={<>
          <Button size="sm" onClick={() => download(`/cases/${c.id}/export?format=json`)}><Download className="h-3.5 w-3.5" />JSON</Button>
          <Button size="sm" onClick={() => download(`/cases/${c.id}/export?format=csv`)}><Download className="h-3.5 w-3.5" />CSV</Button>
          {can("reports:generate") && <Button size="sm" variant="primary" loading={gen.isPending} onClick={() => gen.mutate()}><FileText className="h-3.5 w-3.5" />Generate PDF report</Button>}
        </>} />
      {gen.error && <ErrorState error={gen.error} className="py-2" />}
      {c.reports.length === 0 ? <Empty title="No reports generated" /> : (
        <Table><thead><tr><Th>File</Th><Th>Generated</Th><Th>By</Th><Th>SHA-256</Th><Th /></tr></thead>
          <tbody>{c.reports.map((r) => (
            <tr key={r.id} className="border-b border-border/60"><Td className="font-mono text-xs">{r.filename}</Td><Td className="text-xs">{dt(r.generated_at)}</Td><Td className="text-xs">{r.generated_by}</Td>
              <Td className="font-mono text-2xs text-subtle" title={r.sha256}>{r.sha256.slice(0, 16)}…</Td>
              <Td><Button size="sm" onClick={() => download(`/reports/${r.id}/download`, r.filename)}><Download className="h-3.5 w-3.5" />Download</Button></Td></tr>))}</tbody></Table>
      )}
    </Card>
  );
}
