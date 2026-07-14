import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { approveSchema, getDriftReports, getSchemas } from "../api/client";
import { subscribeJobEvents, type JobEvent } from "../api/sse";
import CopyButton from "../components/CopyButton";
import GuardedActionModal from "../components/GuardedActionModal";
import SchemaTree from "../components/SchemaTree";
import StatusChip from "../components/StatusChip";
import type { SchemaSystem } from "../api/types";
import { useJobRunner } from "../hooks/useJobRunner";
import { label } from "../labels";
import { errMsg, fmtDate, prettyJson, shortFp } from "../utils";

interface ApproveTarget {
  fingerprint: string;
  system: string;
}

interface ResetTarget {
  direction: "source" | "target" | "all";
}

/**
 * "Schema Changes" surface: a single Source ↔ Target comparison of the two
 * live schema fingerprints, with the drift reports recorded between them.
 */
export default function SchemaChanges() {
  const [sourceSchema, setSourceSchema] = useState("irimsv");
  const [approveInitial, setApproveInitial] = useState(false);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [approveTarget, setApproveTarget] = useState<ApproveTarget | null>(null);
  const [resetTarget, setResetTarget] = useState<ResetTarget | null>(null);

  const scan = useJobRunner();
  const monitor = useJobRunner();
  const reset = useJobRunner();
  const resolveDrift = useJobRunner();
  const [showResolveDrift, setShowResolveDrift] = useState(false);

  const schemas = useQuery({
    queryKey: ["schemas", sourceSchema],
    queryFn: () => getSchemas(sourceSchema),
  });
  const drift = useQuery({
    queryKey: ["drift-reports"],
    queryFn: getDriftReports,
    refetchInterval: 30000,
  });

  const leftSystem: SchemaSystem | undefined = schemas.data?.source;
  const rightSystem: SchemaSystem | undefined = schemas.data?.target;

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
    const params = approveInitial ? { approve_initial: true } : {};
    void scan.run({ job_type: "schema_scan", params }).catch(() => undefined);
  };

  const handleReset = (reason: string, typedConfirm?: string) => {
    if (!resetTarget) return;
    const dir = resetTarget.direction;
    const resetSource = dir === "source" || dir === "all";
    const resetTargetFlag = dir === "target" || dir === "all";
    const confirmMap: Record<string, string> = { source: "SOURCE", target: "TARGET", all: "RESET ALL" };
    void reset
      .run({
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

  const approveBtn = (system: SchemaSystem) => (
    <button
      type="button"
      className="btn btn-sm"
      onClick={() => setApproveTarget({ fingerprint: system.fingerprint, system: system.system_name })}
    >
      Approve fingerprint
    </button>
  );

  const driftReports = drift.data ?? [];

  return (
    <div className="page">
      <h2 className="page-title">Schema Changes</h2>

      <h3 className="section-sub" style={{ marginTop: 0, marginBottom: 16 }}>
        Source ↔ Target
      </h3>

      {/* ---- Scan trigger + live progress ---- */}
      <section className="panel">
        <div className="panel-header">
          <h3 className="panel-title">Scan</h3>
          {job && <StatusChip status={job.status} />}
        </div>

        <div className="form-row">
          <label className="field field-inline">
            <span className="field-label">Source schema</span>
            <input
              className="input input-sm mono"
              aria-label="Source schema"
              value={sourceSchema}
              onChange={(e) => setSourceSchema(e.target.value)}
            />
          </label>
          <label className="checkbox-label dim">
            <input
              type="checkbox"
              checked={approveInitial}
              onChange={(e) => setApproveInitial(e.target.checked)}
            />
            approve initial fingerprints
          </label>
          <button type="button" className="btn btn-primary" disabled={scan.running} onClick={runScan}>
            {scan.running ? "Scanning…" : "Scan source → target"}
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

      {/* ---- Two schema trees side by side ---- */}
      {schemas.isError && <div className="alert alert-danger">{errMsg(schemas.error)}</div>}
      {(leftSystem || rightSystem) && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, alignItems: "start" }}>
          {leftSystem && (
            <SchemaTree
              title="Source"
              system={leftSystem}
              headerExtra={approveBtn(leftSystem)}
            />
          )}
          {rightSystem && (
            <SchemaTree
              title="Target (LRMIS)"
              system={rightSystem}
              headerExtra={approveBtn(rightSystem)}
            />
          )}
        </div>
      )}

      {/* ---- Drift reports ---- */}
      <div className="page-title-row">
        <h3 className="section-sub" style={{ margin: 0 }}>
          {label("drift")} · <span className="mono dim">source-&gt;target</span>
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
          <p className="dim">No schema changes recorded.</p>
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
          re-deliver every deployed entity's rows into the target. Use this when schemas have changed
          and you want a clean start without waiting for drift detection.
        </p>

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
              Re-deliver every deployed entity's rows into the <strong>lrmis_target</strong> database.
              Integration data (mappings, proposals) is preserved.
            </span>
          ) : (
            <span>
              <strong>Full reset:</strong> re-scan source fingerprints AND re-deliver every deployed
              entity into the target. Integration metadata is preserved.
            </span>
          )
        }
        confirmString={
          resetTarget?.direction === "source"
            ? "SOURCE"
            : resetTarget?.direction === "target"
              ? "TARGET"
              : "RESET ALL"
        }
        warning={
          <div>
            <p>
              This will affect <strong>all deployed entities</strong>. The operation is idempotent,
              but the data load may take several minutes.
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
        open={showResolveDrift}
        tier="confirm"
        title="Resolve All Drift"
        description={
          <span>
            Re-scan source schemas, re-deliver drifted entities into the target, update fingerprints,
            and re-enable all drifted entities.
          </span>
        }
        warning={
          <div>
            <p>
              This affects all entities with unresolved drift. Data is reloaded from source into the
              target. The operation may take several minutes depending on the number of entities.
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
