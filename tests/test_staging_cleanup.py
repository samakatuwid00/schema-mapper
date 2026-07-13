"""Staging cleanup: orphan sweep + entity retire logic.

Mocks the connectors so the tests run without a live Postgres/MySQL, and
patches snapshot/drop helpers (covered elsewhere) to focus on the decision
logic: which tables are orphans, and what retire_entity mutates.
"""
from __future__ import annotations

from unittest.mock import patch

from src.services import staging_cleanup as cleanup
from src.services import NotFoundError


class FakeCentral:
    def __init__(self, active=None, row=None):
        self._active = active or []
        self._row = row
        self.closed = False

    def connection(self):
        outer = self

        class Ctx:
            def __enter__(self2):
                return self2

            def __exit__(self2, *a):
                pass

            def commit(self2):
                pass

            def cursor(self):
                class Cur:
                    def __enter__(self3):
                        return self3

                    def __exit__(self3, *a):
                        pass

                    def execute(self3, *a):
                        pass

                    def fetchall(self3):
                        return [(t,) for t in outer._active]

                    def fetchone(self3):
                        return outer._row

                return Cur()

        return Ctx()

    def close(self):
        self.closed = True


class FakeStaging:
    def __init__(self, tables):
        self._tables = tables
        self.closed = False

    def information_schema(self, schema=None):
        return [{"table_name": t} for t in self._tables]

    def table_names(self, schema=None):
        return list(self._tables)

    def close(self):
        self.closed = True

    def connection(self):
        class Ctx:
            def __enter__(self2):
                return self2

            def __exit__(self2, *a):
                pass

            def cursor(self):
                class Cur:
                    def __enter__(self3):
                        return self3

                    def __exit__(self3, *a):
                        pass

                    def execute(self3, *a):
                        pass

                return Cur()

        return Ctx()


def test_sweep_dry_run_reports_orphans_without_dropping():
    central = FakeCentral(active={"irimsv_a_staging"})
    staging = FakeStaging(["irimsv_a_staging", "irimsv_b_staging", "irimsv_c_staging"])
    with patch.object(cleanup, "snapshot_staging_table") as snap, patch.object(cleanup, "drop_staging_table") as drop:
        snap.return_value = "snap"
        res = cleanup.sweep_orphans(dry_run=True, central=central, staging=staging)
    assert res["orphans_found"] == ["irimsv_b_staging", "irimsv_c_staging"]
    assert res["dropped"] == []
    drop.assert_not_called()


def test_sweep_drops_only_orphans():
    central = FakeCentral(active={"irimsv_a_staging"})
    staging = FakeStaging(["irimsv_a_staging", "irimsv_b_staging", "irimsv_c_staging"])
    with patch.object(cleanup, "snapshot_staging_table") as snap, patch.object(cleanup, "drop_staging_table") as drop:
        snap.return_value = "snap"
        res = cleanup.sweep_orphans(dry_run=False, central=central, staging=staging)
    assert set(res["dropped"]) == {"irimsv_b_staging", "irimsv_c_staging"}
    # The live table is never touched.
    drop.assert_any_call(staging, "irimsv_b_staging")
    drop.assert_any_call(staging, "irimsv_c_staging")


def test_sweep_ignores_view_tables():
    central = FakeCentral(active=set())
    staging = FakeStaging(["irimsv_x_for_lrmis"])
    with patch.object(cleanup, "snapshot_staging_table") as snap, patch.object(cleanup, "drop_staging_table") as drop:
        res = cleanup.sweep_orphans(dry_run=False, central=central, staging=staging)
    assert res["orphans_found"] == []
    drop.assert_not_called()


def test_retire_dry_run_does_not_drop():
    central = FakeCentral(row=(12, "farmers", "irimsv_farmers_staging", "deployed"))
    staging = FakeStaging([])
    with patch.object(cleanup, "snapshot_staging_table") as snap, patch.object(cleanup, "drop_staging_table") as drop:
        res = cleanup.retire_entity(12, dry_run=True, central=central, staging=staging)
    assert res["dropped"] is False
    drop.assert_not_called()


def test_retire_drops_and_disables():
    central = FakeCentral(row=(12, "farmers", "irimsv_farmers_staging", "deployed"))
    staging = FakeStaging([])
    with patch.object(cleanup, "snapshot_staging_table") as snap, patch.object(cleanup, "drop_staging_table") as drop:
        snap.return_value = "snap"
        res = cleanup.retire_entity(12, dry_run=False, central=central, staging=staging)
    assert res["dropped"] is True
    assert res["snapshot"] == "snap"
    drop.assert_called_once_with(staging, "irimsv_farmers_staging")


def test_retire_unknown_entity_raises():
    central = FakeCentral(row=None)
    staging = FakeStaging([])
    with patch.object(cleanup, "snapshot_staging_table"), patch.object(cleanup, "drop_staging_table"):
        try:
            cleanup.retire_entity(999, central=central, staging=staging)
            assert False, "expected NotFoundError"
        except NotFoundError:
            pass


def test_sweep_uses_table_names_not_columns():
    """Orphan detection reads information_schema.tables via table_names()."""
    central = FakeCentral(active={"irimsv_a_staging"})

    class OnlyTableNames(FakeStaging):
        def information_schema(self, schema=None):
            raise AssertionError("sweep must not scan information_schema.columns")

    staging = OnlyTableNames(["irimsv_a_staging", "irimsv_b_staging"])
    with patch.object(cleanup, "snapshot_staging_table"), patch.object(cleanup, "drop_staging_table"):
        res = cleanup.sweep_orphans(dry_run=True, central=central, staging=staging)
    assert res["orphans_found"] == ["irimsv_b_staging"]


def test_sweep_closes_connectors_it_owns():
    central = FakeCentral(active=set())
    staging = FakeStaging([])
    with patch.object(cleanup, "PostgresCentralConnector", lambda: central), \
         patch.object(cleanup, "MySQLStagingConnector", lambda: staging):
        cleanup.sweep_orphans(dry_run=True)
    assert central.closed is True and staging.closed is True


def test_retire_closes_connectors_it_owns():
    central = FakeCentral(row=(12, "farmers", "irimsv_farmers_staging", "deployed"))
    staging = FakeStaging([])
    with patch.object(cleanup, "PostgresCentralConnector", lambda: central), \
         patch.object(cleanup, "MySQLStagingConnector", lambda: staging), \
         patch.object(cleanup, "snapshot_staging_table", return_value="snap"), \
         patch.object(cleanup, "drop_staging_table"):
        cleanup.retire_entity(12, dry_run=True)
    assert central.closed is True and staging.closed is True
