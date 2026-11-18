import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, Download, FileSpreadsheet, UploadCloud, XCircle } from "lucide-react";
import { api, download, upload } from "@/lib/api";
import { bytes, dt, n } from "@/lib/format";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge, Card, CardBody, CardHeader, Select, Table, Td, Th, Tr } from "@/components/ui/primitives";
import { Empty, ErrorState, QueryState } from "@/components/ui/states";
import { PageHeader } from "@/components/domain";
import { Dialog } from "@/components/ui/dialog";

interface Batch {
  id: number; filename: string; file_type: string; file_sha256: string; file_size: number; status: string; rows_total: number; rows_imported: number;
  rows_duplicate: number; rows_invalid: number; rows_with_warnings: number; column_mapping: Record<string, string>; has_ground_truth: boolean;
  created_at: string; completed_at: string | null; created_by: string | null; previously_imported: boolean;
  columns?: string[]; suggested_mapping?: Record<string, { column: string; confidence: number; method: string }>;
  sample?: Record<string, string>[]; errors?: { row?: number | null; txn_id?: string; errors: string[]; level?: string }[];
  fields?: Record<string, { label: string; required: boolean }>;
}

const statusTone = (s: string) => (s === "completed" ? "success" : s === "failed" ? "danger" : s === "pending" ? "neutral" : "warning") as "success" | "danger" | "neutral" | "warning";

export default function DataImport() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [preview, setPreview] = useState<Batch | null>(null);
  const [detail, setDetail] = useState<number | null>(null);
  const [active, setActive] = useState<number | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const history = useQuery({ queryKey: ["imports"], queryFn: () => api<{ items: Batch[]; total: number }>("/imports?page_size=50"),
    refetchInterval: (q) => (q.state.data?.items.some((b) => ["processing", "queued"].includes(b.status) || (b.status === "pending" && b.id === active)) ? 1500 : false) });
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => api<{ files: { name: string; size_bytes: number; description: string }[]; card: { transactions?: number; suspicious_labelled?: number; patterns?: Record<string, number> } }>("/datasets") });

  const stage = useMutation({
    mutationFn: (f: File) => { const fd = new FormData(); fd.append("file", f); return upload<Batch>("/imports/preview", fd); },
    onSuccess: (b) => { setPreview(b); qc.invalidateQueries({ queryKey: ["imports"] }); },
  });

  return (
    <>
      <PageHeader title="Data Import" subtitle="Ingest CSV, Excel (XLSX) or JSON transaction files. Columns are mapped automatically; you can adjust before importing." />
      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Upload transactions" />
          <CardBody>
            {can("data:import") ? (
              <label onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) stage.mutate(f); }}
                className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border-strong bg-bg px-6 py-10 text-center hover:border-accent">
                <UploadCloud className="h-6 w-6 text-muted" />
                <div className="text-[13px] font-medium">{stage.isPending ? "Uploading and analysing…" : "Drop a file here or click to choose"}</div>
                <div className="text-xs text-muted">.csv · .xlsx · .json — up to 200 MB. The original file is preserved unchanged with its SHA-256.</div>
                <input ref={fileRef} type="file" className="hidden" accept=".csv,.xlsx,.json" onChange={(e) => { const f = e.target.files?.[0]; if (f) stage.mutate(f); e.target.value = ""; }} />
              </label>
            ) : <Empty title="Read-only access">Your role cannot import data.</Empty>}
            {stage.error && <ErrorState error={stage.error} className="py-4" />}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Synthetic test datasets" subtitle="Fictitious data with ground-truth labels for evaluating detection" />
          <QueryState q={datasets}>
            {(d) => (
              <div>
                {d.card.transactions && <p className="px-4 pt-3 text-xs text-muted"><span className="num">{n(d.card.transactions)}</span> transactions, <span className="num">{n(d.card.suspicious_labelled)}</span> labelled suspicious across {Object.keys(d.card.patterns ?? {}).length} patterns.</p>}
                <ul className="divide-y divide-border">
                  {d.files.map((f) => (
                    <li key={f.name} className="flex items-center gap-3 px-4 py-2.5">
                      <FileSpreadsheet className="h-4 w-4 shrink-0 text-muted" />
                      <div className="min-w-0 flex-1"><div className="truncate font-mono text-xs">{f.name}</div><div className="text-2xs text-subtle">{f.description} · {bytes(f.size_bytes)}</div></div>
                      <Button size="icon" variant="ghost" onClick={() => download(`/datasets/${f.name}`, f.name)} aria-label={`Download ${f.name}`}><Download className="h-3.5 w-3.5" /></Button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </QueryState>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader title="Import history" subtitle="Every import with its validation outcome" />
        <QueryState q={history}>
          {(h) => h.items.length === 0 ? <Empty title="No imports yet" /> : (
            <Table>
              <thead><tr><Th>#</Th><Th>File</Th><Th>Status</Th><Th className="text-right">Rows</Th><Th className="text-right">Imported</Th><Th className="text-right">Duplicates</Th><Th className="text-right">Invalid</Th><Th className="text-right">Warnings</Th><Th>By</Th><Th>When</Th></tr></thead>
              <tbody>
                {h.items.map((b) => (
                  <Tr key={b.id} onClick={() => (b.status === "pending" ? api<Batch>(`/imports/${b.id}`).then(setPreview) : setDetail(b.id))}>
                    <Td className="num text-muted">{b.id}</Td>
                    <Td><div className="font-mono text-xs">{b.filename}</div><div className="font-mono text-2xs text-subtle" title={b.file_sha256}>sha256 {b.file_sha256.slice(0, 16)}…</div></Td>
                    <Td><Badge tone={statusTone(b.status)}>{b.status}</Badge>{b.has_ground_truth && <Badge className="ml-1" title="File includes synthetic ground-truth labels">labels</Badge>}</Td>
                    <Td className="num text-right">{n(b.rows_total)}</Td>
                    <Td className="num text-right">{n(b.rows_imported)}</Td>
                    <Td className="num text-right">{n(b.rows_duplicate)}</Td>
                    <Td className={`num text-right ${b.rows_invalid ? "text-danger" : ""}`}>{n(b.rows_invalid)}</Td>
                    <Td className={`num text-right ${b.rows_with_warnings ? "text-warning" : ""}`}>{n(b.rows_with_warnings)}</Td>
                    <Td className="text-xs text-muted">{b.created_by ?? "—"}</Td>
                    <Td className="text-xs text-muted">{dt(b.created_at)}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          )}
        </QueryState>
      </Card>
      {preview && <MappingDialog batch={preview} onClose={() => setPreview(null)} onStarted={(id) => { setPreview(null); setActive(id); setDetail(id); qc.invalidateQueries({ queryKey: ["imports"] }); }} />}
      {detail && <BatchDialog id={detail} onClose={() => setDetail(null)} />}
    </>
  );
}

function MappingDialog({ batch, onClose, onStarted }: { batch: Batch; onClose: () => void; onStarted: (id: number) => void }) {
  const [mapping, setMapping] = useState<Record<string, string>>(() => Object.fromEntries(Object.entries(batch.suggested_mapping ?? {}).map(([k, v]) => [k, v.column])));
  const [skipDup, setSkipDup] = useState(true);
  const commit = useMutation({ mutationFn: () => api<Batch>(`/imports/${batch.id}/commit`, { method: "POST", json: { mapping, skip_content_duplicates: skipDup } }), onSuccess: (b) => onStarted(b.id) });
  const fields = batch.fields ?? {};
  const missing = Object.entries(fields).filter(([k, f]) => f.required && !mapping[k]).map(([, f]) => f.label);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} wide title={`Map columns · ${batch.filename}`}
      description={`${batch.columns?.length ?? 0} columns detected. Required fields are marked *. ${batch.previously_imported ? "This exact file was imported before - rows will be reported as duplicates." : ""}`}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={missing.length > 0} loading={commit.isPending} onClick={() => commit.mutate()}>Import</Button></>}>
      <div className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
        {Object.entries(fields).map(([k, f]) => {
          const s = batch.suggested_mapping?.[k];
          return (
            <div key={k} className="flex items-center gap-2">
              <div className="w-40 shrink-0 text-xs">{f.label}{f.required && <span className="text-danger"> *</span>}</div>
              <Select value={mapping[k] ?? ""} onChange={(e) => setMapping((m) => ({ ...m, [k]: e.target.value }))} aria-label={f.label}>
                <option value="">— not mapped —</option>
                {batch.columns?.map((c) => <option key={c} value={c}>{c}</option>)}
              </Select>
              {s && mapping[k] === s.column && <Badge tone={s.method === "exact" ? "success" : "warning"} title={`Auto-mapped (${s.method}, ${Math.round(s.confidence * 100)}%)`}>{s.method}</Badge>}
            </div>
          );
        })}
      </div>
      <label className="mt-3 flex items-start gap-2 text-xs text-muted"><input type="checkbox" className="mt-0.5" checked={skipDup} onChange={(e) => setSkipDup(e.target.checked)} />
        <span>Skip rows matching an existing transaction's accounts and amount within 60 seconds, under a different ID (overlapping exports). Rows with an already-imported transaction ID are always skipped.</span></label>
      {missing.length > 0 && <p className="mt-3 text-xs text-danger">Map required fields: {missing.join(", ")}</p>}
      {mapping.ground_truth_label && <p className="mt-3 text-xs text-muted">Ground-truth columns are stored only to evaluate detection on synthetic data. They are never used by the detection engine.</p>}
      <div className="mt-4 text-2xs font-semibold uppercase tracking-wide text-subtle">Sample rows</div>
      <div className="mt-1 max-h-48 overflow-auto rounded border border-border">
        <table className="w-full text-2xs">
          <thead><tr>{batch.columns?.map((c) => <th key={c} className="whitespace-nowrap border-b border-border bg-raised px-2 py-1 text-left font-medium">{c}</th>)}</tr></thead>
          <tbody>{batch.sample?.map((r, i) => <tr key={i}>{batch.columns?.map((c) => <td key={c} className="whitespace-nowrap border-b border-border/50 px-2 py-1 font-mono text-muted">{String(r[c] ?? "")}</td>)}</tr>)}</tbody>
        </table>
      </div>
      {commit.error && <ErrorState error={commit.error} className="py-3" />}
    </Dialog>
  );
}

function BatchDialog({ id, onClose }: { id: number; onClose: () => void }) {
  const q = useQuery({ queryKey: ["import", id], queryFn: () => api<Batch>(`/imports/${id}`),
    refetchInterval: (qq) => (["completed", "failed"].includes(qq.state.data?.status ?? "") ? false : 1200) });
  const qc = useQueryClient();
  useEffect(() => { if (q.data?.status === "completed") { qc.invalidateQueries({ queryKey: ["imports"] }); qc.invalidateQueries({ queryKey: ["dashboard"] }); } }, [q.data?.status, qc]);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} wide title={`Import #${id}`} description={q.data?.filename}>
      <QueryState q={q}>
        {(b) => (
          <div className="space-y-4">
            <div className="flex items-center gap-2 text-[13px]">
              {b.status === "completed" ? <CheckCircle2 className="h-4 w-4 text-success" /> : b.status === "failed" ? <XCircle className="h-4 w-4 text-danger" /> : <span className="h-3 w-3 animate-pulse rounded-full bg-warning" />}
              <span className="font-medium capitalize">{b.status}</span>
              <span className="text-muted">· {n(b.rows_imported)} of {n(b.rows_total)} rows imported{b.status === "processing" ? " so far" : ""}</span>
            </div>
            <div className="grid grid-cols-4 gap-2 text-center">
              {[["Imported", b.rows_imported, ""], ["Duplicates skipped", b.rows_duplicate, ""], ["Invalid rejected", b.rows_invalid, "text-danger"], ["With warnings", b.rows_with_warnings, "text-warning"]].map(([l, v, c]) => (
                <div key={String(l)} className="rounded border border-border p-2"><div className={`num text-base font-semibold ${v ? c : ""}`}>{n(Number(v))}</div><div className="text-2xs text-subtle">{l}</div></div>
              ))}
            </div>
            {b.status === "completed" && <p className="text-xs text-muted">Next: <Link to="/detection" className="text-accent">run detection</Link> to score the new transactions.</p>}
            <div>
              <div className="mb-1 text-2xs font-semibold uppercase tracking-wide text-subtle">Row-level issues {b.errors && b.errors.length >= 500 && "(first 500)"}</div>
              {b.errors?.length ? (
                <div className="max-h-64 overflow-auto rounded border border-border">
                  <table className="w-full text-xs"><tbody>
                    {b.errors.map((e, i) => (
                      <tr key={i} className="border-b border-border/50"><td className="num whitespace-nowrap px-2 py-1 text-subtle">{e.row ? `row ${e.row}` : e.txn_id ?? "—"}</td>
                        <td className="px-2 py-1"><Badge tone={e.level === "duplicate" ? "neutral" : "danger"}>{e.level === "duplicate" ? "duplicate" : "invalid"}</Badge></td>
                        <td className="px-2 py-1 text-muted">{e.errors.join("; ")}</td></tr>
                    ))}
                  </tbody></table>
                </div>
              ) : <p className="text-xs text-muted">No rejected rows.</p>}
            </div>
            <div className="text-2xs text-subtle">Column mapping: {Object.entries(b.column_mapping).map(([k, v]) => `${k} ← ${v}`).join(" · ")}</div>
          </div>
        )}
      </QueryState>
    </Dialog>
  );
}
