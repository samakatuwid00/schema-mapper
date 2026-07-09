import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getMigrationSql, listMigrations, markMigrationApplied } from "../api/client";
import { useAuth } from "../auth";
import GuardedActionModal from "../components/GuardedActionModal";
import StatusChip from "../components/StatusChip";
import { useJobRunner } from "../hooks/useJobRunner";
import { errMsg, fmtDate } from "../utils";

export default function Migrations() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const queryClient = useQueryClient();

  const [applyTarget, setApplyTarget] = useState<string | null>(null);
  const [markTarget, setMarkTarget] = useState<string | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);

  const migrations = useQuery({
    queryKey: ["migrations"],
    queryFn: listMigrations,
    refetchInterval: 15000,
  });

  // SQL preview for the typed apply modal.
  const sqlPreview = useQuery({
    queryKey: ["migration-sql", applyTarget],
    queryFn: () => getMigrationSql(applyTarget as string),
    enabled: applyTarget !== null && isAdmin,
  });

  const applyJob = useJobRunner();

  const markApplied = useMutation({
    mutationFn: (args: { filename: string; reason: string }) => markMigrationApplied(args),
    onSuccess: () => {
      setMarkTarget(null);
      void queryClient.invalidateQueries({ queryKey: ["migrations"] });
    },
  });

  return (
    <div className="page">
      <h2 className="page-title">Migrations</h2>

      {!isAdmin && (
        <div className="alert alert-info">
          You are signed in as <span className="mono">{user?.role}</span> — applying migrations requires the
          admin role.
        </div>
      )}
      {applyJob.job && (
        <div className={`alert ${applyJob.job.status === "failed" ? "alert-danger" : "alert-info"}`}>
          Migration job #{applyJob.job.id}: <StatusChip status={applyJob.job.status} />
          {applyJob.job.error_message && <span className="mono"> {applyJob.job.error_message}</span>}
        </div>
      )}

      <section className="panel">
        <div className="panel-header">
          <h3 className="panel-title">Migration files</h3>
          <span className="dim">{migrations.data?.length ?? 0} files</span>
        </div>
        {migrations.isError && <div className="alert alert-danger">{errMsg(migrations.error)}</div>}
        <table className="table">
          <thead>
            <tr>
              <th>Filename</th>
              <th>State</th>
              <th>Applied</th>
              <th>Checksum</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {migrations.data?.map((m) => (
              <tr key={m.filename}>
                <td className="mono">
                  {m.filename}
                  {!m.exists_on_disk && <span className="badge badge-warn">missing on disk</span>}
                </td>
                <td>
                  <StatusChip status={m.applied ? "applied" : "pending"} />
                </td>
                <td className="dim">
                  {m.applied ? `${fmtDate(m.applied_at)} by ${m.applied_by ?? "?"}` : "—"}
                </td>
                <td>
                  {m.checksum_mismatch ? (
                    <span className="badge badge-danger" title="File on disk no longer matches the applied checksum">
                      checksum mismatch
                    </span>
                  ) : (
                    <span className="mono dim" title={m.current_checksum ?? undefined}>
                      {m.current_checksum ? `${m.current_checksum.slice(0, 10)}…` : "—"}
                    </span>
                  )}
                </td>
                <td className="row-actions">
                  {isAdmin && !m.applied && (
                    <button
                      type="button"
                      className="btn btn-danger-outline btn-sm"
                      disabled={!m.exists_on_disk}
                      onClick={() => {
                        setApplyError(null);
                        setApplyTarget(m.filename);
                      }}
                    >
                      Apply…
                    </button>
                  )}
                  {isAdmin && !m.applied && (
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => setMarkTarget(m.filename)}
                      title="Record as applied without executing the SQL"
                    >
                      Mark applied…
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {migrations.data && migrations.data.length === 0 && (
              <tr>
                <td colSpan={5} className="dim">
                  No migration files found.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <GuardedActionModal
        open={applyTarget !== null}
        tier="typed"
        danger
        title={`Apply migration ${applyTarget ?? ""}`}
        description="Executes this SQL against the target database. This cannot be undone automatically."
        confirmString={applyTarget ?? ""}
        warning={
          <div>
            {sqlPreview.isLoading && <p className="dim">Loading SQL preview…</p>}
            {sqlPreview.isError && <p className="danger-text">{errMsg(sqlPreview.error)}</p>}
            {sqlPreview.data && (
              <>
                <p>
                  <strong>Target DSN:</strong> <span className="mono">{sqlPreview.data.dsn}</span>
                </p>
                <pre className="json-block sql-preview">{sqlPreview.data.sql}</pre>
              </>
            )}
          </div>
        }
        actionLabel="Apply migration"
        busy={applyJob.running}
        error={applyError}
        onConfirm={(reason) => {
          const filename = applyTarget as string;
          void applyJob
            .run({
              job_type: "migration_apply",
              params: { filename },
              reason,
              confirm: filename,
            })
            .then(() => setApplyTarget(null))
            .catch((err) => setApplyError(errMsg(err)));
        }}
        onClose={() => setApplyTarget(null)}
      />

      <GuardedActionModal
        open={markTarget !== null}
        tier="confirm"
        title={`Mark ${markTarget ?? ""} as applied`}
        description="Records the migration as applied in the ledger without executing its SQL. Use only when the change was applied manually."
        actionLabel="Mark applied"
        busy={markApplied.isPending}
        error={markApplied.isError ? errMsg(markApplied.error) : null}
        onConfirm={(reason) => markApplied.mutate({ filename: markTarget as string, reason })}
        onClose={() => setMarkTarget(null)}
      />
    </div>
  );
}
