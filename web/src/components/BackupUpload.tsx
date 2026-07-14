import { useRef, useState, type DragEvent } from "react";
import { uploadRecoveryFile, type RecoveryUpload } from "../api/client";
import { errMsg } from "../utils";

export interface BackupUploadProps {
  /** What the file is: a replacement source dump or a target backup. */
  kind: RecoveryUpload["kind"];
  /** Called with the recorded row after the upload completes (valid or not). */
  onUploaded?: (row: RecoveryUpload) => void;
}

/**
 * Drag-and-drop (or browse) upload for recovery files, with real upload
 * progress and explicit validation feedback: the server validates the file
 * (magic byte, encoding, schema content) before it is ever offered as a
 * restore candidate, and this surfaces the exact rejection reason — e.g.
 * "file is UTF-16, expected UTF-8".
 */
export default function BackupUpload({ kind, onUploaded }: BackupUploadProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RecoveryUpload | null>(null);

  const send = (file: File) => {
    setBusy(true);
    setError(null);
    setResult(null);
    setProgress(0);
    uploadRecoveryFile(file, kind, setProgress)
      .then((row) => {
        setResult(row);
        onUploaded?.(row);
      })
      .catch((exc: unknown) => setError(errMsg(exc)))
      .finally(() => setBusy(false));
  };

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragOver(false);
    const file = event.dataTransfer.files?.[0];
    if (file && !busy) send(file);
  };

  const label = kind === "source_dump" ? "source dump" : "target backup";

  return (
    <div>
      <div
        className={`upload-drop${dragOver ? " drag-over" : ""}`}
        role="button"
        tabIndex={0}
        aria-label={`Upload ${label}`}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        style={{
          border: "1px dashed var(--border, #666)",
          borderRadius: "6px",
          padding: "1.25rem",
          textAlign: "center",
          cursor: busy ? "progress" : "pointer",
          opacity: busy ? 0.7 : 1,
        }}
      >
        <input
          ref={inputRef}
          type="file"
          hidden
          aria-label={`${label} file`}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) send(file);
            e.target.value = "";
          }}
        />
        {busy ? (
          <span className="dim">Uploading… {Math.round(progress * 100)}%</span>
        ) : (
          <span className="dim">
            Drop a {label} here, or click to browse. It is validated before it
            can be used.
          </span>
        )}
      </div>

      {busy && (
        <progress
          value={progress}
          max={1}
          aria-label="Upload progress"
          style={{ width: "100%", marginTop: "0.5rem" }}
        />
      )}

      {error && <div className="alert alert-danger">{error}</div>}

      {result && result.valid && (
        <div className="alert" role="status">
          <strong>{result.original_filename}</strong> passed validation and is
          now available as a restore candidate.
        </div>
      )}
      {result && !result.valid && (
        <div className="alert alert-danger" role="alert">
          <strong>{result.original_filename}</strong> was rejected:{" "}
          {result.invalid_reason ?? "validation failed"}. The file was recorded
          but can never be restored from.
        </div>
      )}
    </div>
  );
}
