import type {
  AdminUser,
  AuditRow,
  SourceTargetCompareResponse,
  CreateJobPayload,
  CreateJobResponse,
  DataRowsParams,
  DataRowsResponse,
  DataTablesResponse,
  DeadLetterRow,
  DriftReport,
  JobDetail,
  JobSummary,
  MigrationRow,
  MigrationSqlResponse,
  ProposalResponse,
  ProposalSummary,
  QuarantineRow,
  SchemasResponse,
  StatusResponse,
  User,
  WorkerStatus,
} from "./types";

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

let unauthorizedHandler: (() => void) | null = null;

/** Register a callback fired whenever any API call returns 401. */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, credentials: "include" };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const res = await fetch(path, init);

  if (res.status === 401) {
    unauthorizedHandler?.();
    throw new ApiError(401, "Not authenticated");
  }
  if (!res.ok) {
    let detail = res.statusText || `HTTP ${res.status}`;
    try {
      const parsed: unknown = await res.json();
      if (parsed && typeof parsed === "object" && "detail" in parsed) {
        const d = (parsed as { detail: unknown }).detail;
        detail = typeof d === "string" ? d : JSON.stringify(d);
      }
    } catch {
      /* body was not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

const get = <T>(path: string) => request<T>("GET", path);
const post = <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {});

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const s = search.toString();
  return s ? `?${s}` : "";
}

// ---- Auth ----

export const login = (username: string, password: string) =>
  post<User>("/api/auth/login", { username, password });

export const logout = () => post<void>("/api/auth/logout");

export const me = () => get<User>("/api/auth/me");

// ---- Status ----

export const getStatus = () => get<StatusResponse>("/api/status");

// ---- Quarantine / dead letter ----

export const getQuarantine = (includeResolved = false) =>
  get<QuarantineRow[]>(`/api/quarantine${qs({ include_resolved: includeResolved })}`);

export const getDeadLetter = () => get<DeadLetterRow[]>("/api/dead-letter");

// ---- Drift ----

export const getDriftReports = () => get<DriftReport[]>("/api/drift-reports");

// ---- Schemas ----

export const getSchemas = (sourceSchema = "irimsv") =>
  get<SchemasResponse>(`/api/schemas${qs({ source_schema: sourceSchema })}`);

// ---- Proposals ----

export const getProposal = (id: number | string) =>
  get<ProposalResponse>(`/api/proposals/${id}`);

/** List proposals (each joined to its entity), optionally filtered by status. */
export const getProposals = (status?: string) =>
  get<ProposalSummary[]>(`/api/proposals${qs({ status })}`);

// ---- Data browser (read-only) ----

export const getDataTables = (sourceSchema = "irimsv") =>
  get<DataTablesResponse>(`/api/data/tables${qs({ source_schema: sourceSchema })}`);

export const getDataRows = (params: DataRowsParams) =>
  get<DataRowsResponse>(
    `/api/data/rows${qs({
      side: params.side,
      table: params.table,
      page: params.page,
      size: params.size,
      sort: params.sort,
      direction: params.direction,
      source_schema: params.sourceSchema,
    })}`,
  );

/** Compare a source row (by primary key) against the exact rows it produced in the LRMIS target. */
export const compareSourceTarget = (entity: string, pk: string) =>
  get<SourceTargetCompareResponse>(
    `/api/data/compare-source-target${qs({ entity, pk })}`,
  );

// ---- Audit ----

export const getAudit = (params: { limit?: number; actor?: string; action?: string } = {}) =>
  get<AuditRow[]>(`/api/audit${qs(params)}`);

// ---- One-click / guarded actions ----

export const replayEvent = (eventId: string) =>
  post<unknown>("/api/actions/replay", { event_id: eventId });

export const toggleEntity = (payload: {
  entity: string;
  target_system: string;
  enabled: boolean;
  reason?: string;
}) => post<unknown>("/api/actions/entity-toggle", payload);

export const approveSchema = (payload: {
  fingerprint: string;
  target_system: string;
  reason: string;
}) => post<unknown>("/api/actions/approve-schema", payload);

export const approveMapping = (payload: { mapping_id: number; reason: string }) =>
  post<unknown>("/api/actions/approve-mapping", payload);

export const resolveMapping = (payload: {
  proposal_id: number;
  source_column: string;
  target_table?: string;
  target_column: string;
  transform: string;
}) => post<unknown>("/api/actions/resolve", payload);

export const getLrmisSchema = () =>
  get<{ tables: Record<string, string[]> }>("/api/lrmis-schema");

// ---- Jobs ----

export const createJob = (payload: CreateJobPayload) =>
  post<CreateJobResponse>("/api/jobs", payload);

export const listJobs = () => get<JobSummary[]>("/api/jobs");

export const getJob = (id: number | string) => get<JobDetail>(`/api/jobs/${id}`);

// ---- Worker ----

export const getWorkerStatus = () => get<WorkerStatus>("/api/worker/status");

export const startWorker = (payload: { interval: number; batch_size: number; reason: string }) =>
  post<unknown>("/api/worker/start", payload);

export const stopWorker = (reason: string) => post<unknown>("/api/worker/stop", { reason });

// ---- Migrations ----

export const listMigrations = () => get<MigrationRow[]>("/api/migrations");

export const getMigrationSql = (filename: string) =>
  get<MigrationSqlResponse>(`/api/migrations/sql${qs({ filename })}`);

export const markMigrationApplied = (payload: { filename: string; reason: string }) =>
  post<unknown>("/api/migrations/mark-applied", payload);

// ---- Users (admin) ----

export const listUsers = () => get<AdminUser[]>("/api/users");

export const createUser = (payload: { username: string; password: string; role: string }) =>
  post<AdminUser>("/api/users", payload);

export const setUserActive = (id: number, isActive: boolean) =>
  post<unknown>(`/api/users/${id}/active`, { is_active: isActive });

// ---- View proposals ----

export interface ViewProposal {
  id: number;
  entity_id: number;
  source_schema: string;
  source_table: string;
  target_system: string;
  view_schema: string;
  view_name: string;
  view_sql: string;
  joined_tables: Array<{ from_table: string; from_col: string; to_table: string; to_col: string }>;
  mapped_columns: Array<{ table: string; column: string; alias: string }>;
  status: string;
  pending_proposal_id?: number;
  created_at: string;
  applied_at?: string;
  applied_by?: string;
}

export const getViewProposals = (status?: string) =>
  get<ViewProposal[]>(`/api/views/proposals${status ? qs({ status }) : ""}`);

export const generateView = (payload: {
  entity_id: number;
  source_schema?: string;
  source_table: string;
  target_system?: string;
}) => post<ViewProposal>("/api/actions/generate-view", payload);

export const applyView = (payload: { proposal_id: number }) =>
  post<{ proposal_id: number; entity_id: number; view_schema: string; view_name: string; status: string; view_sql: string }>(
    "/api/actions/apply-view", payload,
  );

// ---- Cancel queue ----

export const cancelQueue = (entity: string) =>
  post<CreateJobResponse>("/api/jobs", {
    job_type: "cancel_queue",
    params: { entity },
  });

// ---- Recovery (backups + validated uploads + confirmed restores) ----

export interface TargetBackup {
  id: string;
  path: string;
  size_bytes: number;
  modified_at: string;
}

export interface RecoveryValidation {
  ok: boolean;
  reason: string | null;
  format: string | null;
}

export interface RecoveryUpload {
  id: number;
  kind: "source_dump" | "target_backup";
  original_filename: string;
  stored_path: string;
  checksum: string;
  size_bytes: number;
  valid: boolean;
  invalid_reason: string | null;
  uploaded_by: string;
  uploaded_at: string;
  used_at: string | null;
  used_by: string | null;
  /** Present on the row returned directly from an upload. */
  validation?: RecoveryValidation;
}

export interface RecoveryBackupsResponse {
  target_backups: TargetBackup[];
  uploads: RecoveryUpload[];
}

export interface RestoreResult {
  executed: boolean;
  dry_run?: boolean;
  command?: string;
  [key: string]: unknown;
}

export const getRecoveryBackups = () =>
  get<RecoveryBackupsResponse>("/api/recovery/backups");

/**
 * Multipart upload with real progress (XHR — fetch has no upload progress).
 * The server streams it to a quarantined path and returns the validation
 * verdict on the row; a rejected file still resolves (valid=false + reason).
 */
export function uploadRecoveryFile(
  file: File,
  kind: RecoveryUpload["kind"],
  onProgress?: (fraction: number) => void,
): Promise<RecoveryUpload> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/recovery/upload");
    xhr.withCredentials = true;
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      if (xhr.status === 401) {
        unauthorizedHandler?.();
        reject(new ApiError(401, "Not authenticated"));
        return;
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        let detail = xhr.statusText || `HTTP ${xhr.status}`;
        try {
          const parsed: unknown = JSON.parse(xhr.responseText);
          if (parsed && typeof parsed === "object" && "detail" in parsed) {
            const d = (parsed as { detail: unknown }).detail;
            detail = typeof d === "string" ? d : JSON.stringify(d);
          }
        } catch {
          /* body was not JSON */
        }
        reject(new ApiError(xhr.status, detail));
        return;
      }
      resolve(JSON.parse(xhr.responseText) as RecoveryUpload);
    };
    xhr.onerror = () => reject(new ApiError(0, "network error during upload"));
    const form = new FormData();
    form.set("kind", kind);
    form.set("file", file);
    xhr.send(form);
  });
}

export const restoreTarget = (payload: {
  backup_id: string;
  confirm: string;
  reason: string;
  dry_run?: boolean;
}) => post<RestoreResult>("/api/recovery/restore-target", payload);

export const restoreSource = (payload: {
  upload_id: number;
  confirm: string;
  reason: string;
  dry_run?: boolean;
}) => post<RestoreResult>("/api/recovery/restore-source", payload);
