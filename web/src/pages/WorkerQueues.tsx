import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cancelQueue,
  getDeadLetter,
  getQuarantine,
  getStatus,
  getWorkerStatus,
  replayEvent,
  startWorker,
  stopWorker,
} from "../api/client";
import GuardedActionModal from "../components/GuardedActionModal";
import StatusChip from "../components/StatusChip";
import { useJobRunner } from "../hooks/useJobRunner";
import { errMsg, fmtAgo, fmtDate, prettyJson } from "../utils";

type WorkerModal = "start" | "stop" | "refresh" | "refresh_all" | null;

export default function WorkerQueues() {
  const queryClient = useQueryClient();
  const [modal, setModal] = useState<WorkerModal>(null);
  const [interval, setIntervalSec] = useState(10);
  const [batchSize, setBatchSize] = useState(200);
  const [includeResolved, setIncludeResolved] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [actionError, setActionError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState<string | null>(null);

  // refresh (typed tier) inputs
  const [refreshSchema, setRefreshSchema] = useState("irimsv");
  const [refreshTables, setRefreshTables] = useState("");
  const [refreshTarget, setRefreshTarget] = useState("lrmis");

  const worker = useQuery({ queryKey: ["worker"], queryFn: getWorkerStatus, refetchInterval: 5000 });
  const status = useQuery({ queryKey: ["status"], queryFn: getStatus, refetchInterval: 5000 });
  const quarantine = useQuery({
    queryKey: ["quarantine", includeResolved],
    queryFn: () => getQuarantine(includeResolved),
    refetchInterval: 10000,
  });
  const deadLetter = useQuery({
    queryKey: ["dead-letter"],
    queryFn: getDeadLetter,
    refetchInterval: 10000,
  });

  const singlePass = useJobRunner();
  const refreshAll = useJobRunner();

  const entities = status.data?.entities ?? [];
  const deployedCount = entities.filter((e) => e.status === "deployed").length;

  const invalidateAll = () => {
    void queryClient.invalidateQueries({ queryKey: ["worker"] });
    void queryClient.invalidateQueries({ queryKey: ["status"] });
    void queryClient.invalidateQueries({ queryKey: ["quarantine"] });
    void queryClient.invalidateQueries({ queryKey: ["dead-letter"] });
  };

  const start = useMutation({
    mutationFn: (reason: string) => startWorker({ interval, batch_size: batchSize, reason }),
    onSuccess: () => {
      setModal(null);
      invalidateAll();
    },
  });
  const stop = useMutation({
    mutationFn: (reason: string) => stopWorker(reason),
    onSuccess: () => {
      setModal(null);
      invalidateAll();
    },
  });
  const replay = useMutation({
    mutationFn: replayEvent,
    onSuccess: invalidateAll,
    onError: (err) => setActionError(errMsg(err)),
  });

  const handleCancelQueue = async (entity: string) => {
    setCancelling(entity);
    try {
      await cancelQueue(entity);
      invalidateAll();
    } catch (err) {
      setActionError(errMsg(err));
    } finally {
      setCancelling(null);
    }
  };

  const toggleExpanded = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const w = worker.data;

  return (
    <div className="page">
      <h2 className="page-title">Worker &amp; Queues</h2>

      {actionError && (
        <div className="alert alert-danger" role="alert">
          {actionError}{" "}
          <button type="button" className="btn btn-ghost btn-xs" onClick={() => setActionError(null)}>
            dismiss
          </button>
        </div>
      )}

      <div className="two-col">
        <section className="panel">
          <div className="panel-header">
            <h3 className="panel-title">Worker loop</h3>
            <StatusChip status={w?.running ? "running" : "paused"} label={w?.running ? "running" : "stopped"} />
          </div>
          {w?.running ? (
            <dl className="kv">
              <div>
                <dt>Started</dt>
                <dd>
                  {fmtDate(w.started_at)} ({fmtAgo(w.started_at)} ago)
                </dd>
              </div>
              <div>
                <dt>Started by</dt>
                <dd className="mono">{w.started_by ?? "—"}</dd>
              </div>
              <div>
                <dt>Interval / batch</dt>
                <dd className="mono">
                  {w.interval ?? "—"}s / {w.batch_size ?? "—"}
                </dd>
              </div>
              {w.last_result !== undefined && w.last_result !== null && (
                <div>
                  <dt>Last result</dt>
                  <dd>
                    <pre className="json-block">{prettyJson(w.last_result)}</pre>
                  </dd>
                </div>
              )}
            </dl>
          ) : (
            <p className="dim">The continuous delivery worker is not running.</p>
          )}
          <div className="form-row">
            <label className="field field-inline">
              <span className="field-label">Interval (s)</span>
              <input
                className="input input-sm"
                type="number"
                min={1}
                value={interval}
                onChange={(e) => setIntervalSec(Number(e.target.value))}
              />
            </label>
            <label className="field field-inline">
              <span className="field-label">Batch size</span>
              <input
                className="input input-sm"
                type="number"
                min={1}
                value={batchSize}
                onChange={(e) => setBatchSize(Number(e.target.value))}
              />
            </label>
          </div>
          <div className="btn-row">
            {w?.running ? (
              <button type="button" className="btn btn-danger" onClick={() => setModal("stop")}>
                Stop worker
              </button>
            ) : (
              <button type="button" className="btn btn-primary" onClick={() => setModal("start")}>
                Start worker
              </button>
            )}
            <button
              type="button"
              className="btn"
              disabled={singlePass.running}
              onClick={() =>
                void singlePass
                  .run({ job_type: "worker_run", params: { batch_size: batchSize } })
                  .catch(() => undefined)
              }
            >
              {singlePass.running ? "Pass running…" : "Run single pass"}
            </button>
          </div>
          {singlePass.submitError && <div className="form-error">{singlePass.submitError}</div>}
          {singlePass.job && (
            <p className="dim">
              Single pass job #{singlePass.job.id}: <StatusChip status={singlePass.job.status} />
            </p>
          )}
        </section>

        <section className="panel">
          <div className="panel-header">
            <h3 className="panel-title">Outbox by entity</h3>
          </div>
          <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th>Entity</th>
                <th>Status</th>
                <th>Events</th>
                <th>Oldest</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {status.data?.outbox_stats.map((row, i) => (
                <tr key={`${row.source_entity}-${row.status}-${i}`}>
                  <td className="mono">{row.source_entity}</td>
                  <td>
                    <StatusChip status={row.status} />
                  </td>
                  <td>{row.events}</td>
                  <td className="dim">{row.oldest ? `${fmtAgo(row.oldest)} ago` : "—"}</td>
                  <td className="row-actions">
                    {row.status === "pending" && row.events > 0 && (
                      <button
                        type="button"
                        className="btn btn-sm btn-ghost"
                        disabled={cancelling === row.source_entity}
                        onClick={() => handleCancelQueue(row.source_entity)}
                        title="Cancel queued events"
                      >
                        {cancelling === row.source_entity ? "…" : "Cancel"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {status.data && status.data.outbox_stats.length === 0 && (
                <tr>
                  <td colSpan={5} className="dim">
                    Outbox is empty.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          </div>
        </section>
      </div>

      <section className="panel">
        <div className="panel-header">
          <h3 className="panel-title">Manual data refresh</h3>
          <span className="dim">typed confirmation required</span>
        </div>
        <div className="form-row">
          <label className="field field-inline">
            <span className="field-label">Source schema</span>
            <input
              className="input input-sm mono"
              value={refreshSchema}
              onChange={(e) => setRefreshSchema(e.target.value)}
            />
          </label>
          <label className="field field-inline grow">
            <span className="field-label">Source tables (comma separated)</span>
            <input
              className="input input-sm mono"
              value={refreshTables}
              placeholder="farmers,parcels"
              onChange={(e) => setRefreshTables(e.target.value)}
            />
          </label>
          <label className="field field-inline">
            <span className="field-label">Target system</span>
            <input
              className="input input-sm mono"
              value={refreshTarget}
              onChange={(e) => setRefreshTarget(e.target.value)}
            />
          </label>
          <button
            type="button"
            className="btn btn-danger-outline"
            disabled={!refreshTables.trim()}
            onClick={() => setModal("refresh")}
          >
            Refresh…
          </button>
          <button
            type="button"
            className="btn btn-danger-outline"
            disabled={deployedCount === 0 || refreshAll.running}
            onClick={() => setModal("refresh_all")}
          >
            {refreshAll.running
              ? "Refreshing…"
              : `Refresh all deployed${deployedCount > 0 ? ` (${deployedCount})` : ""}`}
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h3 className="panel-title">Quarantine</h3>
          <label className="checkbox-label dim">
            <input
              type="checkbox"
              checked={includeResolved}
              onChange={(e) => setIncludeResolved(e.target.checked)}
            />
            include resolved
          </label>
        </div>
        {quarantine.data?.length === 0 && <p className="dim">Quarantine is empty.</p>}
        {quarantine.data?.map((row) => {
          const isOpen = expanded.has(row.event_id);
          return (
            <div key={row.event_id} className="event-row">
              <div className="event-row-top">
                <button type="button" className="btn btn-ghost btn-xs" onClick={() => toggleExpanded(row.event_id)}>
                  {isOpen ? "▾" : "▸"}
                </button>
                <span className="mono event-id" title={row.event_id}>
                  {row.event_id}
                </span>
                <span className="mono">{row.source_entity}</span>
                <span className="badge badge-type">{row.operation}</span>
                <StatusChip status={row.outbox_status} />
                <span className="dim">attempts {row.attempts}</span>
                {row.external_reference && <span className="dim mono">ref {row.external_reference}</span>}
                <span className="dim">{fmtAgo(row.created_at)} ago</span>
                <button
                  type="button"
                  className="btn btn-sm btn-primary push-right"
                  disabled={replay.isPending}
                  onClick={() => replay.mutate(row.event_id)}
                >
                  Replay
                </button>
              </div>
              {isOpen && (
                <div className="event-detail">
                  <div>
                    <div className="field-label">Errors</div>
                    <pre className="json-block">{prettyJson(row.errors)}</pre>
                  </div>
                  <div>
                    <div className="field-label">Payload snapshot</div>
                    <pre className="json-block">{prettyJson(row.payload_snapshot)}</pre>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </section>

      <section className="panel">
        <div className="panel-header">
          <h3 className="panel-title">Dead letter</h3>
        </div>
        {deadLetter.data?.length === 0 && <p className="dim">Dead-letter queue is empty.</p>}
        {deadLetter.data && deadLetter.data.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>Event</th>
                <th>Entity</th>
                <th>Op</th>
                <th>Attempts</th>
                <th>Last error</th>
                <th>Created</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {deadLetter.data.map((row) => (
                <tr key={row.event_id}>
                  <td className="mono event-id" title={row.event_id}>
                    {row.event_id}
                  </td>
                  <td className="mono">{row.source_entity}</td>
                  <td>
                    <span className="badge badge-type">{row.operation}</span>
                  </td>
                  <td>{row.attempts}</td>
                  <td className="error-cell">
                    {row.last_error_code && <span className="mono">[{row.last_error_code}]</span>}{" "}
                    {row.last_error_message ?? "—"}
                  </td>
                  <td className="dim">{fmtAgo(row.created_at)} ago</td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-sm btn-primary"
                      disabled={replay.isPending}
                      onClick={() => replay.mutate(row.event_id)}
                    >
                      Replay
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <GuardedActionModal
        open={modal === "start"}
        tier="confirm"
        title="Start delivery worker"
        description={
          <span>
            Start the continuous worker loop with interval <code className="mono">{interval}s</code> and batch
            size <code className="mono">{batchSize}</code>.
          </span>
        }
        actionLabel="Start worker"
        busy={start.isPending}
        error={start.isError ? errMsg(start.error) : null}
        onConfirm={(reason) => start.mutate(reason)}
        onClose={() => setModal(null)}
      />
      <GuardedActionModal
        open={modal === "stop"}
        tier="confirm"
        danger
        title="Stop delivery worker"
        description="Pending outbox events will stop flowing to the target system until restarted."
        actionLabel="Stop worker"
        busy={stop.isPending}
        error={stop.isError ? errMsg(stop.error) : null}
        onConfirm={(reason) => stop.mutate(reason)}
        onClose={() => setModal(null)}
      />
      <GuardedActionModal
        open={modal === "refresh"}
        tier="typed"
        danger
        title="Refresh source data"
        description="Re-extracts the listed tables from the source database and rewrites target data."
        confirmString={refreshTables.trim()}
        warning={
          <div>
            <p>
              <strong>This rewrites target data for:</strong>
            </p>
            <p className="mono">
              {refreshSchema} → {refreshTarget}: {refreshTables.trim()}
            </p>
            <p>Deliveries for these entities may re-emit events after refresh.</p>
          </div>
        }
        actionLabel="Run refresh"
        busy={false}
        onConfirm={(reason) => {
          setModal(null);
          void singlePass
            .run({
              job_type: "refresh",
              params: {
                source_schema: refreshSchema.trim(),
                source_tables: refreshTables.trim(),
                target_system: refreshTarget.trim(),
                batch_size: batchSize,
              },
              reason,
              confirm: refreshTables.trim(),
            })
            .catch((err) => setActionError(errMsg(err)));
        }}
        onClose={() => setModal(null)}
      />
      <GuardedActionModal
        open={modal === "refresh_all"}
        tier="typed"
        danger
        title={`Refresh all deployed tables (${deployedCount})`}
        description="Re-deliver every deployed entity's rows into the target from the source."
        confirmString="REFRESH ALL"
        warning={
          <div>
            <p>
              <strong>This rewrites target data for all {deployedCount} deployed entities.</strong>
            </p>
            <p>Deliveries for these entities may re-emit events after refresh.</p>
          </div>
        }
        actionLabel="Refresh all"
        busy={refreshAll.running}
        error={refreshAll.submitError}
        onConfirm={(reason) => {
          setModal(null);
          void refreshAll
            .run({
              job_type: "refresh_all",
              params: { source_schema: refreshSchema.trim(), target_system: refreshTarget.trim() },
              reason,
              confirm: "REFRESH ALL",
            })
            .then(() => void queryClient.invalidateQueries({ queryKey: ["status"] }))
            .catch(() => {});
        }}
        onClose={() => setModal(null)}
      />
    </div>
  );
}
