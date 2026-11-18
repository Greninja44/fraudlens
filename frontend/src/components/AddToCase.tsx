import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import type { CaseSummary, Paged, User } from "@/lib/types";
import { Button } from "./ui/button";
import { Dialog } from "./ui/dialog";
import { Field, Input, Select, Tabs, Textarea } from "./ui/primitives";
import { ErrorState } from "./ui/states";
import { useAuth } from "@/lib/auth";

/** Add accounts / transactions / an alert's material to an existing case, or open a new case with them. */
export function AddToCaseDialog({ open, onClose, accountIds = [], transactionIds = [], alertId, defaultTitle }: {
  open: boolean; onClose: () => void; accountIds?: number[]; transactionIds?: number[]; alertId?: number; defaultTitle?: string;
}) {
  const { user } = useAuth();
  const [mode, setMode] = useState<"new" | "existing">("new");
  const [caseId, setCaseId] = useState("");
  const [title, setTitle] = useState(defaultTitle ?? "");
  const [summary, setSummary] = useState("");
  const [objectives, setObjectives] = useState("");
  const [priority, setPriority] = useState("high");
  const [assignee, setAssignee] = useState<string>(String(user?.id ?? ""));
  const nav = useNavigate();
  const qc = useQueryClient();
  const cases = useQuery({ queryKey: ["cases", "open-for-add"], queryFn: () => api<Paged<CaseSummary>>("/cases?status=open,under_investigation,escalated&page_size=100"), enabled: open });
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<User[]>("/users"), enabled: open });

  const m = useMutation({
    mutationFn: async () => {
      if (mode === "new") {
        return api<CaseSummary>("/cases", { method: "POST", json: { title, summary, objectives, priority, account_ids: accountIds, transaction_ids: transactionIds,
          alert_ids: alertId ? [alertId] : [], assignee_ids: assignee ? [Number(assignee)] : [] } });
      }
      const id = Number(caseId);
      if (accountIds.length) await api(`/cases/${id}/accounts`, { method: "POST", json: { account_ids: accountIds, role: "subject" } });
      if (transactionIds.length || alertId) return api<CaseSummary>(`/cases/${id}/transactions`, { method: "POST", json: { transaction_ids: transactionIds, alert_id: alertId ?? null, include_accounts: true } });
      return api<CaseSummary>(`/cases/${id}`);
    },
    onSuccess: (c) => { qc.invalidateQueries({ queryKey: ["cases"] }); qc.invalidateQueries({ queryKey: ["case"] }); onClose(); nav(`/cases/${c.id}`); },
  });
  const what = [accountIds.length && `${accountIds.length} account(s)`, transactionIds.length && `${transactionIds.length} transaction(s)`, alertId && "the alert's accounts and transactions"].filter(Boolean).join(", ");
  const valid = mode === "new" ? title.trim().length >= 3 : !!caseId;
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()} title={what ? "Add to investigation" : "New investigation case"} description={what ? `Will add ${what}.` : "Create a new investigation case; add accounts and transactions afterwards."}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!valid} loading={m.isPending} onClick={() => m.mutate()}>{mode === "new" ? "Open case" : "Add to case"}</Button></>}>
      <Tabs value={mode} onChange={setMode} items={[{ value: "new", label: "New case" }, { value: "existing", label: "Existing case" }]} className="mb-4" />
      {mode === "new" ? (
        <div className="space-y-3">
          <Field label="Title"><Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Layering through shell companies" /></Field>
          <Field label="Summary"><Textarea value={summary} onChange={(e) => setSummary(e.target.value)} /></Field>
          <Field label="Objectives"><Textarea value={objectives} onChange={(e) => setObjectives(e.target.value)} placeholder="What must this investigation establish?" /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Priority"><Select value={priority} onChange={(e) => setPriority(e.target.value)}>{["low", "medium", "high", "critical"].map((p) => <option key={p}>{p}</option>)}</Select></Field>
            <Field label="Assign to"><Select value={assignee} onChange={(e) => setAssignee(e.target.value)}>
              {users.data?.filter((u) => u.role !== "reviewer" && u.is_active).map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
            </Select></Field>
          </div>
        </div>
      ) : (
        <Field label="Case">
          <Select value={caseId} onChange={(e) => setCaseId(e.target.value)}>
            <option value="">Select a case…</option>
            {cases.data?.items.map((c) => <option key={c.id} value={c.id}>{c.case_number} · {c.title}</option>)}
          </Select>
        </Field>
      )}
      {m.error && <ErrorState error={m.error} className="py-3" />}
    </Dialog>
  );
}
