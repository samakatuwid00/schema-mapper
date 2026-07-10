import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createJob,
  getSnapshots,
  getStatus,
  getWorkerStatus,
  restoreSnapshot,
  toggleEntity,
} from "../api/client";
import type { OnboardingEntity, StatusResponse } from "../api/types";
import GuardedActionModal from "../components/GuardedActionModal";
import HealthCard from "../components/HealthCard";
import PipelineDiagram from "../components/PipelineDiagram";
import Sparkline from "../components/Sparkline";
import StatusChip from "../components/StatusChip";
import { errMsg, fmtAgo, fmtDate } from "../utils";

const HISTORY_LEN = 30;

function queueCount(status: StatusResponse | undefined, key: string): number {
  return status?.queues.find((q) => q.status === key)?.events ?? 0;
}

function controlFor(status: StatusResponse | undefined, entity: OnboardingEntity) {
  return status?.entity_controls.find(
    (c) =>
      (c.source_entity === entity.source_table ||
        c.source_entity === `${entity.source_schema}.${entity.source_table}`) &&
      c.target_system === entity.target_system,
  );
}

function SnapshotsModal({
  entity,
  onClose,
}: {
  entity: OnboardingEntity;
  onClose: () => void;
}) {
  const table = entity.staging_table ?? entity.source_table;
  const [restoreTarget, setRestoreTarget] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ["snapshots", table],
    queryFn: () => getSnapshots(table),
  });
  const restore = useMutation({
    mutationFn: (args: { snapshot: string; reason: string }) =>
      restoreSnapshot({ table, snapshot: args.snapshot, reason: args.reason }),
    onSuccess: () => {
      setRestoreTarget(null);
      onClose();
    },
    onError: (err) => setError(errMsg(err)),
  });

  return (
    <div className="modal-overlay" role="presentation" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3 className="modal-title">
            Snapshots — <span className="mono">{table}</span>
          </h3>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
            ✕
          </button>
        </div>
        {isLoading && <p className="dim">Loading…</p>}
        {data && data.snapshots.length === 0 && <p className="dim">No snapshots for this table.</p>}
        <ul className="snapshot-list">
          {data?.snapshots.map((snap) => (
            <li key={snap} className="snapshot-row">
              <span className="mono">{snap}</span>
              <button type="button" className="btn btn-sm" onClick={() => setRestoreTarget(snap)}>
                Restore
              </button>
            </li>
          ))}
        </ul>
        {error && <div className="form-error">{error}</div>}
        <GuardedActionModal
          open={restoreTarget !== null}
          tier="confirm"
          danger
          title={`Restore snapshot of ${table}`}
          description={
            <span>
              Restore <code className="mono">{restoreTarget}</code> over the current staging table.
            </span>
          }
          actionLabel="Restore snapshot"
          busy={restore.isPending}
          error={restore.isError ? errMsg(restore.error) : null}
          onConfirm={(reason) => restore.mutate({ snapshot: restoreTarget as string, reason })}
          onClose={() => setRestoreTarget(null)}
        />
      </div>
    </div>
  );
}

export default function Overview() {
  const queryClient = useQueryClient();
  const [snapshotEntity, setSnapshotEntity] = useState<OnboardingEntity | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const { data: status, isLoading } = useQuery({
    queryKey: ["status"],
    queryFn: getStatus,
    refetchInterval: 5000,
  });

  const { data: worker } = useQuery({
    queryKey: ["worker-status"],
    queryFn: getWorkerStatus,
    refetchInterval: 5000,
  });

  // Rolling in-memory history of queue depth + delivered, sampled each poll.
  // Purely client-side (the API exposes point-in-time counts, not a series).
  const [pendingHistory, setPendingHistory] = useState<number[]>([]);
  const [deliveredHistory, setDeliveredHistory] = useState<number[]>([]);
  const lastSample = useRef<number>(-1);
  const pendingNow = status?.queues.find((q) => q.status === "pending")?.events ?? 0;
  const deliveredNow = status?.queues.find((q) => q.status === "delivered")?.events ?? 0;

  useEffect(() => {
    if (!status) return;
    // updated_at-free dedupe: sample once per distinct render of fresh data.
    const stamp = pendingNow * 1e6 + deliveredNow;
    if (stamp === lastSample.current) return;
    lastSample.current = stamp;
    setPendingHistory((h) => [...h, pendingNow].slice(-HISTORY_LEN));
    setDeliveredHistory((h) => [...h, deliveredNow].slice(-HISTORY_LEN));
  }, [status, pendingNow, deliveredNow]);

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ["status"] });

  const toggle = useMutation({
    mutationFn: toggleEntity,
    onSuccess: invalidate,
    onError: (err) => setActionError(errMsg(err)),
  });

  const reconcile = useMutation({
    mutationFn: (entity: string) => createJob({ job_type: "reconcile", params: { entity } }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["jobs"] }),
    onError: (err) => setActionError(errMsg(err)),
  });

  const oldestPending = status?.queues.find((q) => q.status === "pending")?.oldest ?? null;

  return (
    <div className="page">
      <h2 className="page-title">Overview</h2>

      {status && status.unresolved_drift > 0 && (
        <div className="alert alert-danger">
          <strong>Schema change detected.</strong> {status.unresolved_drift} unresolved schema change
          {status.unresolved_drift === 1 ? "" : "s"} — review before deploying or copying rows.{" "}
          <Link to="/schema">Open schema changes →</Link>
        </div>
      )}
      {actionError && (
        <div className="alert alert-danger" role="alert">
          {actionError}{" "}
          <button type="button" className="btn btn-ghost btn-xs" onClick={() => setActionError(null)}>
            dismiss
          </button>
        </div>
      )}

      <PipelineDiagram
        pending={queueCount(status, "pending")}
        retry={queueCount(status, "retry")}
        blocked={queueCount(status, "quarantined") + queueCount(status, "dead_letter")}
        delivered={queueCount(status, "delivered")}
        workerRunning={Boolean(worker?.running)}
      />

      <div className="trend-grid">
        <div className="panel trend-card">
          <div className="trend-head">
            <span className="trend-label">Queue depth</span>
            <span className="trend-value mono">{pendingNow}</span>
          </div>
          <Sparkline data={pendingHistory} width={220} height={40} stroke="var(--st-waiting)"
            ariaLabel={`Queue depth trend, now ${pendingNow}`} />
        </div>
        <div className="panel trend-card">
          <div className="trend-head">
            <span className="trend-label">Delivered</span>
            <span className="trend-value mono">{deliveredNow}</span>
          </div>
          <Sparkline data={deliveredHistory} width={220} height={40} stroke="var(--st-flowing)"
            fill="color-mix(in srgb, var(--st-flowing) 18%, transparent)"
            ariaLabel={`Delivered trend, now ${deliveredNow}`} />
        </div>
      </div>

      <div className="health-grid">
        <HealthCard
          label="Pending"
          tone="amber"
          value={queueCount(status, "pending")}
          sub={oldestPending ? `oldest ${fmtAgo(oldestPending)}` : "queue empty"}
        />
        <HealthCard label="Retry" tone="orange" value={queueCount(status, "retry")} />
        <HealthCard label="Quarantined" tone="purple" value={queueCount(status, "quarantined")} />
        <HealthCard label="Dead letter" tone="red" value={queueCount(status, "dead_letter")} />
        <HealthCard
          label="Unresolved quarantine"
          tone="purple"
          value={status?.unresolved_quarantine ?? "—"}
          sub="open worker & queues"
          to="/worker"
        />
        <HealthCard
          label="Unresolved drift"
          tone={status && status.unresolved_drift > 0 ? "red" : "green"}
          value={status?.unresolved_drift ?? "—"}
          sub="open schema changes"
          to="/schema"
        />
      </div>

      <section className="panel">
        <div className="panel-header">
          <h3 className="panel-title">Entities</h3>
          <span className="dim">{status?.entities.length ?? 0} onboarded</span>
        </div>
        {isLoading && <p className="dim">Loading…</p>}
        <table className="table">
          <thead>
            <tr>
              <th>Entity</th>
              <th>Target</th>
              <th>Status</th>
              <th>Staging table</th>
              <th>Deployed</th>
              <th>Kill switch</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {status?.entities.map((entity) => {
              const control = controlFor(status, entity);
              const enabled = control ? control.enabled : true;
              return (
                <tr key={entity.id}>
                  <td className="mono">
                    {entity.source_schema}.{entity.source_table}
                  </td>
                  <td className="mono">{entity.target_system}</td>
                  <td>
                    <StatusChip
                      status={entity.status}
                      title={entity.paused_reason ?? undefined}
                    />
                  </td>
                  <td className="mono dim">{entity.staging_table ?? "—"}</td>
                  <td className="dim">
                    {entity.deployed_at ? `${fmtDate(entity.deployed_at)} by ${entity.deployed_by ?? "?"}` : "—"}
                  </td>
                  <td>
                    <button
                      type="button"
                      className={`btn btn-sm ${enabled ? "btn-danger-outline" : "btn-primary"}`}
                      disabled={toggle.isPending}
                      title={control?.paused_reason ?? undefined}
                      onClick={() =>
                        toggle.mutate({
                          entity: entity.source_table,
                          target_system: entity.target_system,
                          enabled: !enabled,
                        })
                      }
                    >
                      {enabled ? "Disable" : "Enable"}
                    </button>
                  </td>
                  <td className="row-actions">
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => reconcile.mutate(entity.source_table)}
                      title="Run reconcile job for this entity"
                    >
                      Reconcile
                    </button>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => setSnapshotEntity(entity)}
                    >
                      Snapshots
                    </button>
                  </td>
                </tr>
              );
            })}
            {status && status.entities.length === 0 && (
              <tr>
                <td colSpan={7} className="dim">
                  No tables connected yet — go to <Link to="/tables">Tables</Link> to onboard them.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      {snapshotEntity && (
        <SnapshotsModal entity={snapshotEntity} onClose={() => setSnapshotEntity(null)} />
      )}
    </div>
  );
}
