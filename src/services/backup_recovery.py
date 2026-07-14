"""Disaster-recovery core (source-schema-swap-and-disaster-recovery §3).

Covers exactly the two failure points this project has already hit:

* the source dump on disk is unreadable (the recorded UTF-16-from-PowerShell
  incident in docs/RUNBOOK_source_to_target.md) — an admin uploads a
  replacement dump, which is validated BEFORE it is ever offered as a restore
  candidate;
* a target rebuild fails partway — the timestamped backups `nightly_refresh`
  already takes before every destructive reset become visible and restorable.

Design rails (see the change's design.md):

* D3 — uploads land in a quarantined staging directory, never a path any
  restore command executes on its own (`LRMIS_SOURCE_DUMP_PATH` is untouched);
  they are validated (magic byte, encoding, schema-content) before being
  offered; every upload is recorded in ``integration.recovery_upload``.
* D4 — a restore is ALWAYS a typed-confirmation action, wraps the existing
  restore primitives (`nightly_refresh.restore_source_dump`,
  `pg_restore.restore_pg_backup`, or the mysql mirror of `backup_target`),
  and stamps ``used_at``/``used_by``.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone

import psycopg2.extras

from ..connectors import PostgresCentralConnector
from .common import NotFoundError, ValidationError
from .nightly_refresh import BACKUP_DIR, restore_source_dump
from .pg_restore import PG_DUMP_MAGIC, is_pg_custom_dump

# Quarantined upload staging area — deliberately NOT a path any restore
# command reads by default (D3).
UPLOAD_DIR = os.environ.get(
    "LRMIS_RECOVERY_UPLOAD_DIR", os.path.join(BACKUP_DIR, "uploads"))

# Uploads are admin-only recovery artifacts, but still size-capped so a stray
# file can't fill the disk. 2 GiB default, overridable per deployment.
MAX_UPLOAD_BYTES = int(os.environ.get("LRMIS_RECOVERY_MAX_UPLOAD_BYTES",
                                      2 * 1024 * 1024 * 1024))

VALID_KINDS = ("source_dump", "target_backup")

_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
_SQLISH_MARKERS = ("create table", "insert into", "create schema",
                   "mysql dump", "create database", "copy ", "set ")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Listing what can be restored
# ---------------------------------------------------------------------------

def list_target_backups(backup_dir: str | None = None) -> list[dict]:
    """The timestamped ``{db}-{stamp}.sql`` backups `nightly_refresh.backup_target`
    already writes before every destructive reset — previously invisible to any
    UI. Newest first. Listing only; restoring is a separate confirmed action."""
    backup_dir = backup_dir or BACKUP_DIR
    if not os.path.isdir(backup_dir):
        return []
    out = []
    for name in os.listdir(backup_dir):
        path = os.path.join(backup_dir, name)
        if not os.path.isfile(path) or not name.lower().endswith(".sql"):
            continue
        stat = os.stat(path)
        out.append({
            "id": name,
            "path": path,
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    return sorted(out, key=lambda b: b["modified_at"], reverse=True)


def list_uploads(central: PostgresCentralConnector | None = None,
                 kind: str | None = None) -> list[dict]:
    """Recorded recovery uploads (valid and invalid — invalid rows show the
    operator why a file was rejected), newest first."""
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if kind:
                    cur.execute("""
                        SELECT * FROM integration.recovery_upload
                        WHERE kind = %s ORDER BY uploaded_at DESC
                    """, (kind,))
                else:
                    cur.execute("""
                        SELECT * FROM integration.recovery_upload
                        ORDER BY uploaded_at DESC
                    """)
                return [dict(r) for r in cur.fetchall()]
    finally:
        if owns:
            central.close()


# ---------------------------------------------------------------------------
# Validation (magic byte + encoding + schema content) — pure file checks
# ---------------------------------------------------------------------------

def validate_upload(path: str, kind: str) -> dict:
    """Validate an uploaded file BEFORE it is ever offered as a restore
    candidate. Returns ``{"ok": bool, "reason": str | None, "format": ...}``.

    * magic byte — reuses `pg_restore`'s ``PGDMP`` check for custom-format
      archives; anything else must look like a plain-SQL text dump.
    * encoding — a plain-SQL dump must be UTF-8. UTF-16 (the exact
      PowerShell-``>``-redirect failure recorded in the runbook) is rejected
      with a specific reason, BOM'd or not.
    * schema content — a ``source_dump`` must actually contain the source
      schema (``irimsv``); restoring a dump of something else would silently
      produce an empty source.
    """
    if kind not in VALID_KINDS:
        return {"ok": False, "reason": f"unknown upload kind {kind!r}", "format": None}
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            head = handle.read(65536)
    except OSError as exc:
        return {"ok": False, "reason": f"file unreadable: {exc}", "format": None}
    if size == 0:
        return {"ok": False, "reason": "file is empty", "format": None}

    # -- PostgreSQL custom-format archive (pg_restore input) -----------------
    if head.startswith(PG_DUMP_MAGIC):
        if kind == "target_backup":
            return {"ok": False, "format": "pg_custom",
                    "reason": ("file is a PostgreSQL custom-format archive; a "
                               "target backup must be a plain-SQL dump "
                               "(mysqldump output)")}
        # Custom archives keep schema names as plain text in the TOC.
        if not _binary_contains(path, b"irimsv"):
            return {"ok": False, "format": "pg_custom",
                    "reason": "dump does not contain the irimsv schema"}
        return {"ok": True, "reason": None, "format": "pg_custom"}

    # -- plain-SQL text dump --------------------------------------------------
    if head.startswith(_UTF16_BOMS) or _looks_utf16(head):
        return {"ok": False, "format": "sql",
                "reason": "file is UTF-16, expected UTF-8 (re-export the dump "
                          "without PowerShell '>' redirection, e.g. "
                          "pg_dump -f / mysqldump --result-file)"}
    try:
        text_head = head.decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "format": None,
                "reason": "file is neither a PostgreSQL custom-format archive "
                          "(no PGDMP magic) nor UTF-8 text — not a database dump"}
    lowered = text_head.lower()
    if not any(marker in lowered for marker in _SQLISH_MARKERS):
        return {"ok": False, "format": "sql",
                "reason": "file is UTF-8 text but contains no SQL dump "
                          "statements (CREATE/INSERT/COPY)"}
    if kind == "source_dump" and not _text_file_contains(path, re.compile(r"\birimsv\b", re.I)):
        return {"ok": False, "format": "sql",
                "reason": "dump does not contain the irimsv schema"}
    return {"ok": True, "reason": None, "format": "sql"}


def _looks_utf16(head: bytes) -> bool:
    """BOM-less UTF-16 heuristic: SQL text is ASCII-dominated, so UTF-16
    encodes it with a NUL in nearly every other byte."""
    sample = head[:4096]
    return bool(sample) and sample.count(b"\x00") > len(sample) // 4


def _binary_contains(path: str, needle: bytes, limit: int = 32 * 1024 * 1024) -> bool:
    read = 0
    tail = b""
    with open(path, "rb") as handle:
        while read < limit:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                return False
            if needle in tail + chunk:
                return True
            tail = chunk[-len(needle):]
            read += len(chunk)
    return False


def _text_file_contains(path: str, pattern: re.Pattern, limit: int = 64 * 1024 * 1024) -> bool:
    read = 0
    tail = ""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        while read < limit:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                return False
            if pattern.search(tail + chunk):
                return True
            tail = chunk[-64:]
            read += len(chunk)
    return False


# ---------------------------------------------------------------------------
# Staging an upload (quarantine + validate + audit row)
# ---------------------------------------------------------------------------

def _safe_filename(name: str) -> str:
    base = os.path.basename(name or "upload")
    return re.sub(r"[^A-Za-z0-9._-]", "_", base) or "upload"


def stage_upload(stream, original_filename: str, kind: str, by: str,
                 central: PostgresCentralConnector | None = None,
                 upload_dir: str | None = None) -> dict:
    """Write an uploaded file to the quarantined staging area, validate it, and
    record a ``recovery_upload`` row (valid or not — the upload itself is the
    audited event). Returns the row as a dict, including the validation result.

    ``stream`` is any object with ``read(size)`` (an open file, an API upload).
    The file is never written to a path a restore command executes directly
    (D3); a restore is a separate, typed-confirmation action.
    """
    if kind not in VALID_KINDS:
        raise ValidationError(f"kind must be one of {VALID_KINDS}, got {kind!r}")
    upload_dir = upload_dir or UPLOAD_DIR
    os.makedirs(upload_dir, exist_ok=True)

    safe = _safe_filename(original_filename)
    stored_path = os.path.join(upload_dir, f"{uuid.uuid4().hex[:12]}-{safe}")
    digest = hashlib.sha256()
    size = 0
    with open(stored_path, "wb") as out:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8")
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                out.close()
                os.remove(stored_path)
                raise ValidationError(
                    f"upload exceeds the {MAX_UPLOAD_BYTES} byte cap")
            digest.update(chunk)
            out.write(chunk)

    verdict = validate_upload(stored_path, kind)

    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO integration.recovery_upload
                        (kind, original_filename, stored_path, checksum,
                         size_bytes, valid, invalid_reason, uploaded_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                """, (kind, original_filename, stored_path,
                      digest.hexdigest(), size, verdict["ok"],
                      verdict["reason"], by))
                row = dict(cur.fetchone())
            conn.commit()
    finally:
        if owns:
            central.close()
    row["validation"] = verdict
    return row


def _load_upload(conn, upload_id: int) -> dict:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM integration.recovery_upload WHERE id = %s",
                    (upload_id,))
        row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"recovery upload {upload_id} not found")
    return dict(row)


def _mark_used(conn, upload_id: int, by: str) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE integration.recovery_upload
            SET used_at = now(), used_by = %s WHERE id = %s
        """, (by, upload_id))


# ---------------------------------------------------------------------------
# Restores (typed confirmation, wrap existing primitives, audited)
# ---------------------------------------------------------------------------

def _target_db() -> str:
    return os.environ.get("LRMIS_TARGET_DATABASE", "lrmis_target")


def _mysql_restore_cmd(backup_path: str) -> str:
    """Mirror of `nightly_refresh.backup_target`'s mysqldump invocation: the
    same server/credential env vars, replayed with the mysql client. A
    deployment-specific command can override via LRMIS_TARGET_MYSQL_RESTORE_CMD
    ({backup}/{db} substituted) — configured, never guessed beyond the exact
    config the backup itself already used."""
    override = os.environ.get("LRMIS_TARGET_MYSQL_RESTORE_CMD")
    if override:
        return (override.replace("{backup}", backup_path)
                        .replace("{db}", _target_db()))
    host = os.environ.get("LRMIS_STAGING_HOST", "localhost")
    port = os.environ.get("LRMIS_STAGING_PORT", "3307")
    user = os.environ.get("LRMIS_ROOT_USER", "root")
    password = os.environ.get("LRMIS_ROOT_PASSWORD", "root")
    return (f'mysql -h {host} -P {port} -u {user} -p{password} '
            f'"{_target_db()}" < "{backup_path}"')


def _run_shell(cmd: str) -> dict:
    completed = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"restore failed (exit {completed.returncode}): "
                           f"{(completed.stderr or '')[-2000:]}")
    return {"command": cmd, "returncode": completed.returncode, "executed": True}


def restore_target(backup_id: str, *, confirm: str, by: str,
                   central: PostgresCentralConnector | None = None,
                   backup_dir: str | None = None, dry_run: bool = False,
                   runner=None) -> dict:
    """Restore the target database from a listed backup file or a validated
    uploaded target backup. ALWAYS requires typed confirmation (the target
    database name, mirroring the nightly rebuild's guard) — design D4.

    ``backup_id`` is either a filename from `list_target_backups()` or the
    numeric id of a ``recovery_upload`` row with kind='target_backup'.
    """
    expected = _target_db()
    if confirm != expected:
        raise ValidationError(
            f"typed confirmation mismatch: pass confirm={expected!r} to "
            f"restore the target from a backup (got {confirm!r})")

    owns = central is None
    central = central or PostgresCentralConnector()
    upload_row = None
    try:
        with central.connection() as conn:
            if str(backup_id).isdigit():
                upload_row = _load_upload(conn, int(backup_id))
                if upload_row["kind"] != "target_backup":
                    raise ValidationError(
                        f"upload {backup_id} is a {upload_row['kind']}, not a "
                        "target_backup")
                if not upload_row["valid"]:
                    raise ValidationError(
                        f"upload {backup_id} failed validation "
                        f"({upload_row['invalid_reason']}); refusing to restore from it")
                backup_path = upload_row["stored_path"]
            else:
                name = _safe_filename(str(backup_id))
                backup_path = os.path.join(backup_dir or BACKUP_DIR, name)
            if not os.path.exists(backup_path):
                raise NotFoundError(f"backup file not found: {backup_path}")

            if is_pg_custom_dump(backup_path):
                from .pg_restore import restore_pg_backup
                plan = restore_pg_backup(backup_path=backup_path, dry_run=True)
                cmd = plan["command"]
            else:
                cmd = _mysql_restore_cmd(backup_path)

            result = {"backup": str(backup_id), "path": backup_path,
                      "database": expected, "command": cmd, "executed": False,
                      "dry_run": dry_run}
            if dry_run:
                return result

            result.update((runner or _run_shell)(cmd))
            if upload_row is not None:
                _mark_used(conn, upload_row["id"], by)
            conn.commit()
            return result
    finally:
        if owns:
            central.close()


def restore_source(upload_id: int, *, confirm: str, by: str,
                   central: PostgresCentralConnector | None = None,
                   dry_run: bool = False, runner=None) -> dict:
    """Restore the source from a validated uploaded dump, wrapping
    `nightly_refresh.restore_source_dump` (same configured command). ALWAYS
    requires typed confirmation — the source schema name — regardless of who
    or what initiates it (design D4)."""
    expected = os.environ.get("SOURCE_SCHEMA", "irimsv")
    if confirm != expected:
        raise ValidationError(
            f"typed confirmation mismatch: pass confirm={expected!r} to "
            f"restore the source from an upload (got {confirm!r})")

    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            row = _load_upload(conn, int(upload_id))
            if row["kind"] != "source_dump":
                raise ValidationError(
                    f"upload {upload_id} is a {row['kind']}, not a source_dump")
            if not row["valid"]:
                raise ValidationError(
                    f"upload {upload_id} failed validation "
                    f"({row['invalid_reason']}); refusing to restore from it")
            if not os.path.exists(row["stored_path"]):
                raise NotFoundError(f"staged file missing: {row['stored_path']}")

            # One execution path: the wrapped primitive builds the plan (same
            # configured command the nightly rebuild uses), _run_shell runs it.
            plan = restore_source_dump(dump_path=row["stored_path"], dry_run=True)
            result = {"upload_id": row["id"], "dry_run": dry_run, **plan}
            if not dry_run:
                result.update((runner or _run_shell)(plan["command"]))
                _mark_used(conn, row["id"], by)
                conn.commit()
            return result
    finally:
        if owns:
            central.close()
