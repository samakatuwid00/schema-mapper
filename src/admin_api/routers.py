"""API routes: auth, reads, guarded actions, jobs + SSE, migrations, users."""
from __future__ import annotations

import asyncio
import json

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ..services import data_browser as data_browser_service
from ..services import migrations as migrations_service
from ..services import ops as ops_service
from ..services import view_proposer
from ..services.common import ValidationError
from ..services.onboarding import get_review, resolve
from . import db, jobs
from .audit import audited, list_audit
from .auth import (AdminUser, authenticate, clear_session, current_user,
                   hash_password, require_admin, require_operator, set_session)

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
data_router = APIRouter(prefix="/api/data", tags=["data-browser"])
reads_router = APIRouter(prefix="/api", tags=["reads"],
                         dependencies=[Depends(current_user)])
actions_router = APIRouter(prefix="/api/actions", tags=["actions"])
jobs_router = APIRouter(prefix="/api", tags=["jobs"])
migrations_router = APIRouter(prefix="/api/migrations", tags=["migrations"])
users_router = APIRouter(prefix="/api/users", tags=["users"])
worker_router = APIRouter(prefix="/api/worker", tags=["worker"])


def _require_reason(reason: str | None) -> str:
    if not reason or not reason.strip():
        raise ValidationError("a reason is required for this action")
    return reason.strip()


def _require_typed_confirm(confirm: str | None, expected: str) -> None:
    if (confirm or "").strip() != expected:
        raise ValidationError(
            f"typed confirmation mismatch - type '{expected}' to proceed")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class LoginBody(BaseModel):
    username: str
    password: str


@auth_router.post("/login")
def login(body: LoginBody, response: Response):
    user = authenticate(body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="invalid credentials")
    set_session(response, user)
    return {"username": user.username, "role": user.role}


@auth_router.post("/logout")
def logout(response: Response, user: AdminUser = Depends(current_user)):
    clear_session(response)
    return {"ok": True}


@auth_router.get("/me")
def me(user: AdminUser = Depends(current_user)):
    return {"username": user.username, "role": user.role}


# ---------------------------------------------------------------------------
# Reads (auth required via router dependency)
# ---------------------------------------------------------------------------

@reads_router.get("/status")
def status():
    return ops_service.get_status(central=db.central())


@reads_router.get("/quarantine")
def quarantine(include_resolved: bool = False):
    return ops_service.list_quarantine(central=db.central(), include_resolved=include_resolved)


@reads_router.get("/dead-letter")
def dead_letter():
    return ops_service.list_dead_letter(central=db.central())


@reads_router.get("/drift-reports")
def drift_reports():
    return ops_service.list_drift_reports(central=db.central())


@reads_router.get("/schemas")
def schemas(source_schema: str = "irimsv"):
    return ops_service.get_schema_trees(central=db.central(), staging=db.staging(),
                                        source_schema=source_schema)


@reads_router.get("/proposals")
def proposals(status: str | None = None, limit: int = 200):
    return ops_service.list_proposals(central=db.central(), status=status, limit=limit)


@reads_router.get("/proposals/{proposal_id}")
def proposal(proposal_id: int):
    return get_review(proposal_id, central=db.central())


@reads_router.get("/audit")
def audit_log(limit: int = 200, actor: str | None = None, action: str | None = None):
    return list_audit(limit=limit, actor=actor, action=action)


@reads_router.get("/snapshots/{table}")
def snapshots(table: str):
    return ops_service.staging_snapshots(table, staging=db.staging())


@reads_router.get("/views/proposals")
def view_proposals(status: str | None = None):
    return view_proposer.list_view_proposals(status=status, central=db.central())


# ---------------------------------------------------------------------------
# Data browser (read-only; audited because rows contain personal data)
# ---------------------------------------------------------------------------

def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@data_router.get("/tables")
def data_tables(response: Response, source_schema: str | None = None,
                user: AdminUser = Depends(require_operator)):
    _no_store(response)
    return data_browser_service.list_browsable_tables(
        central=db.central(), staging=db.staging(), source_schema=source_schema)


@data_router.get("/rows")
def data_rows(response: Response, side: str, table: str, page: int = 1,
              size: int = data_browser_service.DEFAULT_PAGE_SIZE,
              sort: str | None = None, direction: str = "asc",
              source_schema: str | None = None,
              user: AdminUser = Depends(require_operator)):
    _no_store(response)
    with audited(user.username, "data_browse", target_type=side, target_id=table,
                 details={"page": page, "size": size, "sort": sort}):
        return data_browser_service.fetch_rows(
            side, table, page=page, size=size, sort=sort, direction=direction,
            central=db.central(), staging=db.staging(), source_schema=source_schema)


@data_router.get("/compare")
def data_compare(response: Response, entity: str, external_reference: str,
                 source_schema: str | None = None,
                 user: AdminUser = Depends(require_operator)):
    _no_store(response)
    with audited(user.username, "data_compare", target_type="entity", target_id=entity,
                 details={"external_reference": external_reference}):
        return data_browser_service.compare_row(
            entity, external_reference, central=db.central(), staging=db.staging(),
            source_schema=source_schema)


# ---------------------------------------------------------------------------
# Quick guarded actions (synchronous, audited)
# ---------------------------------------------------------------------------

class ReplayBody(BaseModel):
    event_id: str


@actions_router.post("/replay")
def replay(body: ReplayBody, user: AdminUser = Depends(require_operator)):
    with audited(user.username, "replay", target_type="outbox_event",
                 target_id=body.event_id):
        return ops_service.replay_event(body.event_id, central=db.central())


class EntityToggleBody(BaseModel):
    entity: str
    target_system: str = "LRMIS"
    enabled: bool
    reason: str | None = None


@actions_router.post("/entity-toggle")
def entity_toggle(body: EntityToggleBody, user: AdminUser = Depends(require_operator)):
    with audited(user.username, "entity_toggle", target_type="entity",
                 target_id=body.entity, reason=body.reason,
                 details={"enabled": body.enabled}):
        return ops_service.set_entity_enabled(
            body.entity, body.target_system, body.enabled, body.reason,
            central=db.central())


class ApproveSchemaBody(BaseModel):
    fingerprint: str
    target_system: str = "LRMIS"
    reason: str | None = None


@actions_router.post("/approve-schema")
def approve_schema(body: ApproveSchemaBody, user: AdminUser = Depends(require_operator)):
    reason = _require_reason(body.reason)
    with audited(user.username, "approve_schema", target_type="schema_version",
                 target_id=body.fingerprint, reason=reason):
        return ops_service.approve_schema(body.fingerprint, body.target_system,
                                          user.username, central=db.central())


class ApproveMappingBody(BaseModel):
    mapping_id: int
    reason: str | None = None


@actions_router.post("/approve-mapping")
def approve_mapping(body: ApproveMappingBody, user: AdminUser = Depends(require_operator)):
    reason = _require_reason(body.reason)
    with audited(user.username, "approve_mapping", target_type="mapping_version",
                 target_id=str(body.mapping_id), reason=reason):
        return ops_service.approve_mapping(body.mapping_id, user.username,
                                           central=db.central())


class ResolveBody(BaseModel):
    proposal_id: int
    source_column: str
    target_column: str
    transform: str = "none"


@actions_router.post("/resolve")
def resolve_field(body: ResolveBody, user: AdminUser = Depends(require_operator)):
    with audited(user.username, "resolve_field", target_type="proposal",
                 target_id=str(body.proposal_id),
                 details={"source_column": body.source_column,
                          "target_column": body.target_column}):
        return resolve(body.proposal_id, body.source_column, body.target_column,
                       body.transform, resolved_by=user.username, central=db.central())


class RestoreSnapshotBody(BaseModel):
    table: str
    snapshot: str | None = None
    reason: str | None = None


@actions_router.post("/restore-snapshot")
def restore_snapshot(body: RestoreSnapshotBody, user: AdminUser = Depends(require_operator)):
    reason = _require_reason(body.reason)
    with audited(user.username, "restore_snapshot", target_type="staging_table",
                 target_id=body.table, reason=reason):
        return ops_service.restore_staging_snapshot(body.table, body.snapshot,
                                                    staging=db.staging())


class GenerateViewBody(BaseModel):
    entity_id: int
    source_schema: str = "irimsv"
    source_table: str
    target_system: str = "LRMIS"


@actions_router.post("/generate-view")
def generate_view(body: GenerateViewBody, user: AdminUser = Depends(require_operator)):
    with audited(user.username, "generate_view", target_type="entity",
                 target_id=f"{body.source_schema}.{body.source_table}",
                 details={"target_system": body.target_system}):
        return view_proposer.propose_view(
            body.entity_id, body.source_schema, body.source_table,
            body.target_system, central=db.central(),
        )


class ApplyViewBody(BaseModel):
    proposal_id: int


@actions_router.post("/apply-view")
def apply_view(body: ApplyViewBody, user: AdminUser = Depends(require_operator)):
    with audited(user.username, "apply_view", target_type="view_proposal",
                 target_id=str(body.proposal_id)):
        return view_proposer.apply_view(body.proposal_id, user.username,
                                        central=db.central())


# ---------------------------------------------------------------------------
# Jobs + SSE
# ---------------------------------------------------------------------------

class JobBody(BaseModel):
    job_type: str
    params: dict = {}
    reason: str | None = None
    confirm: str | None = None


# reason required. onboard_bulk belongs here rather than in the typed tier because
# it skips already-deployed tables outright - no populated staging table can be
# dropped by it, so typed confirmation would be friction without a hazard.
_CONFIRM_MODAL_JOBS = {"deploy", "backfill", "onboard_bulk"}
_ONE_CLICK_JOBS = {"schema_scan", "discover", "propose", "monitor",
                   "worker_run", "reconcile", "replay", "entity_toggle",
                   "cancel_queue"}


def _entity_deployed_for_proposal(proposal_id: int) -> str | None:
    """Returns source_table when the proposal's entity is already deployed."""
    with db.central().connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT e.source_table, e.status FROM integration.onboarding_proposal p
                JOIN integration.onboarding_entity e ON e.id = p.entity_id
                WHERE p.id = %s
            """, (proposal_id,))
            row = cur.fetchone()
    if row and row["status"] == "deployed":
        return row["source_table"]
    return None


@jobs_router.post("/jobs")
def submit_job(body: JobBody, user: AdminUser = Depends(require_operator)):
    job_type, params = body.job_type, dict(body.params)

    if job_type == "refresh":                       # typed-confirmation tier
        _require_reason(body.reason)
        tables = params.get("source_tables", "")
        expected = tables if isinstance(tables, str) else ",".join(tables)
        _require_typed_confirm(body.confirm, expected)
    elif job_type == "refresh_all":                 # typed-confirmation tier
        _require_reason(body.reason)
        _require_typed_confirm(body.confirm, "REFRESH ALL")
    elif job_type == "migration_apply":             # typed-confirmation tier
        _require_reason(body.reason)
        _require_typed_confirm(body.confirm, params.get("filename", ""))
    elif job_type == "deploy":
        _require_reason(body.reason)
        deployed_table = _entity_deployed_for_proposal(int(params.get("proposal_id", 0)))
        if deployed_table:                          # redeploy = typed-confirmation tier
            _require_typed_confirm(body.confirm, deployed_table)
    elif job_type in _CONFIRM_MODAL_JOBS:
        _require_reason(body.reason)

    return jobs.enqueue(job_type, params, user.username, user.role, body.reason)


@jobs_router.get("/jobs")
def all_jobs(user: AdminUser = Depends(current_user)):
    return jobs.list_jobs()


@jobs_router.get("/jobs/{job_id}")
def one_job(job_id: str, user: AdminUser = Depends(current_user)):
    return jobs.get_job(job_id)


def _resume_from(request: Request) -> int | None:
    """Honor the SSE reconnect header so no event is missed across a drop."""
    raw = request.headers.get("last-event-id")
    if raw and raw.isdigit():
        return int(raw)
    return None


async def _event_stream(job_id: str | None, start_after: int = 0):
    last_id = start_after
    while True:
        events = await asyncio.to_thread(jobs.job_events_after, last_id, job_id)
        for e in events:
            last_id = e["id"]
            yield {"event": e["event_type"],
                   "id": str(e["id"]),
                   "data": json.dumps({"job_id": str(e["job_id"]),
                                       "message": e["message"],
                                       "data": e["data"],
                                       "created_at": str(e["created_at"])})}
        await asyncio.sleep(1.0)


@jobs_router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request, user: AdminUser = Depends(current_user)):
    # A single job's history is small and worth replaying, so a client that
    # opens the stream late still sees the run from its first progress tick.
    start_after = _resume_from(request) or 0
    return EventSourceResponse(_event_stream(job_id, start_after))


@jobs_router.get("/events")
async def firehose(request: Request, user: AdminUser = Depends(current_user)):
    # Live tail. Replaying the whole event log on every connect would fire one
    # client-side refetch per historical event and hammer the API.
    resume = _resume_from(request)
    start_after = resume if resume is not None else await asyncio.to_thread(jobs.latest_event_id)
    return EventSourceResponse(_event_stream(None, start_after))


# ---------------------------------------------------------------------------
# Worker loop control
# ---------------------------------------------------------------------------

class WorkerStartBody(BaseModel):
    interval: int = 300
    batch_size: int = 100
    reason: str | None = None


@worker_router.get("/status")
def worker_status(user: AdminUser = Depends(current_user)):
    return jobs.worker_controller.status()


@worker_router.post("/start")
def worker_start(body: WorkerStartBody, user: AdminUser = Depends(require_operator)):
    reason = _require_reason(body.reason)
    with audited(user.username, "worker_loop_start", reason=reason,
                 details={"interval": body.interval, "batch_size": body.batch_size}):
        return jobs.worker_controller.start(body.interval, body.batch_size, user.username)


class WorkerStopBody(BaseModel):
    reason: str | None = None


@worker_router.post("/stop")
def worker_stop(body: WorkerStopBody, user: AdminUser = Depends(require_operator)):
    reason = _require_reason(body.reason)
    with audited(user.username, "worker_loop_stop", reason=reason):
        return jobs.worker_controller.stop(user.username)


# ---------------------------------------------------------------------------
# Migrations (admin role)
# ---------------------------------------------------------------------------

@migrations_router.get("")
def migrations_list(user: AdminUser = Depends(current_user)):
    return migrations_service.list_migrations(central=db.central())


@migrations_router.get("/sql")
def migration_sql(filename: str, user: AdminUser = Depends(require_admin)):
    import os
    return {"filename": filename,
            "dsn": os.environ.get("CENTRAL_DB_URL", "postgresql://localhost:5433/central"),
            "sql": migrations_service.read_migration_sql(filename)}


class MarkAppliedBody(BaseModel):
    filename: str
    reason: str | None = None


@migrations_router.post("/mark-applied")
def mark_applied(body: MarkAppliedBody, user: AdminUser = Depends(require_admin)):
    reason = _require_reason(body.reason)
    with audited(user.username, "migration_mark_applied", target_type="migration",
                 target_id=body.filename, reason=reason):
        return migrations_service.mark_applied(body.filename, user.username,
                                               central=db.central())


# ---------------------------------------------------------------------------
# User management (admin role)
# ---------------------------------------------------------------------------

@users_router.get("")
def list_users(user: AdminUser = Depends(require_admin)):
    with db.central().connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, username, role, is_active, created_at
                FROM integration.admin_user ORDER BY username
            """)
            return [dict(r) for r in cur.fetchall()]


class CreateUserBody(BaseModel):
    username: str
    password: str
    role: str = "operator"


@users_router.post("")
def create_user(body: CreateUserBody, user: AdminUser = Depends(require_admin)):
    if body.role not in ("operator", "admin"):
        raise ValidationError("role must be operator or admin")
    with audited(user.username, "user_create", target_type="admin_user",
                 target_id=body.username, details={"role": body.role}):
        with db.central().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO integration.admin_user (username, password_hash, role)
                    VALUES (%s, %s, %s) RETURNING id
                """, (body.username, hash_password(body.password), body.role))
                new_id = cur.fetchone()[0]
            conn.commit()
        return {"id": new_id, "username": body.username, "role": body.role}


class SetActiveBody(BaseModel):
    is_active: bool


@users_router.post("/{user_id}/active")
def set_user_active(user_id: int, body: SetActiveBody,
                    user: AdminUser = Depends(require_admin)):
    with audited(user.username, "user_set_active", target_type="admin_user",
                 target_id=str(user_id), details={"is_active": body.is_active}):
        with db.central().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE integration.admin_user SET is_active = %s WHERE id = %s
                """, (body.is_active, user_id))
            conn.commit()
        return {"id": user_id, "is_active": body.is_active}
