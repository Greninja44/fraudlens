import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Download, Eye, FileUp, Fingerprint, Link2, Pencil, ShieldCheck } from "lucide-react";
import { api, download, objectUrl, upload } from "@/lib/api";
import { bytes, dt, humanize } from "@/lib/format";
import { useAuth } from "@/lib/auth";
import type { Evidence } from "@/lib/types";
import { Button } from "./ui/button";
import { Badge, Field, KV, Select, Table, Td, Textarea, Th, Tr } from "./ui/primitives";
import { Dialog, Drawer } from "./ui/dialog";
import { Empty, ErrorState, QueryState } from "./ui/states";
import { VerificationBadge } from "./domain";

interface Custody { id: number; action: string; actor_email: string | null; details: Record<string, unknown>; ip_address: string | null; created_at: string; prev_hash: string; event_hash: string }
type EvidenceDetail = Evidence & { custody: Custody[]; chain: { valid: boolean; events: number; head_hash?: string; broken_at_event_id?: number } };

export function useEvidenceMeta() {
  return useQuery({ queryKey: ["evidence-meta"], queryFn: () => api<{ categories: string[]; extensions: string[]; max_mb: number }>("/evidence-categories"), staleTime: Infinity });
}

export function EvidenceUploadDialog({ caseId, open, onClose }: { caseId: number; open: boolean; onClose: () => void }) {
  const meta = useEvidenceMeta();
  const [file, setFile] = useState<File | null>(null);
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("supporting_document");
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: () => { const fd = new FormData(); fd.append("file", file!); fd.append("description", description); fd.append("category", category); return upload<Evidence>(`/cases/${caseId}/evidence`, fd); },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["case", caseId] }); qc.invalidateQueries({ queryKey: ["evidence"] }); onClose(); },
  });
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()} title="Attach evidence"
      description="The original file is stored unchanged and read-only. Its SHA-256 is computed on upload and recorded in the chain of custody."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!file} loading={m.isPending} onClick={() => m.mutate()}><FileUp className="h-3.5 w-3.5" />Upload</Button></>}>
      <div className="space-y-3">
        <Field label="File" hint={meta.data && `${meta.data.extensions.join(" ")} · max ${meta.data.max_mb} MB`}>
          <input type="file" accept={meta.data?.extensions.join(",")} onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="block w-full text-xs file:mr-3 file:rounded file:border file:border-border file:bg-raised file:px-2 file:py-1 file:text-fg" />
        </Field>
        <Field label="Category"><Select value={category} onChange={(e) => setCategory(e.target.value)}>{meta.data?.categories.map((c) => <option key={c} value={c}>{humanize(c)}</option>)}</Select></Field>
        <Field label="Description"><Textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Source, how it was obtained, relevance" /></Field>
        {m.error && <ErrorState error={m.error} className="py-2" />}
      </div>
    </Dialog>
  );
}

export function EvidenceTable({ items, onOpen, showCase }: { items: Evidence[]; onOpen: (id: number) => void; showCase?: boolean }) {
  if (!items.length) return <Empty title="No evidence">Attach files, screenshots, statements or system extracts.</Empty>;
  return (
    <Table>
      <thead><tr><Th>Evidence ID</Th>{showCase && <Th>Case</Th>}<Th>File</Th><Th>Category</Th><Th className="text-right">Size</Th><Th>SHA-256</Th><Th>Integrity</Th><Th>Uploaded</Th></tr></thead>
      <tbody>{items.map((e) => (
        <Tr key={e.id} onClick={() => onOpen(e.id)}>
          <Td className="font-mono text-xs">{e.evidence_code}</Td>
          {showCase && <Td>{e.case_number && <Link to={`/cases/${e.case_id}`} onClick={(ev) => ev.stopPropagation()} className="font-mono text-xs text-accent">{e.case_number}</Link>}</Td>}
          <Td><div className="max-w-[260px] truncate">{e.original_filename}</div><div className="max-w-[260px] truncate text-xs text-muted">{e.description}</div></Td>
          <Td className="text-xs">{humanize(e.category)}</Td>
          <Td className="num text-right text-xs">{bytes(e.size_bytes)}</Td>
          <Td className="font-mono text-2xs text-subtle" title={e.sha256}>{e.sha256.slice(0, 12)}…</Td>
          <Td><VerificationBadge status={e.verification_status} /></Td>
          <Td className="text-xs text-muted">{dt(e.uploaded_at)}<div className="text-2xs">{e.uploaded_by}</div></Td>
        </Tr>))}</tbody>
    </Table>
  );
}

function Preview({ e }: { e: Evidence }) {
  const [url, setUrl] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const urlRef = useRef<string | null>(null);
  useEffect(() => () => { if (urlRef.current) URL.revokeObjectURL(urlRef.current); }, []);
  const load = () => objectUrl(`/evidence/${e.id}/download?inline=true`).then((u) => { urlRef.current = u; setUrl(u); }).catch(setErr);
  if (!["image", "pdf"].includes(e.file_type)) return null;
  if (!url) return <Button size="sm" onClick={load}><Eye className="h-3.5 w-3.5" />Preview (logged as access)</Button>;
  if (err) return <ErrorState error={err} />;
  return e.file_type === "image" ? <img src={url} alt={e.original_filename} className="max-h-80 rounded border border-border" /> : <iframe title="pdf" src={url} className="h-96 w-full rounded border border-border bg-white" />;
}

export function EvidenceDrawer({ id, onClose }: { id: number; onClose: () => void }) {
  const q = useQuery({ queryKey: ["evidence-item", id], queryFn: () => api<EvidenceDetail>(`/evidence/${id}`), staleTime: Infinity });
  const qc = useQueryClient();
  const { can } = useAuth();
  const [editing, setEditing] = useState(false);
  const verify = useMutation({
    mutationFn: () => api<{ hash_match: boolean; actual_sha256: string | null; custody_chain: { valid: boolean } }>(`/evidence/${id}/verify`, { method: "POST" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["evidence-item", id] }); qc.invalidateQueries({ queryKey: ["evidence"] }); qc.invalidateQueries({ queryKey: ["case"] }); },
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["evidence-item", id] });
  return (
    <Drawer open onOpenChange={(o) => !o && onClose()} title={q.data ? `${q.data.evidence_code} · ${q.data.original_filename}` : "Evidence"}
      actions={q.data && <>
        {can("evidence:verify") && <Button size="sm" variant="success" loading={verify.isPending} onClick={() => verify.mutate()}><ShieldCheck className="h-3.5 w-3.5" />Verify integrity</Button>}
        <Button size="sm" onClick={() => download(`/evidence/${id}/download`, q.data!.original_filename).then(refresh)}><Download className="h-3.5 w-3.5" />Export</Button>
      </>}>
      <QueryState q={q}>
        {(e) => (
          <div className="space-y-5">
            {verify.data && (
              <div className={`rounded border p-2 text-xs ${verify.data.hash_match ? "border-success/40 bg-success/5 text-success" : "border-danger/40 bg-danger/5 text-danger"}`}>
                {verify.data.hash_match ? "Stored file matches the SHA-256 recorded at upload." : `INTEGRITY FAILURE: stored file hash ${verify.data.actual_sha256 ?? "(file missing)"} does not match the recorded hash.`}
                {" "}Custody chain {verify.data.custody_chain.valid ? "intact" : "BROKEN"}.
              </div>
            )}
            {verify.error && <ErrorState error={verify.error} className="py-2" />}
            <KV items={[["Case", <Link className="text-accent" to={`/cases/${e.case_id}`}>{e.case_number}</Link>], ["Original filename", e.original_filename], ["Type", `${e.file_type} · ${e.content_type}`],
              ["Category", humanize(e.category)], ["Size", bytes(e.size_bytes)], ["SHA-256", <span className="break-all font-mono text-2xs">{e.sha256}</span>],
              ["Uploaded", `${dt(e.uploaded_at)} UTC by ${e.uploaded_by}`], ["Integrity", <span className="flex items-center gap-2"><VerificationBadge status={e.verification_status} />{e.last_verified_at && <span className="text-xs text-muted">last checked {dt(e.last_verified_at)}</span>}</span>],
              ["Description", <span className="whitespace-pre-wrap">{e.description || "—"}</span>]]} />
            {can("evidence:edit") && <Button size="sm" variant="ghost" onClick={() => setEditing(true)}><Pencil className="h-3.5 w-3.5" />Edit metadata</Button>}
            <Preview e={e} />
            <div>
              <div className="mb-2 flex items-center justify-between">
                <h4 className="text-xs font-semibold uppercase tracking-wide text-subtle">Chain of custody</h4>
                <Badge tone={e.chain.valid ? "success" : "danger"}><Link2 className="h-3 w-3" />{e.chain.valid ? "hash chain intact" : `chain broken at event ${e.chain.broken_at_event_id}`}</Badge>
              </div>
              <ol className="relative space-y-3 border-l border-border pl-4">
                {e.custody.map((c) => (
                  <li key={c.id} className="relative">
                    <span className={`absolute -left-[21px] top-1 h-2.5 w-2.5 rounded-full border-2 border-surface ${c.action === "verification_failed" ? "bg-danger" : c.action === "verified" ? "bg-success" : "bg-accent"}`} />
                    <div className="flex flex-wrap items-center gap-2 text-xs"><span className="font-medium capitalize">{c.action.replace(/_/g, " ")}</span><span className="text-muted">{dt(c.created_at)} UTC · {c.actor_email ?? "system"}{c.ip_address && ` · ${c.ip_address}`}</span></div>
                    {Object.keys(c.details).length > 0 && <div className="mt-0.5 break-all font-mono text-2xs text-subtle">{JSON.stringify(c.details)}</div>}
                    <div className="mt-0.5 flex items-center gap-1 font-mono text-2xs text-subtle" title={`prev ${c.prev_hash}`}><Fingerprint className="h-3 w-3" />{c.event_hash.slice(0, 24)}…</div>
                  </li>
                ))}
              </ol>
              <p className="mt-3 text-2xs text-subtle">Each custody event's hash covers the previous event's hash, so editing, deleting or reordering any record is detectable. Viewing this panel is itself recorded.</p>
            </div>
          </div>
        )}
      </QueryState>
      {editing && q.data && <EditMeta e={q.data} onClose={() => { setEditing(false); refresh(); }} />}
    </Drawer>
  );
}

function EditMeta({ e, onClose }: { e: Evidence; onClose: () => void }) {
  const meta = useEvidenceMeta();
  const [description, setDescription] = useState(e.description);
  const [category, setCategory] = useState(e.category);
  const qc = useQueryClient();
  const m = useMutation({ mutationFn: () => api(`/evidence/${e.id}`, { method: "PATCH", json: { description, category } }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["evidence"] }); onClose(); } });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Edit evidence metadata" description="The file itself cannot be changed. Metadata changes are recorded (old → new) in the chain of custody."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={m.isPending} onClick={() => m.mutate()}>Save</Button></>}>
      <div className="space-y-3">
        <Field label="Category"><Select value={category} onChange={(ev) => setCategory(ev.target.value)}>{meta.data?.categories.map((c) => <option key={c} value={c}>{humanize(c)}</option>)}</Select></Field>
        <Field label="Description"><Textarea value={description} onChange={(ev) => setDescription(ev.target.value)} /></Field>
        {m.error && <ErrorState error={m.error} className="py-2" />}
      </div>
    </Dialog>
  );
}
