"""Dialect-aware multi-table writer (§7.1-7.4).

Generalises `lrmis_writer.write_source_row` so the same fan-out logic writes into
any engine the `Dialect` covers. The only real engine differences are:

* **identifier quoting** — `dialect.quote()` (backticks vs double-quotes);
* **reading back a generated primary key** — Postgres `INSERT ... RETURNING pk`
  (`dialect.supports_returning`), MySQL `cursor.lastrowid`.

Everything else — parents-first ordering, FK propagation, the crosswalk
idempotency, app-assigned ids, reference-table resolution — is engine-agnostic
and reused from `lrmis_writer`. LRMIS specifics (`app_assigned_tables`,
`id_start`) are constructor config, not hardcoded, so a different target loads
different values (the §8 plugin direction).
"""
from __future__ import annotations

from ..dialect import cast_value, native_to_generic
from ..lrmis_registry import LrmisRegistry, get_registry
from ..adapters.lrmis_plugin import LRMIS
from ..lrmis_writer import (
    ReferenceRowNotFound, UnknownTargetTable,
    _apply_foreign_keys, _crosswalk_lookup, _crosswalk_record,
    allocate_id, crosswalk_rows_for_entity,
)


class GenericWriter:
    def __init__(self, dialect, registry: LrmisRegistry | None = None, *,
                 plugin=LRMIS, app_assigned_tables=None, id_start: int | None = None):
        """`plugin` supplies the target's domain config (app-assigned tables, id
        range). The core writer holds no knowledge of `station`/LRMIS — a
        different target passes a different `TargetPlugin` (or none). Explicit
        `app_assigned_tables`/`id_start` override the plugin when given."""
        self.d = dialect
        self.registry = registry or get_registry()
        self.app_assigned_tables = frozenset(
            app_assigned_tables if app_assigned_tables is not None
            else plugin.app_assigned_id_tables)
        self.id_start = id_start if id_start is not None else plugin.id_sequence_start
        self._app_assign_non_autoinc = getattr(plugin, "app_assign_non_autoincrement", False)
        self._reference_only = frozenset(getattr(plugin, "reference_tables", {}) or {})

    def _is_app_assigned(self, table: str) -> bool:
        """A table the pipeline creates rows in via an allocated id."""
        if table in self.app_assigned_tables:
            return True
        # Target whose PKs are app-assigned (no serial/identity): any writable,
        # non-DB-generated table is app-assigned unless it is a declared lookup.
        return (self._app_assign_non_autoinc
                and table not in self._reference_only
                and self.registry.is_reference_table(table))

    def _is_reference(self, table: str) -> bool:
        """A resolve-only lookup the pipeline never inserts into."""
        if table in self._reference_only:
            return True
        return self.registry.is_reference_table(table) and not self._is_app_assigned(table)

    # -- target SQL primitives (dialect-aware) ------------------------------

    def _pk(self, table: str) -> str | None:
        meta = self.registry.get_table(table)
        return meta.primary_key[0] if meta.primary_key else None

    def _cast_row(self, table: str, values: dict) -> dict:
        """Coerce each value toward its target column's generic type, so a
        cross-engine mismatch (e.g. a numeric string into an INTEGER column) is
        fixed before the insert. Conservative — see `dialect.cast_value`."""
        meta = self.registry.get_table(table)
        out = {}
        for col, val in values.items():
            column = meta.get_column(col)
            if column is None:
                out[col] = val
                continue
            out[col] = cast_value(val, native_to_generic(self.d.engine, column.data_type))
        return out

    def _insert_returning(self, tconn, table: str, values: dict):
        """Insert and return the generated primary key (RETURNING or lastrowid)."""
        values = self._cast_row(table, values)
        cols = list(values)
        params = tuple(values[c] for c in cols)
        pk = self._pk(table)
        if self.d.supports_returning and pk:
            sql = self.d.insert_returning_sql(table, cols, pk)
            with tconn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                return row[0] if row else None
        sql = self.d.insert_sql(table, cols)
        with tconn.cursor() as cur:
            cur.execute(sql, params)
            return cur.lastrowid

    def _insert_explicit(self, tconn, table: str, values: dict) -> None:
        """Insert with an explicit (app-assigned) primary key — no id read back."""
        values = self._cast_row(table, values)
        cols = list(values)
        sql = self.d.insert_sql(table, cols)
        with tconn.cursor() as cur:
            cur.execute(sql, tuple(values[c] for c in cols))

    def _update(self, tconn, table: str, pk: str, pk_value, values: dict) -> None:
        values = self._cast_row(table, values)
        assignable = {k: v for k, v in values.items() if k != pk}
        if not assignable:
            return
        sets = ", ".join(f"{self.d.quote(c)} = {self.d.placeholder()}" for c in assignable)
        sql = (f"UPDATE {self.d.quote(table)} SET {sets} "
               f"WHERE {self.d.quote(pk)} = {self.d.placeholder()}")
        with tconn.cursor() as cur:
            cur.execute(sql, (*assignable.values(), pk_value))

    def _resolve_reference_id(self, tconn, table: str, values: dict):
        """Find an existing reference row's PK; never insert (dialect-aware)."""
        pk = self._pk(table)
        if pk is None:
            raise ReferenceRowNotFound(f"{table} has no primary key to resolve")
        ph = self.d.placeholder()
        if values.get(pk) is not None:
            sql = (f"SELECT {self.d.quote(pk)} FROM {self.d.quote(table)} "
                   f"WHERE {self.d.quote(pk)} = {ph} LIMIT 1")
            with tconn.cursor() as cur:
                cur.execute(sql, (values[pk],))
                row = cur.fetchone()
            if row:
                return row[0]
            raise ReferenceRowNotFound(
                f"{table}.{pk}={values[pk]!r} does not exist; {table} is read-only")
        criteria = {k: v for k, v in values.items() if v is not None and k != pk}
        if not criteria:
            raise ReferenceRowNotFound(
                f"cannot resolve a row in reference table {table}: no lookup values mapped")
        where = " AND ".join(f"{self.d.quote(c)} = {ph}" for c in criteria)
        sql = (f"SELECT {self.d.quote(pk)} FROM {self.d.quote(table)} "
               f"WHERE {where} LIMIT 1")
        with tconn.cursor() as cur:
            cur.execute(sql, tuple(criteria.values()))
            row = cur.fetchone()
        if not row:
            raise ReferenceRowNotFound(
                f"no row in reference table {table} matches {criteria}")
        return row[0]

    def record_delivery_audit(self, tconn, event, active) -> None:
        """Upsert the delivery-audit envelope, dialect-aware (Postgres `ON
        CONFLICT` / MySQL `ON DUPLICATE KEY`). The `delivery_audit` table must
        exist in the target (created by target setup)."""
        from datetime import datetime, timezone
        updated = event.get("source_updated_at")
        if isinstance(updated, datetime):
            updated = updated.astimezone(timezone.utc).replace(tzinfo=None)
        row = {
            "event_id": str(event["event_id"]),
            "external_reference": str(event["external_reference"]),
            "source_system": event.get("source_system"),
            "operation": event.get("operation"),
            "source_updated_at": updated,
            "mapping_version": event.get("mapping_version"),
            "payload_checksum": event.get("payload_checksum"),
            "active": 1 if active else 0,
            "accepted_at": datetime.now(timezone.utc).replace(tzinfo=None),
        }
        cols = list(row)
        sql = self.d.upsert_sql("delivery_audit", cols, ["event_id"])
        with tconn.cursor() as cur:
            cur.execute(sql, tuple(row[c] for c in cols))

    def row_exists(self, tconn, table: str, target_id) -> bool:
        """Dialect-aware existence check used by cross-entity FK resolution."""
        pk = self._pk(table)
        if not pk:
            return True
        sql = (f"SELECT 1 FROM {self.d.quote(table)} "
               f"WHERE {self.d.quote(pk)} = {self.d.placeholder()} LIMIT 1")
        with tconn.cursor() as cur:
            cur.execute(sql, (target_id,))
            return cur.fetchone() is not None

    # -- app-assigned id path (e.g. station) --------------------------------

    def _write_app_assigned(self, tconn, central_conn, table, values, *,
                            source_system, source_entity, external_reference,
                            target_system, event_id=None):
        pk = self._pk(table)
        existing = _crosswalk_lookup(central_conn, source_system, source_entity,
                                     external_reference, target_system, table)
        if existing is not None:
            self._update(tconn, table, pk, existing, values)
            return existing
        try:
            matched = self._resolve_reference_id(tconn, table, values)
        except ReferenceRowNotFound:
            matched = None
        if matched is not None:
            _crosswalk_record(central_conn, source_system, source_entity,
                              external_reference, target_system, table, matched, event_id)
            return matched
        new_id = allocate_id(central_conn, table, self.id_start)
        self._insert_explicit(tconn, table, {**values, pk: new_id})
        _crosswalk_record(central_conn, source_system, source_entity,
                          external_reference, target_system, table, new_id, event_id)
        return new_id

    # -- write one source row across N target tables ------------------------

    def write_row(self, tconn, central_conn, *, source_entity: str,
                  external_reference: str, values_by_table: dict[str, dict],
                  source_system: str = "IRIMSV_REGION_V",
                  target_system: str = "LRMIS", event_id=None) -> dict[str, object]:
        for table in values_by_table:
            if not self.registry.has_table(table):
                raise UnknownTargetTable(f"{table!r} is not part of the target schema")

        order = self.registry.topological_order(list(values_by_table))
        target_ids: dict[str, object] = {}
        for table in order:
            values = _apply_foreign_keys(self.registry, table,
                                         values_by_table[table], target_ids)

            # A declared reference-only lookup always resolves, even if the
            # mapping supplies its id (resolve_reference_id verifies existence).
            if table in self._reference_only:
                target_ids[table] = self._resolve_reference_id(tconn, table, values)
                continue

            pk = self._pk(table)
            # Source-carried id: the mapping supplies the primary key itself (e.g.
            # a target whose ids are app-owned strings/uuids, not DB-generated).
            # Insert with that id directly — don't allocate or resolve.
            if pk and values.get(pk) is not None and not self.registry.get_table(table).auto_increment_column:
                existing = _crosswalk_lookup(central_conn, source_system, source_entity,
                                             external_reference, target_system, table)
                if existing is not None:
                    self._update(tconn, table, pk, existing, values)
                    target_ids[table] = existing
                else:
                    self._insert_explicit(tconn, table, values)
                    _crosswalk_record(central_conn, source_system, source_entity,
                                      external_reference, target_system, table,
                                      values[pk], event_id)
                    target_ids[table] = values[pk]
                continue

            if self._is_app_assigned(table):
                target_ids[table] = self._write_app_assigned(
                    tconn, central_conn, table, values,
                    source_system=source_system, source_entity=source_entity,
                    external_reference=external_reference,
                    target_system=target_system, event_id=event_id)
                continue

            if self._is_reference(table):
                target_ids[table] = self._resolve_reference_id(tconn, table, values)
                continue

            pk = self._pk(table)
            existing = _crosswalk_lookup(central_conn, source_system, source_entity,
                                         external_reference, target_system, table)
            if existing is not None and pk:
                self._update(tconn, table, pk, existing, values)
                target_ids[table] = existing
            else:
                target_id = self._insert_returning(tconn, table, values)
                _crosswalk_record(central_conn, source_system, source_entity,
                                  external_reference, target_system, table,
                                  target_id, event_id)
                target_ids[table] = target_id
        return target_ids

    # -- refresh support ----------------------------------------------------

    def delete_entity_rows(self, tconn, central_conn, *, source_entity: str,
                           target_system: str = "LRMIS") -> dict[str, int]:
        """Delete only pipeline-written rows for an entity, children first.
        Reference tables (and app-assigned `station`, conservatively) are
        skipped — same policy as `lrmis_writer.delete_entity_rows`."""
        rows = crosswalk_rows_for_entity(central_conn, source_entity, target_system)
        if not rows:
            return {}
        by_table: dict[str, list] = {}
        for table, target_id in rows:
            if not self.registry.has_table(table) or self.registry.is_reference_table(table):
                continue
            by_table.setdefault(table, []).append(target_id)

        deleted: dict[str, int] = {}
        for table in reversed(self.registry.topological_order(list(by_table))):
            pk = self._pk(table)
            if not pk:
                continue
            ids = by_table[table]
            placeholders = ", ".join([self.d.placeholder()] * len(ids))
            sql = (f"DELETE FROM {self.d.quote(table)} "
                   f"WHERE {self.d.quote(pk)} IN ({placeholders})")
            with tconn.cursor() as cur:
                cur.execute(sql, tuple(ids))
                deleted[table] = cur.rowcount

        with central_conn.cursor() as cur:
            cur.execute("""
                DELETE FROM integration.id_crosswalk
                WHERE source_entity = %s AND target_system = %s AND target_table = ANY(%s)
            """, (source_entity, target_system, list(by_table)))
        return deleted

    def truncate_and_rebuild(self, tconn, *, tables: list[str] | None = None) -> list[str]:
        """TRUNCATE pipeline-written tables children-first (skips seeded
        reference tables). Returns the truncated tables in the order issued.
        Note: FK enforcement may require the caller to disable constraint checks
        (MySQL `SET FOREIGN_KEY_CHECKS=0` / Postgres `TRUNCATE ... CASCADE`);
        the children-first order avoids that where the engine allows it."""
        names = tables or self.registry.table_names
        order = [t for t in reversed(self.registry.topological_order(list(names)))
                 if not self.registry.is_reference_table(t)]
        with tconn.cursor() as cur:
            for table in order:
                cur.execute(self.d.truncate_sql(table))
        return order
