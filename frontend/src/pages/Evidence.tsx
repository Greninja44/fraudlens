import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import { qs } from "@/lib/utils";
import type { Evidence, Paged } from "@/lib/types";
import { Card, Field, Input, Select } from "@/components/ui/primitives";
import { QueryState } from "@/components/ui/states";
import { PageHeader, Pagination, Stat } from "@/components/domain";
import { EvidenceDrawer, EvidenceTable } from "@/components/EvidenceViews";
import { n } from "@/lib/format";

export default function EvidenceVault() {
  const [f, setF] = useState({ search: "", verification_status: "", file_type: "" });
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<number | null>(null);
  const q = useQuery({ queryKey: ["evidence", f, page], queryFn: () => api<Paged<Evidence>>(`/evidence${qs({ ...f, page, page_size: 50 })}`), placeholderData: (p) => p });
  const all = useQuery({ queryKey: ["evidence", "stats"], queryFn: () => api<Paged<Evidence>>("/evidence?page_size=500") });
  const count = (s: string) => all.data?.items.filter((e) => e.verification_status === s).length ?? 0;
  return (
    <>
      <PageHeader title="Evidence Vault" subtitle="All evidence you are permitted to access. Files are write-once; every access, export, verification and metadata change is recorded in a hash-chained custody log." />
      <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Evidence items" value={n(all.data?.total)} />
        <Stat label="Verified" value={n(count("verified"))} tone="success" />
        <Stat label="Unverified" value={n(count("unverified"))} />
        <Stat label="Integrity failures" value={n(count("failed"))} tone={count("failed") ? "danger" : undefined} />
      </div>
      <Card>
        <div className="grid grid-cols-2 items-end gap-2 border-b border-border p-3 md:grid-cols-4">
          <Field label="Search" className="col-span-2"><Input value={f.search} placeholder="Evidence ID, filename, description or SHA-256" onChange={(e) => { setF({ ...f, search: e.target.value }); setPage(1); }} /></Field>
          <Field label="Integrity"><Select value={f.verification_status} onChange={(e) => setF({ ...f, verification_status: e.target.value })}><option value="">Any</option><option value="verified">Verified</option><option value="unverified">Unverified</option><option value="failed">Failed</option></Select></Field>
          <Field label="File type"><Select value={f.file_type} onChange={(e) => setF({ ...f, file_type: e.target.value })}><option value="">Any</option>{["pdf", "image", "csv", "spreadsheet", "document", "json", "text"].map((t) => <option key={t}>{t}</option>)}</Select></Field>
        </div>
        <QueryState q={q}>{(d) => <><EvidenceTable items={d.items} onOpen={setOpen} showCase /><Pagination page={page} pageSize={50} total={d.total} onPage={setPage} /></>}</QueryState>
      </Card>
      {open && <EvidenceDrawer id={open} onClose={() => setOpen(null)} />}
    </>
  );
}
