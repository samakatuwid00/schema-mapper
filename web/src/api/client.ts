import type {
  AdminUser,
  AuditRow,
  CompareResponse,
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
  SnapshotsResponse,
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

export const compareRow = (entity: string, externalReference: string) =>
  get<CompareResponse>(
    `/api/data/compare${qs({ entity, external_reference: externalReference })}`,
  );

// ---- Audit ----

export const getAudit = (params: { limit?: number; actor?: string; action?: string } = {}) =>
  get<AuditRow[]>(`/api/audit${qs(params)}`);

// ---- Snapshots ----

export const getSnapshots = (table: string) =>
  get<SnapshotsResponse>(`/api/snapshots/${encodeURIComponent(table)}`);

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
  target_column: string;
  transform: string;
}) => post<unknown>("/api/actions/resolve", payload);

export const restoreSnapshot = (payload: { table: string; snapshot?: string; reason: string }) =>
  post<unknown>("/api/actions/restore-snapshot", payload);

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
