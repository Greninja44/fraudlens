import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Download, FileText } from "lucide-react";
import { api, download } from "@/lib/api";
import { bytes, dt } from "@/lib/format";
import { useAuth } from "@/lib/auth";
import type { CaseSummary, Paged, ReportItem } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, Field, Select, Table, Td, Th } from "@/components/ui/primitives";
import { Empty, ErrorState, QueryState } from "@/components/ui/states";
import { PageHeader } from "@/components/domain";

const SECTIONS = ["Investigation ID", "Case summary", "Investigation objectives", "Entities involved", "Suspicious transactions", "Detection methodology",
  "Financial flow visualisation", "Transaction timeline", "Supporting evidence references (with SHA-256 and custody status)", "Investigator observations",
  "Automated findings summary vs. investigator conclusions", "Limitations", "Generation timestamp"];

export default function Reports() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [caseId, setCaseId] = useState("");
  const reports = useQuery({ queryKey: ["reports"], queryFn: () => api<ReportItem[]>("/reports") });
  const cases = useQuery({ queryKey: ["cases", "all-lite"], queryFn: () => api<Paged<CaseSummary>>("/cases?page_size=200") });
  const gen = useMutation({ mutationFn: () => api<ReportItem>(`/cases/${caseId}/reports`, { method: "POST" }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["reports"] }); qc.invalidateQueries({ queryKey: ["case"] }); download(`/reports/${r.id}/download`, r.filename); } });
  return (
    <>
      <PageHeader title="Report Generation" subtitle="Professional PDF investigation reports, plus CSV and JSON exports of case data." />
      <div className="grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader title="Generate a report" />
          <CardBody className="space-y-3">
            <Field label="Case"><Select value={caseId} onChange={(e) => setCaseId(e.target.value)}><option value="">Select a case…</option>{cases.data?.items.map((c) => <option key={c.id} value={c.id}>{c.case_number} · {c.title}</option>)}</Select></Field>
            <div className="flex flex-wrap gap-2">
              {can("reports:generate") && <Button variant="primary" disabled={!caseId} loading={gen.isPending} onClick={() => gen.mutate()}><FileText className="h-3.5 w-3.5" />Generate PDF</Button>}
              <Button disabled={!caseId} onClick={() => download(`/cases/${caseId}/export?format=csv`)}><Download className="h-3.5 w-3.5" />Transactions CSV</Button>
              <Button disabled={!caseId} onClick={() => download(`/cases/${caseId}/export?format=json`)}><Download className="h-3.5 w-3.5" />Investigation JSON</Button>
            </div>
            {gen.error && <ErrorState error={gen.error} className="py-2" />}
            <div className="pt-2 text-xs text-muted">
              <div className="mb-1 text-2xs font-semibold uppercase tracking-wide text-subtle">Report contents</div>
              <ol className="list-decimal space-y-0.5 pl-4">{SECTIONS.map((s) => <li key={s}>{s}</li>)}</ol>
              <p className="mt-2">Graph exports (JSON, GraphML, PNG) are available on the Network page.</p>
            </div>
          </CardBody>
        </Card>
        <Card className="xl:col-span-2">
          <CardHeader title="Generated reports" />
          <QueryState q={reports}>
            {(rs) => rs.length === 0 ? <Empty title="No reports yet" /> : (
              <Table><thead><tr><Th>Case</Th><Th>File</Th><Th>Generated</Th><Th>By</Th><Th className="text-right">Size</Th><Th>SHA-256</Th><Th /></tr></thead>
                <tbody>{rs.map((r) => (
                  <tr key={r.id} className="border-b border-border/60">
                    <Td><Link to={`/cases/${r.case_id}`} className="font-mono text-xs text-accent">{r.case_number}</Link><div className="max-w-[200px] truncate text-xs text-muted">{r.case_title}</div></Td>
                    <Td className="font-mono text-xs">{r.filename}</Td><Td className="text-xs">{dt(r.generated_at)}</Td><Td className="text-xs">{r.generated_by}</Td>
                    <Td className="num text-right text-xs">{bytes(r.size_bytes)}</Td><Td className="font-mono text-2xs text-subtle" title={r.sha256}>{r.sha256.slice(0, 12)}…</Td>
                    <Td><Button size="sm" onClick={() => download(`/reports/${r.id}/download`, r.filename)}><Download className="h-3.5 w-3.5" /></Button></Td>
                  </tr>))}</tbody></Table>
            )}
          </QueryState>
        </Card>
      </div>
    </>
  );
}
