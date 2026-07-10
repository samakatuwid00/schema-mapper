"""Durable allowlisted job orchestration (job-orchestration spec).

Jobs live in integration.admin_job, claimed with FOR UPDATE SKIP LOCKED -
the same idiom as the outbox - and emit integration.admin_job_event rows
that feed the SSE streams. A reaper fails running jobs whose heartbeat has
gone stale so a crashed process never shows 'running' forever.
"""
from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg2.extras

from .. import worker as worker_module
from ..services import ConflictError, NotFoundError, ValidationError
from ..services import migrations as migrations_service
from ..services import ops as ops_service
from ..services import scan as scan_service
from ..services.onboarding import backfill, deploy, discover, onboard_bulk, propose
from . import db
from .audit import write_audit
from .settings import JOB_HEARTBEAT_SECONDS, JOB_STALE_SECONDS, JOB_WORKERS


class JobContext:
    def __init__(self, job_id: str):
        self.job_id = job_id

    def emit(self, event_type: str, message: str = "", data: dict | None = None) -> None:
        with db.central().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO integration.admin_job_event (job_id, event_type, message, data)
                    VALUES (%s, %s, %s, %s)
                """, (self.job_id, event_type, message,
                      json.dumps(data, default=str) if data is not None else None))
            conn.commit()

    def progress(self, current: int, total: int | None = None, message: str = "") -> None:
        with db.central().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE integration.admin_job
                    SET progress_current = %s,
                        progress_total = COALESCE(%s, progress_total),
                        heartbeat_at = now()
                    WHERE id = %s
                """, (current, total, self.job_id))
            conn.commit()
        self.emit("progress", message, {"current": current, "total": total})


# ---------------------------------------------------------------------------
# Allowlisted handlers: params dict -> result dict
# ---------------------------------------------------------------------------

def _h_schema_scan(params, ctx: JobContext):
    return scan_service.scan(approve_initial=bool(params.get("approve_initial")),
                             by=params.get("_actor"))


def _h_discover(params, ctx):
    return discover(params.get("source_schema", "irimsv"),
                    params.get("target_system", "LRMIS").upper())


def _h_propose(params, ctx):
    return propose(params.get("source_schema", "irimsv"),
                   params["source_table"], params.get("target_system", "LRMIS").upper())


def _h_deploy(params, ctx):
    return deploy(int(params["proposal_id"]), params["_actor"])


def _h_backfill(params, ctx):
    return backfill(params["entity"])


def _normalize_tables(raw) -> list[str]:
    if isinstance(raw, str):
        raw = raw.split(",")
    return [t.strip() for t in (raw or []) if t and t.strip()]


def _h_onboard_bulk(params, ctx: JobContext):
    tables = _normalize_tables(params.get("tables"))
    if not tables:
        raise ValidationError("onboard_bulk requires at least one table")
    return onboard_bulk(
        params.get("source_schema", "irimsv"), tables,
        params.get("target_system", "LRMIS").upper(), params["_actor"],
        progress=lambda i, n, msg: ctx.progress(i, n, msg),
    )


def _h_reconcile(params, ctx):
    return ops_service.reconcile(params["entity"])


def _h_monitor(params, ctx):
    return ops_service.monitor()


def _h_refresh(params, ctx):
    tables = params["source_tables"]
    if isinstance(tables, str):
        tables = [t.strip() for t in tables.split(",")]
    return ops_service.refresh(
        params.get("source_schema", "irimsv"), tables,
        params.get("target_system", "LRMIS").upper(),
        source_system=params.get("source_system", "IRIMSV_REGION_V"),
        batch_size=int(params.get("batch_size", 1000)),
        schedule=params.get("schedule"),
        progress=lambda i, n, msg: ctx.progress(i, n, msg),
    )


def _h_refresh_all(params, ctx):
    with db.central().connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT source_table FROM integration.onboarding_entity
                WHERE status = 'deployed'
            """)
            tables = [row["source_table"] for row in cur.fetchall()]
    if not tables:
        raise ValidationError("no deployed entities to refresh")
    source_schema = params.get("source_schema", "irimsv")
    target_system = params.get("target_system", "LRMIS").upper()
    return ops_service.refresh(
        source_schema, tables, target_system,
        source_system=params.get("source_system", "IRIMSV_REGION_V"),
        batch_size=int(params.get("batch_size", 1000)),
        schedule=params.get("schedule"),
        progress=lambda i, n, msg: ctx.progress(i, n, msg),
    )


def _h_worker_run(params, ctx):
    return worker_module.process_once(batch_size=int(params.get("batch_size", 100)))


def _h_replay(params, ctx):
    return ops_service.replay_event(params["event_id"])


def _h_entity_toggle(params, ctx):
    return ops_service.set_entity_enabled(
        params["entity"], params.get("target_system", "LRMIS").upper(),
        bool(params["enabled"]), params.get("reason"))


def _h_cancel_queue(params, ctx):
    entity = params.get("entity") or params.get("source_entity")
    if not entity:
        raise ValidationError("entity is required")
    return ops_service.cancel_queue(entity)


def _h_migration_apply(params, ctx):
    return migrations_service.apply_migration(params["filename"], params["_actor"])


JOB_HANDLERS = {
    "schema_scan": _h_schema_scan,
    "discover": _h_discover,
    "propose": _h_propose,
    "deploy": _h_deploy,
    "backfill": _h_backfill,
    "onboard_bulk": _h_onboard_bulk,
    "reconcile": _h_reconcile,
    "monitor": _h_monitor,
    "refresh": _h_refresh,
    "refresh_all": _h_refresh_all,
    "worker_run": _h_worker_run,
    "replay": _h_replay,
    "entity_toggle": _h_entity_toggle,
    "cancel_queue": _h_cancel_queue,
    "migration_apply": _h_migration_apply,
}

ADMIN_ONLY_JOBS = {"migration_apply"}

# job types whose concurrent execution on the same scope must be rejected
_SCOPED = {
    "deploy": lambda p: f"deploy:{p.get('proposal_id')}",
    "refresh": lambda p: f"refresh:{p.get('source_tables')}",
    "refresh_all": lambda p: "refresh-all",
    "migration_apply": lambda p: "migrations",
    "onboard_bulk": lambda p: (
        f"onboard:{p.get('source_schema', 'irimsv')}:"
        f"{','.join(sorted(_normalize_tables(p.get('tables'))))}"
    ),
    "cancel_queue": lambda p: f"cancel:{p.get('entity', '')}",
}


def _scope(job_type: str, params: dict) -> str | None:
    fn = _SCOPED.get(job_type)
    return fn(params) if fn else None


def enqueue(job_type: str, params: dict, requested_by: str, role: str,
            reason: str | None = None) -> dict:
    if job_type not in JOB_HANDLERS:
        raise ValidationError(f"unknown job type '{job_type}' - allowed: {sorted(JOB_HANDLERS)}")
    if job_type in ADMIN_ONLY_JOBS and role != "admin":
        raise ValidationError("admin role required for this job type")
    scope = _scope(job_type, params)
    with db.central().connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if scope:
                cur.execute("""
                    SELECT id FROM integration.admin_job
                    WHERE status IN ('queued', 'running') AND params->>'_scope' = %s
                """, (scope,))
                running = cur.fetchone()
                if running:
                    raise ConflictError(
                        f"job {running['id']} is already {job_type} on the same target")
            stored = {**params, "_actor": requested_by}
            if scope:
                stored["_scope"] = scope
            cur.execute("""
                INSERT INTO integration.admin_job (job_type, params, reason, requested_by)
                VALUES (%s, %s, %s, %s) RETURNING id, created_at
            """, (job_type, json.dumps(stored, default=str), reason, requested_by))
            row = cur.fetchone()
        conn.commit()
    write_audit(requested_by, f"job:{job_type}", target_type="admin_job",
                target_id=str(row["id"]), reason=reason,
                details={k: v for k, v in params.items() if not k.startswith("_")})
    runner().wake()
    return {"job_id": str(row["id"]), "created_at": row["created_at"]}


def get_job(job_id: str) -> dict:
    with db.central().connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM integration.admin_job WHERE id = %s", (job_id,))
            row = cur.fetchone()
    if not row:
        raise NotFoundError(f"job {job_id} not found")
    job = dict(row)
    job["params"] = {k: v for k, v in (job["params"] or {}).items() if not k.startswith("_")}
    return job


def list_jobs(limit: int = 100) -> list[dict]:
    with db.central().connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, job_type, reason, requested_by, status, progress_current,
                       progress_total, created_at, started_at, finished_at, error_message
                FROM integration.admin_job ORDER BY created_at DESC LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]


def latest_event_id() -> int:
    """Current head of the event log, so a live tail can skip replaying history."""
    with db.central().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(max(id), 0) FROM integration.admin_job_event")
            return cur.fetchone()[0]


def job_events_after(last_id: int, job_id: str | None = None, limit: int = 500) -> list[dict]:
    with db.central().connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if job_id:
                cur.execute("""
                    SELECT * FROM integration.admin_job_event
                    WHERE id > %s AND job_id = %s ORDER BY id LIMIT %s
                """, (last_id, job_id, limit))
            else:
                cur.execute("""
                    SELECT * FROM integration.admin_job_event
                    WHERE id > %s ORDER BY id LIMIT %s
                """, (last_id, limit))
            return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Runner: dispatcher thread + worker pool + heartbeat + reaper
# ---------------------------------------------------------------------------

class JobRunner:
    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=JOB_WORKERS, thread_name_prefix="admin-job")
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._active: set[str] = set()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._dispatch_loop, daemon=True,
                                        name="admin-job-dispatch")
        self._hb = threading.Thread(target=self._heartbeat_loop, daemon=True,
                                    name="admin-job-heartbeat")

    def start(self):
        self._thread.start()
        self._hb.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def wake(self):
        self._wake.set()

    def _claim(self) -> dict | None:
        with db.central().connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    UPDATE integration.admin_job
                    SET status = 'running', started_at = now(), heartbeat_at = now()
                    WHERE id = (
                        SELECT id FROM integration.admin_job
                        WHERE status = 'queued'
                        ORDER BY created_at
                        FOR UPDATE SKIP LOCKED LIMIT 1
                    )
                    RETURNING *
                """)
                row = cur.fetchone()
            conn.commit()
        return dict(row) if row else None

    def _reap(self):
        with db.central().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE integration.admin_job
                    SET status = 'failed', finished_at = now(),
                        error_message = 'stale heartbeat - runner crashed or restarted'
                    WHERE status = 'running'
                      AND heartbeat_at < now() - make_interval(secs => %s)
                    RETURNING id
                """, (JOB_STALE_SECONDS,))
                reaped = [r[0] for r in cur.fetchall()]
            conn.commit()
        for job_id in reaped:
            JobContext(str(job_id)).emit("failed", "stale heartbeat - marked failed by reaper")

    def _dispatch_loop(self):
        while not self._stop.is_set():
            try:
                self._reap()
                claimed = self._claim()
                while claimed:
                    job_id = str(claimed["id"])
                    with self._lock:
                        self._active.add(job_id)
                    self._pool.submit(self._run, claimed)
                    claimed = self._claim()
            except Exception:
                # DB briefly unreachable - keep the dispatcher alive and retry
                pass
            self._wake.wait(timeout=1.0)
            self._wake.clear()

    def _heartbeat_loop(self):
        while not self._stop.is_set():
            try:
                with self._lock:
                    active = list(self._active)
                if active:
                    with db.central().connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute("""
                                UPDATE integration.admin_job SET heartbeat_at = now()
                                WHERE id = ANY(%s::uuid[]) AND status = 'running'
                            """, (active,))
                        conn.commit()
            except Exception:
                pass
            self._stop.wait(JOB_HEARTBEAT_SECONDS)

    def _run(self, job: dict):
        job_id = str(job["id"])
        ctx = JobContext(job_id)
        params = job["params"] or {}
        actor = params.get("_actor", job["requested_by"])
        ctx.emit("started", f"{job['job_type']} started by {actor}")
        try:
            result = JOB_HANDLERS[job["job_type"]](params, ctx)
            with db.central().connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE integration.admin_job
                        SET status = 'succeeded', finished_at = now(), result = %s
                        WHERE id = %s
                    """, (json.dumps(result, default=str), job_id))
                conn.commit()
            ctx.emit("succeeded", f"{job['job_type']} finished", result if isinstance(result, dict) else None)
        except Exception as exc:
            with db.central().connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE integration.admin_job
                        SET status = 'failed', finished_at = now(), error_message = %s
                        WHERE id = %s
                    """, (str(exc), job_id))
                conn.commit()
            ctx.emit("failed", str(exc))
            write_audit(actor, f"job:{job['job_type']}", target_type="admin_job",
                        target_id=job_id, result="failure", error_message=str(exc))
        finally:
            with self._lock:
                self._active.discard(job_id)


_runner: JobRunner | None = None


def runner() -> JobRunner:
    global _runner
    if _runner is None:
        _runner = JobRunner()
        _runner.start()
    return _runner


# ---------------------------------------------------------------------------
# Standing delivery-worker loop (start/stop toggle)
# ---------------------------------------------------------------------------

class WorkerController:
    def __init__(self):
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._status: dict = {"running": False}
        self._lock = threading.Lock()

    def start(self, interval: int, batch_size: int, actor: str) -> dict:
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise ConflictError("worker loop is already running")
            stop = threading.Event()
            from datetime import datetime, timezone
            started_at = datetime.now(timezone.utc).isoformat()
            self._status = {"running": True, "interval": interval,
                            "batch_size": batch_size, "started_by": actor,
                            "started_at": started_at, "last_result": None}

            def on_result(result):
                self._status["last_result"] = result

            self._stop_event = stop
            self._thread = threading.Thread(
                target=worker_module.run_loop, args=(stop, interval, batch_size, on_result),
                daemon=True, name="delivery-worker-loop")
            self._thread.start()
            return dict(self._status)

    def stop(self, actor: str) -> dict:
        with self._lock:
            if not (self._thread and self._thread.is_alive()):
                raise ConflictError("worker loop is not running")
            self._stop_event.set()
            self._status = {**self._status, "running": False, "stopped_by": actor}
            return dict(self._status)

    def status(self) -> dict:
        alive = bool(self._thread and self._thread.is_alive())
        return {**self._status, "running": alive}


worker_controller = WorkerController()
