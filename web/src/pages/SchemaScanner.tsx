import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { approveSchema, getSchemas } from "../api/client";
import { subscribeJobEvents, type JobEvent } from "../api/sse";
import GuardedActionModal from "../components/GuardedActionModal";
import SchemaTree from "../components/SchemaTree";
import StatusChip from "../components/StatusChip";
import { useJobRunner } from "../hooks/useJobRunner";
import { errMsg, fmtDate, prettyJson } from "../utils";

interface ApproveTarget {
  fingerprint: string;
  system: string;
}

export default function SchemaScanner() {
  const [sourceSchema, setSourceSchema] = useState("irimsv");
  const [approveInitial, setApproveInitial] = useState(false);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [approveTarget, setApproveTarget] = useState<ApproveTarget | null>(null);

  const scan = useJobRunner();
  const schemas = useQuery({
    queryKey: ["schemas", sourceSchema],
    queryFn: () => getSchemas(sourceSchema),
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

  useEffect(() => {
    if (scan.job?.status === "succeeded") void schemas.refetch();
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
      <h2 className="page-title">Schema Scanner</h2>

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
                <strong>{pausedEntities.length} entit{pausedEntities.length === 1 ? "y" : "ies"} paused by this scan:</strong>{" "}
                <span className="mono">{pausedEntities.map(String).join(", ")}</span>
              </div>
            )}
            {result && (
              <details open>
                <summary className="dim">Scan result</summary>
                <pre className="json-block">{prettyJson(result)}</pre>
              </details>
            )}
          </div>
        )}
      </section>

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

      <GuardedActionModal
        open={approveTarget !== null}
        tier="confirm"
        title={`Approve schema fingerprint — ${approveTarget?.system ?? ""}`}
        description={
          <span>
            Mark fingerprint <code className="mono">{approveTarget?.fingerprint}</code> as the approved schema
            for <code className="mono">{approveTarget?.system}</code>.
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
