import { useState } from "react";
import GuardedActionModal from "../components/GuardedActionModal";
import StatusChip from "../components/StatusChip";
import { useJobRunner } from "../hooks/useJobRunner";

/**
 * Nightly Rebuild — reset the LRMIS target and re-deliver every deployed entity
 * from the current source, optionally restoring the fresh source dump first.
 * A dry run reports the plan and counts without changing anything; a real run
 * is destructive (typed confirmation).
 */
export default function NightlyRebuild() {
  const runner = useJobRunner();
  const [runOpen, setRunOpen] = useState(false);
  const [restore, setRestore] = useState(false);

  const job = runner.job;
  // The nightly_refresh job result is a structured plan/outcome dict.
  const result = (job?.result ?? null) as Record<string, unknown> | null;
  const steps = (result?.steps ?? null) as Record<string, unknown> | null;
  const reset = (steps?.reset ?? null) as Record<string, unknown> | null;
  const redeliver = steps?.redeliver;
  const entities = (result?.entities ?? []) as unknown[];
  const inProgress = job?.status === "queued" || job?.status === "running";

  const dryRun = () => {
    void runner.run({ job_type: "nightly_refresh", params: { dry_run: true } }).catch(() => {});
  };
  const runRebuild = (reason: string) => {
    void runner
      .run({ job_type: "nightly_refresh", params: { restore }, reason, confirm: "REBUILD" })
      .then(() => setRunOpen(false))
      .catch(() => {});
  };

  return (
    <div className="page">
      <h2 className="page-title">Nightly Rebuild</h2>

      <section className="panel">
        <div className="panel-header">
          <h3 className="panel-title">Rebuild the LRMIS target from source</h3>
        </div>
        <p className="dim">
          Resets the LRMIS target (truncates the data tables and reseeds the FK-closure lookups),
          then re-delivers every deployed entity from the current source. The target is briefly empty
          mid-rebuild, so run it off-hours. A dry run changes nothing.
        </p>

        <label className="field">
          <span>
            <input
              type="checkbox"
              checked={restore}
              onChange={(e) => setRestore(e.target.checked)}
              style={{ marginRight: "0.5rem" }}
            />
            Also restore the fresh source dump into central first
          </span>
          <span className="field-label dim">
            Destructive: drops and reloads the source. Requires the restore command to be configured
            (LRMIS_SOURCE_RESTORE_CMD or CENTRAL_DSN).
          </span>
        </label>

        <div className="row-actions" style={{ marginTop: "1rem" }}>
          <button type="button" className="btn btn-sm" disabled={runner.running} onClick={dryRun}>
            {runner.running ? "Working…" : "Dry run"}
          </button>
          <button
            type="button"
            className="btn btn-danger btn-sm"
            disabled={runner.running}
            onClick={() => setRunOpen(true)}
          >
            Run rebuild…
          </button>
        </div>
      </section>

      {job && (
        <section className="panel">
          <div className="panel-header">
            <h3 className="panel-title">
              Last run {result?.dry_run ? <span className="dim">(dry run)</span> : null}
            </h3>
            <StatusChip status={job.status} />
          </div>

          {runner.submitError && <div className="alert alert-danger">{runner.submitError}</div>}
          {job.status === "failed" && (
            <div className="alert alert-danger">{job.error_message ?? "Rebuild failed."}</div>
          )}
          {inProgress && (
            <p className="dim">Rebuild in progress — the target is being reset and refilled…</p>
          )}

          {result && (
            <>
              <div className="fingerprint-row">
                <span className="mono dim">entities: {entities.length}</span>
                {reset && (
                  <span className="mono dim">
                    reset: {String(reset.tables_to_create)} tables, {String(reset.lookups_to_seed)} lookups
                  </span>
                )}
                {Array.isArray(redeliver) && (
                  <span className="mono dim">
                    delivered: {redeliver.filter((r) => (r as Record<string, unknown>).status === "refreshed").length}/
                    {redeliver.length}
                  </span>
                )}
                {job.requested_by && <span className="mono dim">by: {job.requested_by}</span>}
                {job.finished_at && <span className="mono dim">{job.finished_at}</span>}
              </div>
              <details style={{ marginTop: "0.75rem" }}>
                <summary className="dim">Full result</summary>
                <pre className="mono" style={{ overflowX: "auto", maxHeight: "24rem" }}>
                  {JSON.stringify(result, null, 2)}
                </pre>
              </details>
            </>
          )}
        </section>
      )}

      <GuardedActionModal
        open={runOpen}
        tier="typed"
        danger
        title="Run the nightly rebuild"
        description={
          restore
            ? "This will RESTORE the source dump into central, then reset the LRMIS target and re-deliver every entity. Destructive and irreversible."
            : "This will RESET the LRMIS target (truncate + reseed lookups) and re-deliver every entity from the current source. Destructive."
        }
        confirmString="REBUILD"
        warning={
          <div>
            Target <span className="mono">lrmis_target</span> is briefly empty during the rebuild.{" "}
            {restore ? "The source will be dropped and restored from the dump." : "The source is left as-is."}
          </div>
        }
        actionLabel="Run rebuild"
        busy={runner.running}
        error={runner.submitError}
        onConfirm={(reason) => runRebuild(reason)}
        onClose={() => setRunOpen(false)}
      />
    </div>
  );
}
