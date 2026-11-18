export type Role = "admin" | "investigator" | "reviewer";
export interface User { id: number; email: string; full_name: string; role: Role; is_active: boolean; created_at: string; last_login_at: string | null; permissions: string[] }
export type RiskLevel = "low" | "medium" | "high" | "critical";
export interface Account {
  id: number; account_number: string; bank: string | null; holder_name: string | null; entity_id: number | null;
  first_seen: string | null; last_seen: string | null; txn_count: number; total_in: number; total_out: number;
  risk_score: number; risk_level: RiskLevel; alert_count: number; patterns: string[]; entity_type?: string | null;
}
export interface Transaction {
  id: number; txn_id: string; sender_account_id: number; receiver_account_id: number; sender_account: string; receiver_account: string;
  sender_name: string | null; receiver_name: string | null; amount: number; currency: string; timestamp: string; txn_type: string | null;
  bank: string | null; reference: string | null; ip_address: string | null; status: string | null; import_batch_id: number | null;
  data_warnings: string[]; risk_score: number; risk_level: RiskLevel; anomaly_score: number | null; is_flagged: boolean; patterns: string[];
}
export interface Alert {
  id: number; run_id: number; kind: "rule" | "model"; pattern: string; pattern_label: string; severity: RiskLevel; confidence: number;
  score: number; title: string; reasons: string[]; details: Record<string, unknown> & { account_ids?: number[] }; account_id: number | null;
  transaction_ids: number[]; first_txn_at: string | null; last_txn_at: string | null; total_amount: number;
  review_status: "new" | "acknowledged" | "dismissed" | "escalated"; reviewed_at: string | null; created_at: string;
  account: { id: number; account_number: string; holder: string | null; risk_score: number } | null;
}
export interface Paged<T> { total: number; items: T[] }
export interface DetectionRun {
  id: number; status: string; params: Record<string, number>; error: string | null; started_at: string; completed_at: string | null;
  stats: { transactions?: number; analysed?: number; alerts?: number; rule_alerts?: number; model_alerts?: number; flagged_transactions?: number;
    high_risk_accounts?: number; flagged_value?: number; alerts_by_pattern?: Record<string, number>; duration_sec?: number; timings_sec?: Record<string, number>; excluded_by_status?: number };
  evaluation: Evaluation | null;
}
export interface PRF { tp: number; fp: number; fn: number; precision: number; recall: number; f1: number }
export interface Evaluation {
  labelled_transactions: number; positives: number; flagged: PRF; any_rule_hit: PRF; model_outlier_only: PRF;
  per_pattern_recall: Record<string, { labelled: number; flagged: number; recall: number }>; model_roc_auc?: number;
  model_average_precision?: number; risk_roc_auc?: number; note: string;
}
export interface CaseSummary {
  id: number; case_number: string; title: string; summary: string; objectives: string; status: string; priority: string;
  created_by: string | null; created_at: string; updated_at: string; closed_at: string | null;
  assignees: { id: number; full_name: string; role: Role }[];
  counts: Record<"accounts" | "transactions" | "evidence" | "notes" | "findings" | "reports", number>;
  progress: { percent: number; steps: { label: string; done: boolean }[] };
}
export interface Evidence {
  id: number; evidence_code: string; case_id: number; case_number: string | null; original_filename: string; content_type: string; file_type: string;
  category: string; size_bytes: number; sha256: string; description: string; verification_status: "unverified" | "verified" | "failed";
  last_verified_at: string | null; uploaded_by: string | null; uploaded_at: string;
}
export interface ReportItem { id: number; case_id: number; format: string; filename: string; sha256: string; size_bytes: number; generated_at: string; generated_by: string | null; case_number?: string; case_title?: string }
export interface CaseDetail extends CaseSummary {
  accounts: (Account & { case_role: string; note: string | null; added_at: string })[];
  transactions: Transaction[];
  notes: { id: number; body: string; author: string | null; created_at: string }[];
  findings: { id: number; title: string; description: string; status: string; author: string | null; created_at: string; related_alert_ids: number[] }[];
  events: { id: number; event_type: string; description: string; actor: string | null; created_at: string }[];
  evidence: Evidence[]; reports: ReportItem[]; allowed_transitions: string[];
}
export interface TimelineEvent {
  kind: "transaction" | "detection" | "evidence" | "note" | "case"; id: string; timestamp: string; title: string; description?: string;
  amount?: number; risk_score?: number; risk_level?: RiskLevel; flagged?: boolean; patterns?: string[]; severity?: RiskLevel; confidence?: number;
  pattern?: string; account_ids?: number[]; in_case?: boolean; actor?: string | null; ref: Record<string, unknown>;
}
