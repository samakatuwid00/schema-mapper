import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { approveSchema, getDriftReports, getSchemas, getStatus } from "../api/client";
import { subscribeJobEvents, type JobEvent } from "../api/sse";
import CopyButton from "../components/CopyButton";
import GuardedActionModal from "../components/GuardedActionModal";
import SchemaTree from "../components/SchemaTree";
import StatusChip from "../components/StatusChip";
import type { JobDetail, SchemaSystem } from "../api/types";
import { useJobRunner } from "../hooks/useJobRunner";
import { label } from "../labels";
import { errMsg, fmtDate, prettyJson, shortFp } from "../utils";

interface ApproveTarget {
  fingerprint: string;
  system: string;
}

interface ResetTarget {
  direction: "source" | "target" | "all" | "path_b";
}

type View = "source_staging" | "staging_target";

const VIEWS: {
  key: View;
  title: string;
  leftKey: "source" | "staging";
  rightKey: "staging" | "target_b";
  leftLabel: string;
  rightLabel: string;
  driftPair: string;
}[] = [
  {
    key: "source_staging",
    title: "Source ↔ Staging",
    leftKey: "source",
    rightKey: "staging",
    leftLabel: "Source",
    rightLabel: "Staging",
    driftPair: "source->staging",
  },
  {
    key: "staging_target",
    title: "Staging ↔ Target",
    leftKey: "staging",
    rightKey: "target_b",
    leftLabel: "Staging",
    rightLabel: "Target (Path B)",
    driftPair: "staging->target",
  },
];

const RESULT_CAP = 20;

function asStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

/** A named list of result items, truncated at RESULT_CAP with an overflow note. */
function ResultList({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  const shown = items.slice(0, RESULT_CAP);
  const extra = items.length - shown.length;
  return (
    <div style={{ marginTop: 6 }}>
      <span className="dim">{label} ({items.length}):</span>{" "}
      {shown.map((t) => (
        <span key={t} className="badge badge-type mono" style={{ marginRight: 4 }}>
          {t}
        </span>
      ))}
      {extra > 0 && <span className="dim">… and {extra} more</span>}
    </div>
  );
}

/** Human-readable summary of a completed sweep / retire job's structured result. */
function CleanupResultView({ job }: { job: JobDetail }) {
  const result = (job.result ?? null) as Record<string, unknown> | null;
  if (!result) return <div className="alert alert-ok">Cleanup job complete — job #{job.id}.</div>;

  if (job.job_type === "sweep_staging") {
    const found = asStrings(result.orphans_found);
    const dropped = asStrings(result.dropped);
    const snapshots = asStrings(result.snapshots);
    const dry = Boolean(result.dry_run);
    return (
      <div className="alert alert-ok">
        <strong>Sweep complete{dry ? " (dry run)" : ""}</strong> — {found.length} orphan
        {found.length === 1 ? "" : "s"} found, {dropped.length} dropped, {snapshots.length} snapshot
        {snapshots.length === 1 ? "" : "s"} taken.
        <ResultList label="Orphans found" items={found} />
        <ResultList label="Dropped" items={dropped} />
        <ResultList label="Snapshots" items={snapshots} />
      </div>
    );
  }

  if (job.job_type === "retire_entity") {
    const source = result.source_table ? String(result.source_table) : "—";
    const staging = result.staging_table ? String(result.staging_table) : "—";
    return (
      <div className="alert alert-ok">
        <strong>Retire complete</strong> — entity #{String(result.entity_id)} (
        <span className="mono">{source}</span> <span className="lane-arrow">→</span>{" "}
        <span className="mono">{staging}</span>),{" "}
        {result.dropped ? "staging table dropped" : "staging table not dropped"}
        {result.snapshot ? (
          <>
            , snapshot <span className="mono">{String(result.snapshot)}</span>
          </>
        ) : null}
        .
      </div>
    );
  }

  return <div className="alert alert-ok">Cleanup job complete — job #{job.id}.</div>;
}

/**
 * "Schema Changes" surface as two side-by-side schema comparisons:
 * Source ↔ Staging, then (via Next) Staging ↔ Target (Path B). Each view's
 * drift reports are filtered to the matching drift pair.
 */
export default function SchemaChanges() {
  const [view, setView] = useState<View>("source_staging");
  const [sourceSchema, setSourceSchema] = useState("irimsv");
  const [stagingTable, setStagingTable] = useState("");
  const [approveInitial, setApproveInitial] = useState(false);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [approveTarget, setApproveTarget] = useState<ApproveTarget | null>(null);
  const [resetTarget, setResetTarget] = useState<ResetTarget | null>(null);
  const [retireId, setRetireId] = useState("");
  const [retireTarget, setRetireTarget] = useState<{ entityId: string } | null>(null);
  const [sweepTarget, setSweepTarget] = useState(false);

  const scan = useJobRunner();
  const monitor = useJobRunner();
  const reset = useJobRunner();
  const cleanup = useJobRunner();
  const resolveDrift = useJobRunner();
  const [showResolveDrift, setShowResolveDrift] = useState(false);

  const viewIndex = VIEWS.findIndex((v) => v.key === view);
  const active = VIEWS[viewIndex];
  const leftKey = active.leftKey;
  const scanningSource = leftKey === "source";

  const schemas = useQuery({
    queryKey: ["schemas", sourceSchema],
    queryFn: () => getSchemas(sourceSchema),
  });
  const drift = useQuery({
    queryKey: ["drift-reports"],
    queryFn: getDriftReports,
    refetchInterval: 30000,
  });
  // Deployed entities feed the Retire dropdown (id + source→staging labels).
  const status = useQuery({ queryKey: ["status"], queryFn: getStatus });
  const deployedEntities = useMemo(
    () => (status.data?.entities ?? []).filter((e) => e.status === "deployed"),
    [status.data],
  );

  const leftSystem: SchemaSystem | undefined = schemas.data?.[leftKey];
  const rightSystem: SchemaSystem | undefined = schemas.data?.[active.rightKey];

  // Live event feed for the active scan job.
  useEffect(() => {
    if (scan.jobId === null) return;
    setEvents([]);
    const handle = subscribeJobEvents(`/api/jobs/${scan.jobId}/events`, (event) => {
      setEvents((prev) => [...prev.slice(-99), event]);
    });
    return () => handle.close();
  }, [scan.jobId]);

  // A finished scan or reset may change both the trees and the drift list.
  useEffect(() => {
    if (scan.job?.status === "succeeded" || reset.job?.status === "succeeded") {
      void schemas.refetch();
      void drift.refetch();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scan.job?.status, reset.job?.status]);

  const runScan = () => {
    const params = scanningSource
      ? { mode: "source", ...(approveInitial ? { approve_initial: true } : {}) }
      : { mode: "staging", staging_table: stagingTable };
    void scan.run({ job_type: "schema_scan", params }).catch(() => undefined);
  };

  const handleReset = (reason: string, typedConfirm?: string) => {
    if (!resetTarget) return;
    const dir = resetTarget.direction;
    const resetSource = dir === "source" || dir === "all";
    const resetTargetFlag = dir === "target" || dir === "all";
    const confirmMap: Record<string, string> = { source: "SOURCE", target: "TARGET", all: "RESET ALL", path_b: "PATH B" };
    void reset
      .run(dir === "path_b"
        ? { job_type: "reset_path_b", params: {}, reason, confirm: typedConfirm ?? "PATH B" }
        : {
            job_type: "reset_schemas",
            params: { reset_source: resetSource, reset_target: resetTargetFlag },
            reason,
            confirm: typedConfirm ?? confirmMap[dir] ?? "RESET ALL",
          })
      .then(() => setResetTarget(null))
      .catch(() => undefined);
  };

  const approve = useMutation({
    mutationFn: (args: { target: ApproveTarget; reason: string }) =>
      approveSchema({
        fingerprint: args.target.fingerprint,
        target_system: args.target.system,
        reason: args.reason,
      }),
    onSuccess: () => setApproveTarget(null),
  });

  const job = scan.job;
  const result = job?.result ?? null;
  const pausedEntities = Array.isArray(result?.paused_entities)
    ? (result.paused_entities as unknown[])
    : [];

  const approveBtn = (sideKey: "source" | "staging" | "target_b", system: SchemaSystem) =>
    sideKey !== "target_b" ? (
      <button
        type="button"
        className="btn btn-sm"
        onClick={() => setApproveTarget({ fingerprint: system.fingerprint, system: system.system_name })}
      >
        Approve fingerprint
      </button>
    ) : null;

  const driftReports = useMemo(() => {
    const list = drift.data ?? [];
    return list.filter((r) => (r.drift_pair ?? "source->staging") === active.driftPair);
  }, [drift.data, active.driftPair]);

  return (
    <div className="page">
      <h2 className="page-title">Schema Changes</h2>

      {/* ---- View navigation ---- */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 16,
        }}
      >
        <h3 className="section-sub" style={{ margin: 0 }}>
          {active.title}
        </h3>
        <div className="pipeline">
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={viewIndex === 0}
            aria-label="Previous view"
            onClick={() => setView(VIEWS[Math.max(0, viewIndex - 1)].key)}
          >
            <ArrowLeft size={14} strokeWidth={2} aria-hidden="true" /> Back
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={viewIndex === VIEWS.length - 1}
            aria-label="Next view"
            onClick={() => setView(VIEWS[Math.min(VIEWS.length - 1, viewIndex + 1)].key)}
          >
            Next <ArrowRight size={14} strokeWidth={2} aria-hidden="true" />
          </button>
        </div>
      </div>

      {/* ---- Scan trigger + live progress ---- */}
      <section className="panel">
        <div className="panel-header">
          <h3 className="panel-title">Scan</h3>
          {job && <StatusChip status={job.status} />}
        </div>

        <div className="form-row">
          <label className="field field-inline">
            <span className="field-label">{scanningSource ? "Source schema" : "Staging schema"}</span>
            {scanningSource ? (
              <input
                className="input input-sm mono"
                aria-label="Source schema"
                value={sourceSchema}
                onChange={(e) => setSourceSchema(e.target.value)}
              />
            ) : (
              <input
                className="input input-sm mono"
                aria-label="Staging schema"
                placeholder="staging table to inspect"
                value={stagingTable}
                onChange={(e) => setStagingTable(e.target.value)}
              />
            )}
          </label>
          {scanningSource && (
            <label className="checkbox-label dim">
              <input
                type="checkbox"
                checked={approveInitial}
                onChange={(e) => setApproveInitial(e.target.checked)}
              />
              approve initial fingerprints
            </label>
          )}
          <button type="button" className="btn btn-primary" disabled={scan.running} onClick={runScan}>
            {scan.running
              ? "Scanning…"
              : scanningSource
                ? "Scan source → staging"
                : "Scan staging → target"}
          </button>
        </div>
        {scan.submitError && <div className="form-error">{scan.submitError}</div>}

        {job && (job.status === "queued" || job.status === "running") && (
          <div className="scan-progress">
            <div className="progress">
              <div
                className={`progress-fill${!job.progress_total ? " indeterminate" : ""}`}
                style={{
                  width: job.progress_total
                    ? `${Math.min(100, ((job.progress_current ?? 0) / job.progress_total) * 100)}%`
                    : "100%",
                }}
              />
            </div>
          </div>
        )}

        {events.length > 0 && (
          <div className="event-log">
            {events.map((event, i) => (
              <div key={i} className="event-log-row">
                <span className="dim mono">{event.created_at ? fmtDate(event.created_at) : ""}</span>
                <StatusChip status={event.type} />
                <span>{event.message ?? ""}</span>
              </div>
            ))}
          </div>
        )}

        {job?.status === "failed" && (
          <div className="alert alert-danger">Scan failed: {job.error_message ?? "unknown error"}</div>
        )}

        {job?.status === "succeeded" && (
          <div className="scan-result">
            {pausedEntities.length > 0 && (
              <div className="alert alert-danger">
                <strong>
                  {pausedEntities.length} entit{pausedEntities.length === 1 ? "y" : "ies"} paused by this
                  scan:
                </strong>{" "}
                <span className="mono">{pausedEntities.map(String).join(", ")}</span>
              </div>
            )}
            {result && (
              <details>
                <summary className="dim">Scan result</summary>
                <pre className="json-block">{prettyJson(result)}</pre>
              </details>
            )}
          </div>
        )}
      </section>

      {/* ---- Two schema trees side by side for the active view ---- */}
      {schemas.isError && <div className="alert alert-danger">{errMsg(schemas.error)}</div>}
      {(leftSystem || rightSystem) && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, alignItems: "start" }}>
          {leftSystem && (
            <SchemaTree
              title={active.leftLabel}
              system={leftSystem}
              headerExtra={approveBtn(active.leftKey, leftSystem)}
            />
          )}
          {rightSystem && (
            <SchemaTree
              title={active.rightLabel}
              system={rightSystem}
              headerExtra={approveBtn(active.rightKey, rightSystem)}
            />
          )}
        </div>
      )}

      {/* ---- Drift reports (filtered to the active view's pair) ---- */}
      <div className="page-title-row">
        <h3 className="section-sub" style={{ margin: 0 }}>
          {label("drift")} · <span className="mono dim">{active.driftPair}</span>
        </h3>
        <button
          type="button"
          className="btn"
          disabled={monitor.running}
          onClick={() => void monitor.run({ job_type: "monitor", params: {} }).catch(() => undefined)}
        >
          {monitor.running ? "Checking…" : "Run drift check now"}
        </button>
        {driftReports.filter((r) => !r.resolved_at).length > 0 && (
          <button
            type="button"
            className="btn btn-danger"
            style={{ marginLeft: "0.5rem" }}
            disabled={resolveDrift.running}
            onClick={() => setShowResolveDrift(true)}
          >
            {resolveDrift.running ? "Resolving…" : `Resolve All (${driftReports.filter((r) => !r.resolved_at).length})`}
          </button>
        )}
      </div>
      {monitor.submitError && <div className="form-error">{monitor.submitError}</div>}
      {monitor.job?.status === "succeeded" && (
        <div className="alert alert-ok">Drift check complete — job #{monitor.job.id}.</div>
      )}
      {resolveDrift.submitError && <div className="form-error">{resolveDrift.submitError}</div>}
      {resolveDrift.job?.status === "succeeded" && (
        <div className="alert alert-ok">Drift resolved — job #{resolveDrift.job.id}.</div>
      )}

      {drift.isLoading && <p className="dim">Loading…</p>}
      {drift.isError && <div className="alert alert-danger">{errMsg(drift.error)}</div>}
      {!drift.isLoading && driftReports.length === 0 && (
        <div className="panel">
          <p className="dim">
            No <span className="mono">{active.driftPair}</span> schema changes recorded.
          </p>
        </div>
      )}

      <div className="table-scroll">
      {driftReports.map((report) => (
        <section
          key={report.id}
          className={`panel drift-card${report.breaking && !report.resolved_at ? " drift-breaking" : ""}`}
        >
          <div className="panel-header">
            <h3 className="panel-title">
              #{report.id} · <span className="mono">{report.target_system}</span>
            </h3>
            <div className="panel-header-actions">
              {report.drift_pair && <span className="badge badge-type mono">{report.drift_pair}</span>}
              {report.breaking && <StatusChip status="breaking" label="breaking" />}
              <StatusChip
                status={report.resolved_at ? "resolved" : "pending"}
                label={report.resolved_at ? "resolved" : "unresolved"}
              />
            </div>
          </div>

          <dl className="kv">
            <div>
              <dt>Detected</dt>
              <dd>{fmtDate(report.created_at)}</dd>
            </div>
            {report.resolved_at && (
              <div>
                <dt>Resolved</dt>
                <dd>{fmtDate(report.resolved_at)}</dd>
              </div>
            )}
            <div>
              <dt>Fingerprint</dt>
              <dd className="fingerprint-row">
                <span className="mono dim" title={report.previous_fingerprint}>
                  {shortFp(report.previous_fingerprint)}
                </span>
                <CopyButton text={report.previous_fingerprint} />
                <span className="lane-arrow">→</span>
                <span className="mono" title={report.observed_fingerprint}>
                  {shortFp(report.observed_fingerprint)}
                </span>
                <CopyButton text={report.observed_fingerprint} />
              </dd>
            </div>
            <div>
              <dt>Impacted entities</dt>
              <dd>
                {report.impacted_entities.length === 0 && <span className="dim">none</span>}
                {report.impacted_entities.map((entity) => (
                  <span key={entity} className="badge badge-entity mono">
                    {entity}
                  </span>
                ))}
              </dd>
            </div>
          </dl>

          <details>
            <summary className="dim">Differences</summary>
            <pre className="json-block">{prettyJson(report.differences)}</pre>
          </details>
        </section>
      ))}
      </div>

      {/* ---- Drop & Restore ---- */}
      <section className="panel">
        <div className="panel-header">
          <h3 className="panel-title">Drop &amp; Restore</h3>
          {reset.job && <StatusChip status={reset.job.status} />}
        </div>
        <p className="dim" style={{ marginBottom: "0.75rem" }}>
          Manually reset source fingerprints (re-scan all entities from the source system) or
          truncate and repopulate target staging tables. Use this when schemas have changed and you
          want a clean start without waiting for drift detection.
        </p>

        {/* Staging table cleanup: stop unbounded growth of lrmis_staging */}
        <div className="form-row" style={{ marginBottom: 12 }}>
          <label className="field field-inline">
            <span className="field-label">Entity to retire</span>
            <select
              className="input input-sm mono"
              aria-label="Entity to retire"
              value={retireId}
              onChange={(e) => setRetireId(e.target.value)}
            >
              <option value="">
                {status.isLoading
                  ? "Loading entities…"
                  : deployedEntities.length === 0
                    ? "No deployed entities"
                    : "Select an entity…"}
              </option>
              {deployedEntities.map((e) => (
                <option key={e.id} value={String(e.id)}>
                  {e.source_table} → {e.staging_table ?? "—"} ({e.status})
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="btn btn-danger"
            disabled={cleanup.running || !/^\d+$/.test(retireId.trim())}
            onClick={() => setRetireTarget({ entityId: retireId.trim() })}
          >
            Retire entity
          </button>
          <button
            type="button"
            className="btn btn-danger"
            style={{ marginLeft: "0.5rem" }}
            disabled={cleanup.running}
            onClick={() => setSweepTarget(true)}
          >
            Sweep orphan staging tables
          </button>
        </div>
        {cleanup.submitError && <div className="form-error">{cleanup.submitError}</div>}
        {cleanup.job?.status === "succeeded" && <CleanupResultView job={cleanup.job} />}
        {cleanup.job?.status === "failed" && (
          <div className="alert alert-danger">
            Cleanup failed: {cleanup.job.error_message ?? "unknown error"}
          </div>
        )}

        {reset.submitError && <div className="form-error">{reset.submitError}</div>}
        {reset.job?.status === "failed" && (
          <div className="alert alert-danger">Reset failed: {reset.job.error_message ?? "unknown error"}</div>
        )}
        <div className="form-row">
          <button
            type="button"
            className="btn btn-danger"
            disabled={reset.running}
            onClick={() => setResetTarget({ direction: "source" })}
          >
            Drop &amp; Restore Source
          </button>
          <button
            type="button"
            className="btn btn-danger"
            disabled={reset.running}
            onClick={() => setResetTarget({ direction: "target" })}
          >
            Drop &amp; Restore Target
          </button>
          <button
            type="button"
            className="btn btn-danger"
            disabled={reset.running}
            onClick={() => setResetTarget({ direction: "all" })}
          >
            Drop &amp; Restore All
          </button>
          <button
            type="button"
            className="btn btn-danger"
            style={{ marginLeft: "0.5rem" }}
            disabled={reset.running}
            onClick={() => setResetTarget({ direction: "path_b" })}
          >
            Drop &amp; Restore Path B
          </button>
        </div>
      </section>

      <GuardedActionModal
        open={resetTarget !== null}
        tier="typed"
        title="Drop &amp; Restore Schemas"
        description={
          resetTarget?.direction === "source" ? (
            <span>
              Clear all stored <strong>source fingerprints</strong> and re-scan every deployed entity
              from the IRIMSV source system. Integration data (mappings, proposals) is preserved.
            </span>
          ) : resetTarget?.direction === "target" ? (
            <span>
              Drop, recreate, and reload every deployed entity's <strong>target staging table</strong>.
              Each table is snapshotted before the drop so you can restore if needed.
            </span>
          ) : resetTarget?.direction === "path_b" ? (
            <span>
              Drop and recreate the <strong>lrmis_target</strong> (Path B) canonical database from the
              schema DDL — all 51 canonical tables + delivery_audit, lookup data reseeded. Pipeline
              integration metadata (mappings, proposals) is preserved.
            </span>
          ) : (
            <span>
              <strong>Full reset:</strong> re-scan source fingerprints AND recreate all target staging
              tables. Integration metadata is preserved.
            </span>
          )
        }
        confirmString={
          resetTarget?.direction === "source"
            ? "SOURCE"
            : resetTarget?.direction === "target"
              ? "TARGET"
              : resetTarget?.direction === "path_b"
                ? "PATH B"
                : "RESET ALL"
        }
        warning={
          <div>
            <p>
              This will affect <strong>all deployed entities</strong>. The operation is idempotent
              and staging tables are snapshotted before being dropped, but the data load may take
              several minutes.
            </p>
          </div>
        }
        danger
        busy={reset.running}
        error={reset.submitError}
        onConfirm={handleReset}
        onClose={() => setResetTarget(null)}
      />

      <GuardedActionModal
        open={approveTarget !== null}
        tier="confirm"
        title={`Approve schema fingerprint — ${approveTarget?.system ?? ""}`}
        description={
          <span>
            Mark fingerprint <code className="mono">{approveTarget?.fingerprint}</code> as the approved
            schema for <code className="mono">{approveTarget?.system}</code>.
          </span>
        }
        actionLabel="Approve"
        busy={approve.isPending}
        error={approve.isError ? errMsg(approve.error) : null}
        onConfirm={(reason) => {
          if (approveTarget) approve.mutate({ target: approveTarget, reason });
        }}
        onClose={() => setApproveTarget(null)}
      />

      <GuardedActionModal
        open={retireTarget !== null}
        tier="typed"
        title={`Retire entity #${retireTarget?.entityId ?? ""}`}
        description={
          <span>
            Drop the staging table for entity{" "}
            <code className="mono">{retireTarget?.entityId}</code> (snapshotted first) and mark the
            entity <strong>disabled</strong>. This stops all delivery for that entity.
          </span>
        }
        confirmString={`RETIRE ${retireTarget?.entityId ?? ""}`}
        warning={
          <div>
            <p>
              The entity's <code className="mono">irimsv_*_staging</code> table is renamed aside
              (snapshot) and dropped. Delivery for this entity stops. It is restorable only via the
              snapshot.
            </p>
          </div>
        }
        actionLabel="Retire"
        danger
        busy={cleanup.running}
        error={cleanup.submitError}
        onConfirm={(reason) => {
          if (!retireTarget) return;
          const id = retireTarget.entityId;
          void cleanup
            .run({
              job_type: "retire_entity",
              params: { entity_id: Number(id), dry_run: false },
              reason,
              confirm: `RETIRE ${id}`,
            })
            .then(() => {
              setRetireTarget(null);
              setRetireId("");
            })
            .catch(() => undefined);
        }}
        onClose={() => setRetireTarget(null)}
      />

      <GuardedActionModal
        open={sweepTarget}
        tier="typed"
        title="Sweep orphan staging tables"
        description={
          <span>
            Drop every <code className="mono">irimsv_*_staging</code> table in{" "}
            <code className="mono">lrmis_staging</code> that is not referenced by a currently{" "}
            <strong>deployed</strong> entity. Each is snapshotted before dropping.
          </span>
        }
        confirmString="SWEEP STAGING"
        warning={
          <div>
            <p>
              Orphaned staging tables left behind by retired or removed entities are removed. They
              are renamed aside (snapshot) first so they can be restored if needed.
            </p>
          </div>
        }
        actionLabel="Sweep"
        danger
        busy={cleanup.running}
        error={cleanup.submitError}
        onConfirm={(reason) => {
          void cleanup
            .run({
              job_type: "sweep_staging",
              params: { dry_run: false },
              reason,
              confirm: "SWEEP STAGING",
            })
            .then(() => setSweepTarget(false))
            .catch(() => undefined);
        }}
        onClose={() => setSweepTarget(false)}
      />

      <GuardedActionModal
        open={showResolveDrift}
        tier="confirm"
        title="Resolve All Drift"
        description={
          <span>
            Re-scan source schemas, drop and recreate staging tables, update fingerprints, and
            re-enable all drifted entities. Each staging table is snapshotted before dropping.
          </span>
        }
        warning={
          <div>
            <p>
              This affects all entities with unresolved drift. Staging tables are dropped and
              recreated — data is reloaded from source. The operation may take several minutes
              depending on the number of entities.
            </p>
          </div>
        }
        actionLabel="Resolve All"
        danger
        busy={resolveDrift.running}
        error={resolveDrift.submitError}
        onConfirm={(reason) => {
          void resolveDrift
            .run({ job_type: "resolve_drift", params: {}, reason })
            .then(() => {
              setShowResolveDrift(false);
              void drift.refetch();
            })
            .catch(() => undefined);
        }}
        onClose={() => setShowResolveDrift(false)}
      />
    </div>
  );
}
