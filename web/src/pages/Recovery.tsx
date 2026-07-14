import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  getRecoveryBackups,
  restoreSource,
  restoreTarget,
  type RecoveryUpload,
  type RestoreResult,
} from "../api/client";
import BackupUpload from "../components/BackupUpload";
import GuardedActionModal from "../components/GuardedActionModal";
import { errMsg } from "../utils";

const TARGET_DB = "lrmis_target";
const SOURCE_SCHEMA = "irimsv";

function bytes(n: number): string {
  if (n >= 1024 * 1024 * 1024) return `${(n / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${n} B`;
}

/**
 * Recovery — the two failure points this pipeline has actually hit, now
 * recoverable from the UI: restore the target from the backups the nightly
 * rebuild already takes (previously invisible), and replace an unreadable
 * source dump with a validated upload. Every restore is typed-confirmation
 * gated and audited; nothing here ever runs automatically.
 */
export default function Recovery() {
  const { data, error, isLoading, refetch } = useQuery({
    queryKey: ["recovery-backups"],
    queryFn: getRecoveryBackups,
  });

  const [uploadKind, setUploadKind] = useState<RecoveryUpload["kind"]>("source_dump");
  const [targetPending, setTargetPending] = useState<string | null>(null);
  const [sourcePending, setSourcePending] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [lastRestore, setLastRestore] = useState<RestoreResult | null>(null);

  const runTargetRestore = (reason: string, typed?: string) => {
    if (targetPending === null) return;
    setBusy(true);
    setRestoreError(null);
    restoreTarget({ backup_id: targetPending, confirm: typed ?? "", reason })
      .then((result) => {
        setLastRestore(result);
        setTargetPending(null);
        void refetch();
      })
      .catch((exc: unknown) => setRestoreError(errMsg(exc)))
      .finally(() => setBusy(false));
  };

  const runSourceRestore = (reason: string, typed?: string) => {
    if (sourcePending === null) return;
    setBusy(true);
    setRestoreError(null);
    restoreSource({ upload_id: sourcePending, confirm: typed ?? "", reason })
      .then((result) => {
        setLastRestore(result);
        setSourcePending(null);
        void refetch();
      })
      .catch((exc: unknown) => setRestoreError(errMsg(exc)))
      .finally(() => setBusy(false));
  };

  const uploads = data?.uploads ?? [];
  const backups = data?.target_backups ?? [];

  return (
    <div className="page">
      <h2 className="page-title">Recovery</h2>

      {error ? <div className="alert alert-danger">{errMsg(error)}</div> : null}

      <section className="panel">
        <div className="panel-header">
          <h3 className="panel-title">Automatic target backups</h3>
        </div>
        <p className="dim">
          The nightly rebuild dumps <span className="mono">{TARGET_DB}</span> to a
          timestamped file before every destructive reset. Restoring one replays
          it over the current target — use it when a rebuild failed partway.
        </p>
        {isLoading ? (
          <p className="dim">Loading…</p>
        ) : backups.length === 0 ? (
          <p className="dim">No automatic backups found.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>File</th>
                <th>Size</th>
                <th>Taken</th>
                <th aria-label="actions" />
              </tr>
            </thead>
            <tbody>
              {backups.map((backup) => (
                <tr key={backup.id}>
                  <td className="mono">{backup.id}</td>
                  <td>{bytes(backup.size_bytes)}</td>
                  <td className="mono dim">{backup.modified_at}</td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-danger btn-sm"
                      onClick={() => setTargetPending(backup.id)}
                    >
                      Restore…
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel">
        <div className="panel-header">
          <h3 className="panel-title">Upload a recovery file</h3>
        </div>
        <p className="dim">
          Files are staged in quarantine and validated (format, encoding, schema
          content) before they can be selected for a restore. A file that fails
          validation — e.g. a UTF-16 dump from a PowerShell redirect — is
          recorded with the reason and can never be restored from.
        </p>
        <label className="field" style={{ maxWidth: "20rem" }}>
          <span className="field-label">This file is a…</span>
          <select
            className="input"
            aria-label="Upload kind"
            value={uploadKind}
            onChange={(e) => setUploadKind(e.target.value as RecoveryUpload["kind"])}
          >
            <option value="source_dump">Replacement source dump (irimsv)</option>
            <option value="target_backup">Target backup ({TARGET_DB})</option>
          </select>
        </label>
        <BackupUpload kind={uploadKind} onUploaded={() => void refetch()} />
      </section>

      <section className="panel">
        <div className="panel-header">
          <h3 className="panel-title">Uploaded files</h3>
        </div>
        {isLoading ? (
          <p className="dim">Loading…</p>
        ) : uploads.length === 0 ? (
          <p className="dim">Nothing uploaded yet.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>File</th>
                <th>Kind</th>
                <th>Validation</th>
                <th>Uploaded</th>
                <th>Used</th>
                <th aria-label="actions" />
              </tr>
            </thead>
            <tbody>
              {uploads.map((upload) => (
                <tr key={upload.id}>
                  <td className="mono">{upload.original_filename}</td>
                  <td>{upload.kind === "source_dump" ? "source dump" : "target backup"}</td>
                  <td>
                    {upload.valid ? (
                      <span className="status-chip ok">valid</span>
                    ) : (
                      <span className="status-chip failed" title={upload.invalid_reason ?? ""}>
                        rejected: {upload.invalid_reason}
                      </span>
                    )}
                  </td>
                  <td className="mono dim">
                    {upload.uploaded_by} · {upload.uploaded_at}
                  </td>
                  <td className="mono dim">
                    {upload.used_at ? `${upload.used_by} · ${upload.used_at}` : "—"}
                  </td>
                  <td>
                    {upload.valid && upload.kind === "source_dump" && (
                      <button
                        type="button"
                        className="btn btn-danger btn-sm"
                        onClick={() => setSourcePending(upload.id)}
                      >
                        Restore source…
                      </button>
                    )}
                    {upload.valid && upload.kind === "target_backup" && (
                      <button
                        type="button"
                        className="btn btn-danger btn-sm"
                        onClick={() => setTargetPending(String(upload.id))}
                      >
                        Restore target…
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {lastRestore && (
        <section className="panel">
          <div className="panel-header">
            <h3 className="panel-title">Last restore</h3>
          </div>
          <details open>
            <summary className="dim">
              {lastRestore.executed ? "Executed" : "Plan (not executed)"}
            </summary>
            <pre className="mono" style={{ overflowX: "auto", maxHeight: "18rem" }}>
              {JSON.stringify(lastRestore, null, 2)}
            </pre>
          </details>
        </section>
      )}

      <GuardedActionModal
        open={targetPending !== null}
        tier="typed"
        danger
        title="Restore the target from this backup"
        description={
          <>
            This replays <span className="mono">{targetPending}</span> over{" "}
            <span className="mono">{TARGET_DB}</span>, discarding whatever is in
            the target right now. The action is audited.
          </>
        }
        confirmString={TARGET_DB}
        warning={
          <div>
            Current target data is overwritten. The nightly rebuild's own
            pre-reset backup means a wrong choice here is itself recoverable.
          </div>
        }
        actionLabel="Restore target"
        busy={busy}
        error={restoreError}
        onConfirm={runTargetRestore}
        onClose={() => {
          setTargetPending(null);
          setRestoreError(null);
        }}
      />

      <GuardedActionModal
        open={sourcePending !== null}
        tier="typed"
        danger
        title="Restore the source from this upload"
        description={
          <>
            This runs the configured source restore with the validated upload,
            replacing the <span className="mono">{SOURCE_SCHEMA}</span> schema in
            the central database. The action is audited.
          </>
        }
        confirmString={SOURCE_SCHEMA}
        warning={
          <div>
            The current source contents are dropped and reloaded from the
            uploaded dump before the next rebuild reads them.
          </div>
        }
        actionLabel="Restore source"
        busy={busy}
        error={restoreError}
        onConfirm={runSourceRestore}
        onClose={() => {
          setSourcePending(null);
          setRestoreError(null);
        }}
      />
    </div>
  );
}
