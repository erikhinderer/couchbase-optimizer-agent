export type ClusterKind = "enterprise" | "capella";
export type ClusterStatus = "unknown" | "connected" | "unreachable";
export type AccessMode = "read_only" | "read_write";
export type ClusterSourceType = "live" | "support_bundle";

export interface ClusterPublic {
  cluster_id: string;
  name: string;
  kind: ClusterKind;
  connection_string: string;
  access_mode: AccessMode;
  granted_roles?: string[] | null;
  access_mode_note?: string | null;
  source_type: ClusterSourceType;
  bundle_filename?: string | null;
  bundle_uploaded_at?: string | null;
  bundle_parse_note?: string | null;
  status: ClusterStatus;
  last_analyzed_at: string | null;
  created_at: string;
}

export interface ClusterCreate {
  name: string;
  kind: ClusterKind;
  connection_string: string;
  username: string;
  password: string;
  access_mode: AccessMode;
  capella_cluster_id?: string;
}

export type FindingCategory = "index" | "query" | "resource" | "configuration" | "storage";
export type FindingSeverity = "info" | "warning" | "critical";
export type ActionType = "safe_auto" | "requires_code_change";
export type FindingStatus =
  | "open" | "sandbox_testing" | "pending_approval" | "approved"
  | "applied" | "apply_failed" | "rejected" | "dismissed" | "suggested";

export interface DocReference {
  title: string;
  url: string;
  snippet?: string | null;
}

export interface SandboxTestResult {
  ran_at: string;
  passed: boolean;
  summary: string;
  detail: Record<string, unknown>;
  fuel_consumed?: number | null;
}

export interface Finding {
  finding_id: string;
  cluster_id: string;
  category: FindingCategory;
  severity: FindingSeverity;
  action_type: ActionType;
  status: FindingStatus;
  title: string;
  description: string;
  evidence: Record<string, unknown>;
  suggested_action?: Record<string, unknown> | null;
  code_change_guidance?: string | null;
  doc_references: DocReference[];
  sandbox_test_result?: SandboxTestResult | null;
  detected_at: string;
  approved_by?: string | null;
  approved_at?: string | null;
  applied_at?: string | null;
  apply_result?: string | null;
}

export interface AnalysisRunSummary {
  cluster_id: string;
  started_at: string;
  finished_at: string;
  findings_created: number;
  findings_updated: number;
  queries_examined: number;
  indexes_examined: number;
}

export interface ClusterSnapshot {
  cluster_id: string;
  queries_examined: number;
  duration_distribution: Record<string, number>;
  scan_type_breakdown: { primary: number; index: number; other: number };
  index_catalog: Array<Record<string, unknown>>;
  top_statements: Array<{ normalized_statement: string; count: number; avg_elapsed_ms: number }>;
  resource_stats: { nodes?: Array<Record<string, unknown>>; buckets?: Array<Record<string, unknown>> };
  bucket_names: string[];
}

export interface ChatResponse {
  reply: string;
  doc_references: DocReference[];
  recalled_memories: number;
}

export interface MemoryItem {
  id?: string;
  tier: string;
  kind: string;
  cluster_id?: string | null;
  text: string;
  payload: Record<string, unknown>;
  created_at: string;
  score?: number;
}
