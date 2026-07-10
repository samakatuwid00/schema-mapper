import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { approveSchema, getDriftReports, getSchemas } from "../api/client";
import { subscribeJobEvents, type JobEvent } from "../api/sse";
import CopyButton from "../components/CopyButton";
import GuardedActionModal from "../components/GuardedActionModal";
import SchemaTree from "../components/SchemaTree";
import StatusChip from "../components/StatusChip";
import { useJobRunner } from "../hooks/useJobRunner";
import { label } from "../labels";
import { errMsg, fmtDate, prettyJson, shortFp } from "../utils";

interface ApproveTarget {
  fingerprint: string;
  system: string;
}

/**
 * Merged "Schema Changes" surface: the schema scan + trees on top, and the
 * drift reports the scan produces underneath — one route for the whole
 * "did the schema move, and if so what broke" question.
 */
export default function SchemaChanges() {
  const [sourceSchema, setSourceSchema] = useState("irimsv");
  const [approveInitial, setApproveInitial] = useState(false);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [approveTarget, setApproveTarget] = useState<ApproveTarget | null>(null);

  const scan = useJobRunner();
  const monitor = useJobRunner();

  const schemas = useQuery({
    queryKey: ["schemas", sourceSchema],
    queryFn: () => getSchemas(sourceSchema),
  });
  const drift = useQuery({
    queryKey: ["drift-reports"],
    queryFn: getDriftReports,
    refetchInterval: 30000,
  });

  // Live event feed for the active scan job.
  useEffect(() => {
    if (scan.jobId === null) return;
    setEvents([]);
    const handle = subscribeJobEvents(`/api/jobs/${scan.jobId}/events`, (event) => {
      setEvents((prev) => [...prev.slice(-99), event]);
    });
    return () => handle.close();
  }, [scan.jobId]);

  // A finished scan may change both the trees and the drift list.
  useEffect(() => {
    if (scan.job?.status === "succeeded") {
      void schemas.refetch();
      void drift.refetch();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scan.job?.status]);

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

  return (
    <div className="page">
      <h2 className="page-title">Schema Changes</h2>

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
          <button
            type="button"
            className="btn btn-primary"
            disabled={scan.running}
            onClick={() =>
              void scan
                .run({
                  job_type: "schema_scan",
                  params: approveInitial ? { approve_initial: true } : {},
                })
                .catch(() => undefined)
            }
          >
            {scan.running ? "Scanning…" : "Scan schema now"}
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
            {typeof job.progress_total === "number" && job.progress_total > 0 && (
              <span className="dim mono">
                {job.progress_current ?? 0}/{job.progress_total}
              </span>
            )}
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

      {/* ---- Schema trees ---- */}
      {schemas.isError && <div className="alert alert-danger">{errMsg(schemas.error)}</div>}
      {schemas.data && (
        <div className="two-col">
          <SchemaTree
            title={`Source — ${schemas.data.source.system_name}`}
            system={schemas.data.source}
            headerExtra={
              <button
                type="button"
                className="btn btn-sm"
                onClick={() =>
                  setApproveTarget({
                    fingerprint: schemas.data.source.fingerprint,
                    system: schemas.data.source.system_name,
                  })
                }
              >
                Approve fingerprint
              </button>
            }
          />
          <SchemaTree
            title={`Target — ${schemas.data.target.system_name}`}
            system={schemas.data.target}
            headerExtra={
              <button
                type="button"
                className="btn btn-sm"
                onClick={() =>
                  setApproveTarget({
                    fingerprint: schemas.data.target.fingerprint,
                    system: schemas.data.target.system_name,
                  })
                }
              >
                Approve fingerprint
              </button>
            }
          />
        </div>
      )}

      {/* ---- Drift reports ---- */}
      <div className="page-title-row">
        <h3 className="section-sub" style={{ margin: 0 }}>
          {label("drift")}
        </h3>
        <button
          type="button"
          className="btn"
          disabled={monitor.running}
          onClick={() => void monitor.run({ job_type: "monitor", params: {} }).catch(() => undefined)}
        >
          {monitor.running ? "Checking…" : "Run drift check now"}
        </button>
      </div>
      {monitor.submitError && <div className="form-error">{monitor.submitError}</div>}
      {monitor.job?.status === "succeeded" && (
        <div className="alert alert-ok">Drift check complete — job #{monitor.job.id}.</div>
      )}

      {drift.isLoading && <p className="dim">Loading…</p>}
      {drift.isError && <div className="alert alert-danger">{errMsg(drift.error)}</div>}
      {drift.data?.length === 0 && (
        <div className="panel">
          <p className="dim">
            No schema changes — observed target schemas match their approved fingerprints.
          </p>
        </div>
      )}

      {drift.data?.map((report) => (
        <section
          key={report.id}
          className={`panel drift-card${report.breaking && !report.resolved_at ? " drift-breaking" : ""}`}
        >
          <div className="panel-header">
            <h3 className="panel-title">
              #{report.id} · <span className="mono">{report.target_system}</span>
            </h3>
            <div className="panel-header-actions">
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
    </div>
  );
}
