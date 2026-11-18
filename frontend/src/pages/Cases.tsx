import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus } from "lucide-react";
import { api } from "@/lib/api";
import { dt, humanize } from "@/lib/format";
import { qs } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import type { CaseSummary, Paged } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, Field, Input, Select, Table, Td, Th, Tr } from "@/components/ui/primitives";
import { Empty, QueryState } from "@/components/ui/states";
import { CaseStatusBadge, PageHeader, Pagination, PriorityBadge } from "@/components/domain";
import { AddToCaseDialog } from "@/components/AddToCase";

const STATUSES = ["open", "under_investigation", "pending_review", "escalated", "closed"];

export default function Cases() {
  const nav = useNavigate();
  const { can, user } = useAuth();
  const [f, setF] = useState({ status: "", priority: "", search: "", mine: false });
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const q = useQuery({ queryKey: ["cases", f, page], queryFn: () => api<Paged<CaseSummary>>(`/cases${qs({ ...f, page, page_size: 25 })}`), placeholderData: (p) => p });
  const counts = useQuery({ queryKey: ["cases", "counts"], queryFn: () => api<Paged<CaseSummary>>("/cases?page_size=500") });
  const by = (s: string) => counts.data?.items.filter((c) => c.status === s).length ?? 0;
  return (
    <>
      <PageHeader title="Investigation Cases" subtitle={user?.role === "investigator" ? "Cases you created or are assigned to." : "All investigation cases."}
        actions={can("cases:write") && <Button variant="primary" onClick={() => setCreating(true)}><Plus className="h-3.5 w-3.5" />New case</Button>} />
      <div className="mb-3 flex flex-wrap gap-2">
        {STATUSES.map((s) => (
          <button key={s} onClick={() => { setF({ ...f, status: f.status === s ? "" : s }); setPage(1); }}
            className={`rounded-md border px-3 py-1.5 text-xs ${f.status === s ? "border-accent bg-accent/10 text-fg" : "border-border text-muted hover:text-fg"}`}>
            {humanize(s)} <span className="num ml-1 font-semibold">{by(s)}</span>
          </button>
        ))}
      </div>
      <Card>
        <div className="grid grid-cols-2 items-end gap-2 border-b border-border p-3 md:grid-cols-4">
          <Field label="Search" className="col-span-2"><Input value={f.search} placeholder="Title, case number, summary" onChange={(e) => { setF({ ...f, search: e.target.value }); setPage(1); }} /></Field>
          <Field label="Priority"><Select value={f.priority} onChange={(e) => setF({ ...f, priority: e.target.value })}><option value="">Any</option>{["critical", "high", "medium", "low"].map((p) => <option key={p}>{p}</option>)}</Select></Field>
          <label className="flex h-8 items-center gap-1.5 text-xs"><input type="checkbox" checked={f.mine} onChange={(e) => setF({ ...f, mine: e.target.checked })} />Only my cases</label>
        </div>
        <QueryState q={q}>
          {(d) => d.items.length === 0 ? <Empty title="No cases">Open a case from an alert in the Detection Center, or create one here.</Empty> : (
            <>
              <Table>
                <thead><tr><Th>Case</Th><Th>Status</Th><Th>Priority</Th><Th>Assigned</Th><Th className="text-right">Accounts</Th><Th className="text-right">Txns</Th><Th className="text-right">Evidence</Th><Th>Progress</Th><Th>Updated</Th></tr></thead>
                <tbody>{d.items.map((c) => (
                  <Tr key={c.id} onClick={() => nav(`/cases/${c.id}`)}>
                    <Td><div className="font-mono text-2xs text-subtle">{c.case_number}</div><div className="font-medium">{c.title}</div></Td>
                    <Td><CaseStatusBadge status={c.status} /></Td><Td><PriorityBadge priority={c.priority} /></Td>
                    <Td className="text-xs">{c.assignees.map((a) => a.full_name).join(", ")}</Td>
                    <Td className="num text-right">{c.counts.accounts}</Td><Td className="num text-right">{c.counts.transactions}</Td><Td className="num text-right">{c.counts.evidence}</Td>
                    <Td><div className="flex items-center gap-2"><div className="h-1.5 w-20 rounded-full bg-raised"><div className="h-full rounded-full bg-accent" style={{ width: `${c.progress.percent}%` }} /></div><span className="num text-2xs text-muted">{c.progress.percent}%</span></div></Td>
                    <Td className="text-xs text-muted">{dt(c.updated_at)}</Td>
                  </Tr>))}</tbody>
              </Table>
              <Pagination page={page} pageSize={25} total={d.total} onPage={setPage} />
            </>
          )}
        </QueryState>
      </Card>
      {creating && <AddToCaseDialog open onClose={() => setCreating(false)} />}
    </>
  );
}
