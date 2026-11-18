import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { RotateCcw, Save } from "lucide-react";
import { api } from "@/lib/api";
import { dt, humanize } from "@/lib/format";
import { useAuth } from "@/lib/auth";
import type { Paged } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Badge, Card, CardBody, CardHeader, Field, Input, KV, Select, Table, Tabs, Td, Th } from "@/components/ui/primitives";
import { Empty, ErrorState, QueryState } from "@/components/ui/states";
import { PageHeader, Pagination } from "@/components/domain";

interface DetSettings { values: Record<string, number>; defaults: Record<string, number>; help: Record<string, string> }
const GROUPS: [string, string[]][] = [
  ["Structuring & smurfing", ["reporting_threshold", "structuring_band", "structuring_min_count", "structuring_window_days", "smurfing_min_senders"]],
  ["Rapid movement & cycles", ["rapid_window_hours", "rapid_min_ratio", "rapid_min_amount", "cycle_max_length", "cycle_max_span_days", "cycle_amount_tolerance"]],
  ["Velocity, dormancy & behaviour", ["velocity_window_minutes", "velocity_min_count", "dormant_days", "dormant_min_amount", "unusual_zscore", "unusual_min_history"]],
  ["Networks", ["network_min_shared_counterparties", "network_min_jaccard"]],
  ["Model & risk fusion", ["ml_contamination", "ml_weight", "flag_threshold"]],
];

export default function SettingsPage() {
  const { can } = useAuth();
  const [tab, setTab] = useState<"detection" | "system" | "audit">("detection");
  return (
    <>
      <PageHeader title="System Settings" subtitle="Detection parameters, deployment information and the audit trail." />
      <Tabs value={tab} onChange={setTab} className="mb-4" items={[{ value: "detection", label: "Detection" }, { value: "system", label: "System" }, ...(can("audit:read") ? [{ value: "audit" as const, label: "Audit log" }] : [])]} />
      {tab === "detection" && <Detection />}
      {tab === "system" && <System />}
      {tab === "audit" && <Audit />}
    </>
  );
}

function Detection() {
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["settings", "detection"], queryFn: () => api<DetSettings>("/settings/detection") });
  const [vals, setVals] = useState<Record<string, string>>({});
  useEffect(() => { if (q.data) setVals(Object.fromEntries(Object.entries(q.data.values).map(([k, v]) => [k, String(v)]))); }, [q.data]);
  const qc = useQueryClient();
  const save = useMutation({ mutationFn: () => api("/settings/detection", { method: "PUT", json: Object.fromEntries(Object.entries(vals).map(([k, v]) => [k, Number(v)])) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }) });
  const admin = can("settings:manage");
  return (
    <QueryState q={q}>
      {(d) => (
        <div className="space-y-4">
          <p className="text-xs text-muted">Changes apply to the next detection run. Previous runs keep the parameters they were run with (shown in reports).{!admin && " Only administrators can change these values."}</p>
          <div className="grid gap-4 lg:grid-cols-2">
            {GROUPS.map(([g, keys]) => (
              <Card key={g}><CardHeader title={g} /><CardBody className="space-y-3">
                {keys.map((k) => (
                  <Field key={k} label={humanize(k)} hint={String(vals[k]) !== String(d.defaults[k]) ? `default ${d.defaults[k]}` : undefined}>
                    <Input type="number" step="any" min={0} disabled={!admin} value={vals[k] ?? ""} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} />
                    <p className="mt-0.5 text-2xs text-subtle">{d.help[k]}</p>
                  </Field>
                ))}
              </CardBody></Card>
            ))}
          </div>
          {admin && <div className="flex gap-2">
            <Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}><Save className="h-3.5 w-3.5" />Save</Button>
            <Button onClick={() => setVals(Object.fromEntries(Object.entries(d.defaults).map(([k, v]) => [k, String(v)])))}><RotateCcw className="h-3.5 w-3.5" />Restore defaults (unsaved)</Button>
          </div>}
          {save.isSuccess && <p className="text-xs text-success">Saved. Run detection to apply.</p>}
          {save.error && <ErrorState error={save.error} className="py-2" />}
        </div>
      )}
    </QueryState>
  );
}

function System() {
  const q = useQuery({ queryKey: ["settings", "system"], queryFn: () => api<{ environment: string; storage_backend: string; max_import_mb: number; max_evidence_mb: number; access_token_minutes: number; insecure_secret: boolean }>("/settings/system") });
  const h = useQuery({ queryKey: ["health"], queryFn: () => api<{ status: string; database: string }>("/health") });
  return (
    <QueryState q={q}>
      {(s) => (
        <Card className="max-w-2xl"><CardHeader title="Deployment" /><CardBody>
          <KV items={[["Environment", s.environment], ["API health", <Badge tone={h.data?.status === "ok" ? "success" : "danger"}>{h.data?.status ?? "…"}</Badge>], ["Database", h.data?.database ?? "…"],
            ["Evidence storage", s.storage_backend], ["Import size limit", `${s.max_import_mb} MB`], ["Evidence size limit", `${s.max_evidence_mb} MB`], ["Session length", `${s.access_token_minutes} min`],
            ["Signing secret", s.insecure_secret ? <Badge tone="danger">development default - set FL_SECRET_KEY</Badge> : <Badge tone="success">configured</Badge>]]} />
          <p className="mt-4 text-xs text-muted">Secrets and connection settings are configured with environment variables (see SETUP.md); they cannot be changed from the UI.</p>
        </CardBody></Card>
      )}
    </QueryState>
  );
}

function Audit() {
  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");
  const q = useQuery({ queryKey: ["audit", page, action], queryFn: () => api<Paged<{ id: number; actor_email: string | null; action: string; resource_type: string | null; resource_id: string | null; details: Record<string, unknown>; ip_address: string | null; created_at: string }>>(`/audit?page=${page}&page_size=50${action ? `&action=${action}` : ""}`), placeholderData: (p) => p });
  return (
    <Card>
      <div className="border-b border-border p-3"><Field label="Action" className="max-w-xs"><Select value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }}>
        <option value="">All</option>{["auth", "import", "detection", "case", "evidence", "report", "entities", "alert", "user", "settings", "transactions", "graph", "timeline"].map((a) => <option key={a} value={a}>{a}</option>)}</Select></Field></div>
      <QueryState q={q}>
        {(d) => d.items.length === 0 ? <Empty title="No audit events" /> : (
          <>
            <Table><thead><tr><Th>When (UTC)</Th><Th>Actor</Th><Th>Action</Th><Th>Resource</Th><Th>Details</Th><Th>IP</Th></tr></thead>
              <tbody>{d.items.map((a) => (
                <tr key={a.id} className="border-b border-border/60"><Td className="num whitespace-nowrap text-xs">{dt(a.created_at)}</Td><Td className="text-xs">{a.actor_email ?? "anonymous"}</Td>
                  <Td><Badge tone={a.action.includes("failed") ? "danger" : "neutral"}>{a.action}</Badge></Td><Td className="text-xs text-muted">{a.resource_type}{a.resource_id && ` #${a.resource_id}`}</Td>
                  <Td className="max-w-md truncate font-mono text-2xs text-subtle" title={JSON.stringify(a.details)}>{Object.keys(a.details).length ? JSON.stringify(a.details) : ""}</Td><Td className="font-mono text-2xs">{a.ip_address}</Td></tr>))}</tbody></Table>
            <Pagination page={page} pageSize={50} total={d.total} onPage={setPage} />
          </>
        )}
      </QueryState>
    </Card>
  );
}
