// ---- Auth ----

export type Role = "admin" | "operator" | string;

export interface User {
  username: string;
  role: Role;
}

// ---- Status / entities ----

export type EntityStatus =
  | "discovered"
  | "proposed"
  | "reviewed"
  | "deployed"
  | "paused"
  | "disabled"
  | string;

export interface OnboardingEntity {
  id: number;
  source_schema: string;
  source_table: string;
  target_system: string;
  staging_table: string | null;
  status: EntityStatus;
  paused_reason?: string | null;
  deployed_by?: string | null;
  deployed_at?: string | null;
  [key: string]: unknown;
}

export interface OutboxStat {
  source_entity: string;
  status: string;
  events: number;
  oldest: string | null;
}

export interface QueueStat {
  status: string;
  events: number;
  oldest: string | null;
}

export interface EntityControl {
  source_entity: string;
  target_system: string;
  enabled: boolean;
  paused_reason?: string | null;
  updated_at?: string | null;
}

export interface StatusResponse {
  entities: OnboardingEntity[];
  outbox_stats: OutboxStat[];
  queues: QueueStat[];
  entity_controls: EntityControl[];
  unresolved_quarantine: number;
  unresolved_drift: number;
}

// ---- Quarantine / dead letter ----

export interface QuarantineRow {
  event_id: string;
  errors: unknown;
  payload_snapshot: unknown;
  created_at: string;
  source_entity: string;
  outbox_status: string;
  attempts: number;
  operation: string;
  external_reference?: string | null;
}

export interface DeadLetterRow {
  event_id: string;
  source_entity: string;
  external_reference?: string | null;
  operation: string;
  attempts: number;
  last_error_code?: string | null;
  last_error_message?: string | null;
  created_at: string;
  processed_at?: string | null;
}

// ---- Drift ----

export interface DriftReport {
  id: number;
  target_system: string;
  previous_fingerprint: string;
  observed_fingerprint: string;
  differences: unknown;
  impacted_entities: string[];
  breaking: boolean;
  created_at: string;
  resolved_at?: string | null;
}

// ---- Schemas ----

export interface SchemaColumn {
  name: string;
  data_type: string;
  nullable: boolean;
  is_primary_key: boolean;
  description?: string | null;
}

export interface SchemaTable {
  name: string;
  columns: SchemaColumn[];
}

export interface SchemaSystem {
  fingerprint: string;
  system_name: string;
  tables: SchemaTable[];
}

export interface SchemasResponse {
  source: SchemaSystem;
  target: SchemaSystem;
}

// ---- Proposals / mappings ----

export type FieldStatus = "pending" | "accepted" | "rejected" | "resolved" | string;

export interface ProposalField {
  id: number;
  source_column: string;
  suggested_target_table: string | null;
  suggested_target_column: string | null;
  confidence: number;
  transform: string | null;
  reasoning: string | null;
  status: FieldStatus;
  resolved_target_column?: string | null;
  resolved_transform?: string | null;
}

export interface Proposal {
  id: number;
  entity_id: number;
  source_schema: string;
  source_table: string;
  target_system: string;
  status: string;
  source_fingerprint: string;
  target_fingerprint: string;
  unmet_required_columns: string[];
}

export interface ProposalResponse {
  proposal: Proposal;
  fields: ProposalField[];
}

// ---- Audit ----

export interface AuditRow {
  id: number;
  actor: string;
  action: string;
  target_type?: string | null;
  target_id?: string | null;
  reason?: string | null;
  details?: unknown;
  result?: string | null;
  error_message?: string | null;
  performed_at: string;
}

// ---- Snapshots ----

export interface SnapshotsResponse {
  table: string;
  snapshots: string[];
}

// ---- Jobs ----

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled" | string;

export type JobType =
  | "schema_scan"
  | "discover"
  | "propose"
  | "monitor"
  | "worker_run"
  | "reconcile"
  | "deploy"
  | "backfill"
  | "refresh"
  | "migration_apply";

export interface JobSummary {
  id: number;
  job_type: string;
  reason?: string | null;
  requested_by?: string | null;
  status: JobStatus;
  progress_current?: number | null;
  progress_total?: number | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  error_message?: string | null;
}

export interface JobDetail extends JobSummary {
  params?: Record<string, unknown>;
  result?: Record<string, unknown> | null;
}

export interface CreateJobPayload {
  job_type: JobType | string;
  params: Record<string, unknown>;
  reason?: string;
  confirm?: string;
}

export interface CreateJobResponse {
  job_id: number;
  created_at: string;
}

// ---- Worker ----

export interface WorkerStatus {
  running: boolean;
  interval?: number;
  batch_size?: number;
  started_by?: string | null;
  started_at?: string | null;
  last_result?: unknown;
}

// ---- Migrations ----

export interface MigrationRow {
  filename: string;
  exists_on_disk: boolean;
  current_checksum?: string | null;
  applied: boolean;
  applied_at?: string | null;
  applied_by?: string | null;
  checksum_mismatch: boolean;
}

export interface MigrationSqlResponse {
  filename: string;
  dsn: string;
  sql: string;
}

// ---- Users (admin) ----

export interface AdminUser {
  id: number;
  username: string;
  role: Role;
  is_active?: boolean;
  [key: string]: unknown;
}

// ---- Data browser ----

export type DataSide = "source" | "target";

/** One column's metadata as returned by the row-page endpoint. */
export interface DataColumn {
  name: string;
  data_type: string;
  nullable: boolean;
  is_primary_key: boolean;
}

/** A source-side table in the browser rail (counts, not the columns themselves). */
export interface SourceTableSummary {
  table: string;
  columns: number;
  rows: number;
  entity_status: EntityStatus | null;
  staging_table: string | null;
}

/** A staging/target-side table in the browser rail. */
export interface TargetTableSummary {
  table: string;
  columns: number;
  rows: number;
  entity_status: EntityStatus | null;
  source_table: string | null;
}

export interface DataTablesResponse {
  source: { schema: string; tables: SourceTableSummary[] };
  target: { database: string; tables: TargetTableSummary[] };
}

export type DataRow = Record<string, unknown>;

export interface DataRowsResponse {
  side: DataSide;
  table: string;
  columns: DataColumn[];
  rows: DataRow[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

export interface DataRowsParams {
  side: DataSide;
  table: string;
  page?: number;
  size?: number;
  sort?: string;
  direction?: "asc" | "desc";
  sourceSchema?: string;
}

/** One field of a source↔target row comparison. */
export interface CompareField {
  field: string;
  source: unknown;
  target: unknown;
  /** True only when the field is carried on both sides and the values match. */
  matches: boolean;
  /** False for envelope-only columns the target adds that the source never had. */
  compared: boolean;
}

export interface CompareResponse {
  entity: string;
  external_reference: string;
  staging_table: string | null;
  delivery_status: string | null;
  source_row: Record<string, unknown> | null;
  target_row: Record<string, unknown> | null;
  missing_in_target: boolean;
  missing_in_source: boolean;
  fields: CompareField[];
}

// ---- Proposal summaries (review queue) ----

/** A proposal joined to its entity, so the review queue never needs a typed id. */
export interface ProposalSummary {
  proposal_id: number;
  status: string;
  auto_approved_count: number;
  needs_review_count: number;
  rejected_count: number;
  unmet_required_columns: string[];
  created_at: string;
  updated_at: string;
  reviewed_by: string | null;
  entity_id: number;
  source_schema: string;
  source_table: string;
  target_system: string;
  entity_status: EntityStatus | null;
  pending_fields: number;
}
