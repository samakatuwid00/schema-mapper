"""Admin API tests: auth gating, role checks, job allowlist, guard tiers.

These run without a database - DB-touching paths are stubbed or exercised
only up to their pre-DB validation, mirroring the existing pure-unit style
of this suite.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("ADMIN_SESSION_SECRET", "test-secret-for-unit-tests")

from fastapi.testclient import TestClient

from src.admin_api import jobs as jobs_module
from src.admin_api.app import create_app
from src.admin_api.auth import AdminUser, current_user
from src.services.common import ConflictError, ValidationError


@pytest.fixture()
def app():
    return create_app()


@pytest.fixture()
def anon_client(app):
    with TestClient(app) as client:
        yield client


def _client_as(app, role: str):
    app.dependency_overrides[current_user] = lambda: AdminUser(1, "tester", role)
    return TestClient(app)


@pytest.fixture()
def operator_client(app):
    with _client_as(app, "operator") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def admin_client(app):
    with _client_as(app, "admin") as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------

def test_all_api_routes_require_auth(anon_client):
    for method, path in [
        ("get", "/api/status"),
        ("get", "/api/quarantine"),
        ("get", "/api/migrations"),
        ("get", "/api/jobs"),
        ("post", "/api/jobs"),
        ("post", "/api/actions/replay"),
        ("post", "/api/worker/start"),
        ("get", "/api/users"),
    ]:
        response = getattr(anon_client, method)(path, **({"json": {}} if method == "post" else {}))
        assert response.status_code == 401, f"{method} {path} -> {response.status_code}"


def test_login_rejects_bad_signature_cookie(anon_client):
    anon_client.cookies.set("schema_mapper_session", "forged.token.value")
    assert anon_client.get("/api/auth/me").status_code == 401


# ---------------------------------------------------------------------------
# Job allowlist + role enforcement (validated before any DB access)
# ---------------------------------------------------------------------------

def test_unknown_job_type_rejected(operator_client):
    response = operator_client.post(
        "/api/jobs", json={"job_type": "shell_exec", "params": {}})
    assert response.status_code == 422
    assert "unknown job type" in response.json()["detail"]


def test_enqueue_validates_type_before_db():
    with pytest.raises(ValidationError):
        jobs_module.enqueue("drop_database", {}, "tester", "admin")


def test_migration_apply_requires_admin_role(operator_client):
    response = operator_client.post("/api/jobs", json={
        "job_type": "migration_apply",
        "params": {"filename": "sql/003_admin_ui.sql"},
        "reason": "apply admin tables",
        "confirm": "sql/003_admin_ui.sql",
    })
    assert response.status_code == 422
    assert "admin role required" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Guarded action tiers
# ---------------------------------------------------------------------------

def test_refresh_requires_reason(operator_client):
    response = operator_client.post("/api/jobs", json={
        "job_type": "refresh",
        "params": {"source_tables": "customer"},
        "confirm": "customer",
    })
    assert response.status_code == 422
    assert "reason is required" in response.json()["detail"]


def test_refresh_requires_typed_confirmation(operator_client):
    response = operator_client.post("/api/jobs", json={
        "job_type": "refresh",
        "params": {"source_tables": "customer"},
        "reason": "reload after fix",
        "confirm": "customre",  # typo must be rejected
    })
    assert response.status_code == 422
    assert "typed confirmation mismatch" in response.json()["detail"]


def test_migration_apply_requires_typed_filename(admin_client):
    response = admin_client.post("/api/jobs", json={
        "job_type": "migration_apply",
        "params": {"filename": "sql/003_admin_ui.sql"},
        "reason": "apply admin tables",
        "confirm": "wrong-file.sql",
    })
    assert response.status_code == 422
    assert "typed confirmation mismatch" in response.json()["detail"]


def test_onboard_bulk_is_allowlisted():
    assert "onboard_bulk" in jobs_module.JOB_HANDLERS


def test_onboard_bulk_requires_reason(operator_client):
    response = operator_client.post("/api/jobs", json={
        "job_type": "onboard_bulk", "params": {"tables": ["farmers"]}})
    assert response.status_code == 422
    assert "reason is required" in response.json()["detail"]


def test_onboard_bulk_scope_is_order_independent():
    scope = jobs_module._SCOPED["onboard_bulk"]
    assert scope({"source_schema": "irimsv", "tables": ["b", "a"]}) == \
           scope({"source_schema": "irimsv", "tables": " a , b "})


def test_onboard_bulk_rejects_empty_table_list(operator_client, monkeypatch):
    """The handler must refuse an empty batch rather than 'succeed' on nothing."""
    from src.admin_api.jobs import _h_onboard_bulk
    with pytest.raises(ValidationError):
        _h_onboard_bulk({"tables": [], "_actor": "tester"}, None)


def test_data_browse_routes_require_auth(anon_client):
    for path in ("/api/data/tables", "/api/data/rows?side=source&table=x",
                 "/api/data/compare?entity=a&external_reference=b"):
        assert anon_client.get(path).status_code == 401


def test_proposals_list_requires_auth(anon_client):
    assert anon_client.get("/api/proposals").status_code == 401


def test_approve_schema_requires_reason(operator_client):
    response = operator_client.post("/api/actions/approve-schema", json={
        "fingerprint": "abc123", "target_system": "LRMIS"})
    assert response.status_code == 422
    assert "reason is required" in response.json()["detail"]


def test_worker_start_requires_reason(operator_client):
    response = operator_client.post("/api/worker/start", json={
        "interval": 60, "batch_size": 10})
    assert response.status_code == 422


def test_action_bodies_do_not_accept_actor_fields(operator_client, monkeypatch):
    """Spoofed actor/by fields are ignored - identity comes from the session."""
    captured = {}

    def fake_toggle(entity, target_system, enabled, reason, central=None):
        captured.update(entity=entity, enabled=enabled)
        return {"entity": entity, "enabled": enabled}

    from src.admin_api import routers
    monkeypatch.setattr(routers.ops_service, "set_entity_enabled", fake_toggle)
    audits = []
    monkeypatch.setattr("src.admin_api.audit.write_audit",
                        lambda actor, action, **kw: audits.append(actor))
    response = operator_client.post("/api/actions/entity-toggle", json={
        "entity": "customer", "enabled": False, "reason": "test",
        "actor": "mallory", "by": "mallory",
    })
    assert response.status_code == 200
    assert captured["entity"] == "customer"
    assert audits == ["tester"]  # session user, never the body field


# ---------------------------------------------------------------------------
# Worker loop controller state machine
# ---------------------------------------------------------------------------

def test_worker_controller_stop_without_start_conflicts():
    controller = jobs_module.WorkerController()
    with pytest.raises(ConflictError):
        controller.stop("tester")


def test_worker_controller_status_initially_stopped():
    controller = jobs_module.WorkerController()
    assert controller.status()["running"] is False


# ---------------------------------------------------------------------------
# Migration runner pre-DB validation
# ---------------------------------------------------------------------------

def test_unmanaged_migration_file_rejected():
    from src.services.common import NotFoundError
    from src.services.migrations import apply_migration, read_migration_sql
    with pytest.raises(NotFoundError):
        read_migration_sql("sql/evil.sql")
    with pytest.raises(NotFoundError):
        apply_migration("../../etc/passwd", "tester")


def test_migration_sql_readable_for_managed_files():
    """Assert on named files; keyed off MIGRATION_FILES[-1] this broke whenever
    a new migration was appended."""
    from src.services.migrations import MIGRATION_FILES, read_migration_sql

    for filename in MIGRATION_FILES:
        assert read_migration_sql(filename).strip(), f"{filename} is empty"

    scope = read_migration_sql("sql/004_schema_scope_isolation.sql")
    assert "scope_kind" in scope
    assert "lrmis_projection" in scope

    crosswalk = read_migration_sql("sql/005_crosswalk_target_table.sql")
    assert "id_crosswalk" in crosswalk
    assert "target_table" in crosswalk
