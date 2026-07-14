"""Recovery API (source-schema-swap-and-disaster-recovery §4).

Upload a replacement source dump / target backup (validated + quarantined
before it is ever offered — design D3), list what can be restored, and run a
restore behind the same typed-confirmation + reason + audit pattern every
other destructive action in this admin API uses (design D4). All four routes
call `src.services.backup_recovery` — the CLI (`scripts/recover.py`) calls the
same functions, neither bypasses the other.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel

from ..services import backup_recovery
from ..services.common import ValidationError
from .audit import audited
from .auth import AdminUser, current_user, require_operator

recovery_router = APIRouter(prefix="/api/recovery", tags=["recovery"])


def _require_reason(reason: str | None) -> str:
    if not reason or not reason.strip():
        raise ValidationError("a reason is required for this action")
    return reason.strip()


@recovery_router.get("/backups")
def list_backups(user: AdminUser = Depends(current_user)):
    """Everything restorable: the timestamped target backups `nightly_refresh`
    already takes (previously invisible to the UI) plus every recorded upload
    (invalid ones included, so the operator sees why a file was rejected)."""
    return {
        "target_backups": backup_recovery.list_target_backups(),
        "uploads": backup_recovery.list_uploads(),
    }


@recovery_router.post("/upload")
def upload(kind: str = Form(...), file: UploadFile = File(...),
           user: AdminUser = Depends(require_operator)):
    """Multipart upload of a source dump or target backup. Size-capped and
    streamed to the quarantined staging dir; validation runs before the row is
    returned, and the result carries the exact rejection reason (e.g. the
    historical "file is UTF-16, expected UTF-8")."""
    with audited(user.username, "recovery_upload", target_type="recovery_upload",
                 target_id=file.filename, details={"kind": kind}):
        row = backup_recovery.stage_upload(
            file.file, file.filename or "upload", kind, user.username)
    return row


class RestoreTargetBody(BaseModel):
    backup_id: str
    confirm: str | None = None
    reason: str | None = None
    dry_run: bool = False


@recovery_router.post("/restore-target")
def restore_target(body: RestoreTargetBody,
                   user: AdminUser = Depends(require_operator)):
    """Restore the target from a listed backup or validated upload. The typed
    confirmation (the target database name) is enforced in the service so no
    caller — UI, CLI, or future agent tool — can skip it (design D4)."""
    reason = _require_reason(body.reason)
    with audited(user.username, "recovery_restore_target",
                 target_type="backup", target_id=str(body.backup_id),
                 reason=reason, details={"dry_run": body.dry_run}):
        return backup_recovery.restore_target(
            body.backup_id, confirm=body.confirm or "", by=user.username,
            dry_run=body.dry_run)


class RestoreSourceBody(BaseModel):
    upload_id: int
    confirm: str | None = None
    reason: str | None = None
    dry_run: bool = False


@recovery_router.post("/restore-source")
def restore_source(body: RestoreSourceBody,
                   user: AdminUser = Depends(require_operator)):
    """Restore the source from a validated uploaded dump (wraps the same
    configured `restore_source_dump` command the nightly rebuild uses)."""
    reason = _require_reason(body.reason)
    with audited(user.username, "recovery_restore_source",
                 target_type="recovery_upload", target_id=str(body.upload_id),
                 reason=reason, details={"dry_run": body.dry_run}):
        return backup_recovery.restore_source(
            body.upload_id, confirm=body.confirm or "", by=user.username,
            dry_run=body.dry_run)
