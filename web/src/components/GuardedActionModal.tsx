import { useEffect, useRef, useState, type ReactNode } from "react";

export type GuardTier = "one-click" | "confirm" | "typed";

export interface GuardedActionModalProps {
  open: boolean;
  tier: GuardTier;
  title: string;
  /** Explanation shown under the title. */
  description?: ReactNode;
  actionLabel?: string;
  /** Exact string the user must type (required for the "typed" tier). */
  confirmString?: string;
  /** Warning / preview panel content (row counts, SQL preview...) for the "typed" tier. */
  warning?: ReactNode;
  danger?: boolean;
  busy?: boolean;
  error?: string | null;
  onConfirm: (reason: string) => void;
  onClose: () => void;
}

/**
 * Three-tier guarded action:
 *  - "one-click": no modal — fires onConfirm immediately when opened.
 *  - "confirm":   modal requiring a non-empty reason.
 *  - "typed":     modal requiring a reason AND the exact confirmation string,
 *                 with a warning/preview panel.
 */
export default function GuardedActionModal({
  open,
  tier,
  title,
  description,
  actionLabel = "Confirm",
  confirmString,
  warning,
  danger = false,
  busy = false,
  error,
  onConfirm,
  onClose,
}: GuardedActionModalProps) {
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState("");
  const firedRef = useRef(false);

  // Reset inputs each time the modal opens.
  useEffect(() => {
    if (open) {
      setReason("");
      setTyped("");
    } else {
      firedRef.current = false;
    }
  }, [open]);

  // One-click tier: no modal, fire immediately.
  useEffect(() => {
    if (open && tier === "one-click" && !firedRef.current) {
      firedRef.current = true;
      onConfirm("");
      onClose();
    }
  }, [open, tier, onConfirm, onClose]);

  if (!open || tier === "one-click") return null;

  const reasonOk = reason.trim().length > 0;
  const typedOk = tier !== "typed" || (confirmString !== undefined && typed === confirmString);
  const canConfirm = reasonOk && typedOk && !busy;

  return (
    <div className="modal-overlay" role="presentation" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h3 className={danger ? "modal-title danger" : "modal-title"}>{title}</h3>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        {description && <div className="modal-description">{description}</div>}

        {tier === "typed" && warning && (
          <div className="warning-panel" data-testid="warning-panel">
            {warning}
          </div>
        )}

        <label className="field">
          <span className="field-label">Reason (required — recorded in the audit log)</span>
          <textarea
            className="input"
            rows={3}
            value={reason}
            placeholder="Why are you performing this action?"
            onChange={(e) => setReason(e.target.value)}
            aria-label="Reason"
          />
        </label>

        {tier === "typed" && (
          <label className="field">
            <span className="field-label">
              Type <code className="mono confirm-target">{confirmString}</code> to confirm
            </span>
            <input
              className="input mono"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={confirmString}
              aria-label="Confirmation text"
              autoComplete="off"
              spellCheck={false}
            />
          </label>
        )}

        {error && <div className="form-error">{error}</div>}

        <div className="modal-actions">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className={danger ? "btn btn-danger" : "btn btn-primary"}
            disabled={!canConfirm}
            onClick={() => onConfirm(reason.trim())}
          >
            {busy ? "Working…" : actionLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
