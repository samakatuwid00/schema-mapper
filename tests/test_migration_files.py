"""The migration list, the sql/ directory, and the Docker init mounts must agree.

sql/011_recovery.sql, sql/012_entity_source_contract.sql and
sql/013_agent_conversation.sql were each written, committed, and then never added
to `MIGRATION_FILES` -- so the runner neither applied nor tracked them, and 012
silently never reached any database. The Docker init mounts had drifted the same
way, stopping at 008. These tests fail the next time either list is forgotten.
"""
import re

from src.services.migrations import MIGRATION_FILES, REPO_ROOT

# Not a migration: it hard-codes the sample `irimsv.customer` table, so it must
# never run against a central whose `irimsv` holds a restored real source.
DEMO_SQL = "sql/demo_customer_cdc.sql"

MOUNT_RE = re.compile(r"^\s*-\s*\./(sql/[^:]+):/docker-entrypoint-initdb\.d/", re.M)


def _central_db_mounts() -> list[str]:
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    central = compose.split("central_db:", 1)[1].split("\n  lrmis", 1)[0]
    return MOUNT_RE.findall(central)


def test_every_numbered_sql_file_is_a_managed_migration():
    on_disk = sorted(f"sql/{p.name}" for p in (REPO_ROOT / "sql").glob("[0-9][0-9][0-9]_*.sql"))

    assert on_disk == [f for f in MIGRATION_FILES if f not in ("sql/central_db_init.sql",)]


def test_docker_init_mounts_every_managed_migration_in_order():
    mounted = _central_db_mounts()

    assert [m for m in mounted if m != DEMO_SQL] == MIGRATION_FILES


def test_demo_trigger_is_mounted_last_and_is_not_a_managed_migration():
    mounted = _central_db_mounts()

    assert DEMO_SQL not in MIGRATION_FILES
    # It depends on integration.outbox and the enum types the foundation creates.
    assert mounted[-1] == DEMO_SQL
