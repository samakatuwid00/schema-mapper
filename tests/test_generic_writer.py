"""Tests for the dialect-aware GenericWriter (§7).

Engine-agnostic: fake target/central connections record SQL and hand out ids, so
both the Postgres (RETURNING) and MySQL (lastrowid) id-retrieval paths are
exercised without a live database.
"""
from src.lrmis_registry import LrmisRegistry
from src.dialect import get_dialect
from src.delivery import GenericWriter


def _col(table, name, pos, key="", extra="", nullable="YES", dtype="int"):
    return {"table_name": table, "column_name": name, "data_type": dtype,
            "is_nullable": nullable, "ordinal_position": pos,
            "column_key": key, "extra": extra, "column_default": None}


# region (auto-inc lookup) <- school (auto-inc, FK) ; psgc (reference) ; station (app-assigned)
REG = LrmisRegistry.from_discovery(
    [
        _col("region", "id", 1, key="PRI", extra="auto_increment"),
        _col("region", "name", 2),
        _col("school", "id", 1, key="PRI", extra="auto_increment"),
        _col("school", "name", 2),
        _col("school", "region_id", 3, nullable="NO"),
        _col("psgc", "id", 1, key="PRI"),          # no auto-increment -> reference
        _col("station", "id", 1, key="PRI"),        # no auto-increment -> app-assigned
        _col("station", "name", 2),
    ],
    [{"table_name": "school", "column_name": "region_id",
      "ref_table": "region", "ref_column": "id"}],
)


class _TCursor:
    def __init__(self, conn):
        self.conn, self._res, self.lastrowid, self.rowcount = conn, None, None, 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        self.conn.log.append((s, tuple(params)))
        up = s.upper()
        if " RETURNING " in up:
            self._res = (self.conn._next(),)
        elif up.startswith("INSERT"):
            self.lastrowid = self.conn._next()
            self._res = None
        elif up.startswith("SELECT"):
            self._res = self.conn.selects.pop(0) if self.conn.selects else None
        else:
            self._res = None

    def fetchone(self):
        return self._res


class FakeTarget:
    def __init__(self, start=100, selects=None):
        self.log, self._id, self.selects = [], start, list(selects or [])

    def _next(self):
        self._id += 1
        return self._id

    def cursor(self):
        return _TCursor(self)


class _CCursor:
    def __init__(self, conn):
        self.conn, self._res = conn, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        up = " ".join(sql.split()).upper()
        self.conn.log.append((up, tuple(params)))
        if up.startswith("SELECT") and "ID_CROSSWALK" in up:
            self._res = None
            table = params[4]
            if table in self.conn.lookups:
                self._res = (self.conn.lookups[table],)
        elif "INSERT INTO INTEGRATION.ID_CROSSWALK" in up:
            self.conn.records.append(params)
            self._res = None
        elif "INSERT INTO INTEGRATION.ID_SEQUENCE" in up:
            self.conn._alloc += 1
            self._res = (self.conn._alloc,)
        else:
            self._res = None

    def fetchone(self):
        return self._res


class FakeCentral:
    def __init__(self, lookups=None, alloc_start=10_000_000):
        self.log, self.lookups, self.records = [], dict(lookups or {}), []
        self._alloc = alloc_start

    def cursor(self):
        return _CCursor(self)


def _write(writer, target, central, values_by_table, ext="ext1"):
    return writer.write_row(target, central, source_entity="schools",
                            external_reference=ext, values_by_table=values_by_table)


def _target_inserts(target):
    return [(s, p) for s, p in target.log if s.upper().startswith("INSERT")]


def test_postgres_parents_first_returning_and_fk_propagation():
    w = GenericWriter(get_dialect("postgres"), REG)
    t, c = FakeTarget(start=100), FakeCentral()
    ids = _write(w, t, c, {"school": {"name": "S"}, "region": {"name": "R"}})

    inserts = _target_inserts(t)
    # parents first: region inserted before school
    assert 'INSERT INTO "region"' in inserts[0][0]
    assert 'INSERT INTO "school"' in inserts[1][0]
    # region uses RETURNING "id"; school FK filled from region's returned id
    assert 'RETURNING "id"' in inserts[0][0]
    assert ids["region"] == 101 and ids["school"] == 102
    assert '"region_id"' in inserts[1][0] and 101 in inserts[1][1]
    # a crosswalk row recorded per inserted table
    assert len(c.records) == 2


def test_mysql_uses_lastrowid_not_returning():
    w = GenericWriter(get_dialect("mysql"), REG)
    t, c = FakeTarget(start=200), FakeCentral()
    ids = _write(w, t, c, {"school": {"name": "S"}, "region": {"name": "R"}})
    assert all("RETURNING" not in s.upper() for s, _ in t.log)
    assert "`region`" in _target_inserts(t)[0][0]      # backtick quoting
    assert ids["region"] == 201 and ids["school"] == 202
    assert 201 in _target_inserts(t)[1][1]             # FK propagated via lastrowid


def test_idempotent_update_when_crosswalk_has_existing_ids():
    w = GenericWriter(get_dialect("postgres"), REG)
    t = FakeTarget()
    c = FakeCentral(lookups={"region": 501, "school": 777})
    ids = _write(w, t, c, {"school": {"name": "S"}, "region": {"name": "R"}})
    assert ids["region"] == 501 and ids["school"] == 777
    assert any(s.upper().startswith("UPDATE") for s, _ in t.log)
    assert not any(s.upper().startswith("INSERT") for s, _ in t.log)
    assert c.records == []          # nothing new inserted -> no new crosswalk rows


def test_reference_table_is_resolved_never_inserted():
    w = GenericWriter(get_dialect("postgres"), REG)
    t, c = FakeTarget(selects=[(5,)]), FakeCentral()
    ids = _write(w, t, c, {"psgc": {"id": 5}})
    assert ids["psgc"] == 5
    assert any(s.upper().startswith("SELECT") for s, _ in t.log)
    assert not any(s.upper().startswith("INSERT") for s, _ in t.log)


def test_app_assigned_allocates_id_and_inserts_explicit():
    w = GenericWriter(get_dialect("postgres"), REG)
    t, c = FakeTarget(), FakeCentral(alloc_start=10_000_000)   # station has no natural match
    ids = _write(w, t, c, {"station": {"name": "X"}})
    assert ids["station"] == 10_000_001
    ins = _target_inserts(t)[0]
    assert 'INSERT INTO "station"' in ins[0] and 10_000_001 in ins[1]  # id explicit
    assert c.records                                            # crosswalk recorded


def test_source_carried_id_inserts_with_that_id():
    # target PK provided by the mapping (app-owned string/uuid id, no auto-inc):
    # insert directly with that id, not allocate or resolve.
    reg = LrmisRegistry.from_discovery(
        [_col("author", "id", 1, key="PRI", dtype="varchar(36)"),
         _col("author", "name", 2, dtype="varchar(50)")], [])
    w = GenericWriter(get_dialect("postgres"), reg)   # default plugin: author is reference-ish
    t, c = FakeTarget(), FakeCentral()
    ids = _write(w, t, c, {"author": {"id": "uuid-1", "name": "Rizal"}})
    assert ids["author"] == "uuid-1"
    ins = _target_inserts(t)[0]
    assert 'INSERT INTO "author"' in ins[0] and "uuid-1" in ins[1]
    assert c.records                       # crosswalk recorded for idempotency


def test_app_assign_non_autoincrement_feature():
    # a synthetic target whose non-DB-generated tables must get ALLOCATED ids
    from src.adapters.lrmis_plugin import TargetPlugin
    plugin = TargetPlugin(name="ALLOC", app_assigned_id_tables=frozenset(),
                          id_sequence_start=1_000_000_000,
                          app_assign_non_autoincrement=True)
    reg = LrmisRegistry.from_discovery(
        [_col("thing", "id", 1, key="PRI"), _col("thing", "name", 2)], [])  # no auto-inc
    default_w = GenericWriter(get_dialect("postgres"), reg)
    alloc_w = GenericWriter(get_dialect("postgres"), reg, plugin=plugin)
    assert default_w._is_reference("thing") and not default_w._is_app_assigned("thing")
    assert alloc_w._is_app_assigned("thing") and not alloc_w._is_reference("thing")
    # a row with no supplied PK -> allocate an id and insert
    t, c = FakeTarget(), FakeCentral(alloc_start=1_000_000_000)
    ids = _write(alloc_w, t, c, {"thing": {"name": "A"}})
    assert ids["thing"] == 1_000_000_001
    assert 1_000_000_001 in _target_inserts(t)[0][1]


def test_old_lrmis_uses_source_carried_ids_not_allocation():
    # old-lrmis PKs are source-carried strings: mapping supplies the id -> insert it
    from src.adapters.lrmis_plugin import OLD_LRMIS
    reg = LrmisRegistry.from_discovery(
        [_col("author", "id", 1, key="PRI", dtype="varchar(36)"),
         _col("author", "name", 2, dtype="varchar(50)")], [])
    w = GenericWriter(get_dialect("postgres"), reg, plugin=OLD_LRMIS)
    assert not w._is_app_assigned("author")            # no allocation
    assert "region" in w._reference_only               # seeded lookup declared
    t, c = FakeTarget(), FakeCentral()
    ids = _write(w, t, c, {"author": {"id": "uuid-9", "name": "A"}})
    assert ids["author"] == "uuid-9"
    assert "uuid-9" in _target_inserts(t)[0][1]


def test_writer_is_plugin_driven():
    from src.adapters.lrmis_plugin import LRMIS, TargetPlugin
    # default: LRMIS plugin marks station app-assigned
    assert "station" in GenericWriter(get_dialect("postgres"), REG).app_assigned_tables
    # a different target plugin: no app-assigned tables, different id range
    other = TargetPlugin(name="OTHER", app_assigned_id_tables=frozenset(), id_sequence_start=1)
    w = GenericWriter(get_dialect("postgres"), REG, plugin=other)
    assert w.app_assigned_tables == frozenset()
    assert w.id_start == 1
    # without an app-assigned rule, station is classified reference (resolve-only)
    assert w._is_reference("station") and not w._is_app_assigned("station")


def test_resolve_plugin_is_config_driven(monkeypatch):
    """The worker/schema-swap paths pick the plugin by LRMIS_TARGET_PLUGIN so a
    swap to old-lrmis resolves its seeded lookups instead of inserting them."""
    from src.adapters.lrmis_plugin import get_plugin, resolve_plugin, LRMIS, OLD_LRMIS
    assert get_plugin(None) is LRMIS                     # unset -> LRMIS
    assert get_plugin("old_lrmis") is OLD_LRMIS          # case-insensitive
    assert get_plugin("nope") is LRMIS                   # unknown -> safe default
    monkeypatch.delenv("LRMIS_TARGET_PLUGIN", raising=False)
    assert resolve_plugin() is LRMIS
    monkeypatch.setenv("LRMIS_TARGET_PLUGIN", "OLD_LRMIS")
    assert resolve_plugin() is OLD_LRMIS
    # and the selected plugin makes a seeded lookup resolve-only, not app-assigned
    reg = LrmisRegistry.from_discovery(
        [_col("region", "id", 1, key="PRI", dtype="varchar(10)")], [])
    w = GenericWriter(get_dialect("postgres"), reg, plugin=resolve_plugin())
    assert w._is_reference("region") and not w._is_app_assigned("region")


def test_truncate_and_rebuild_children_first_skipping_reference():
    w = GenericWriter(get_dialect("postgres"), REG)
    t = FakeTarget()
    out = w.truncate_and_rebuild(t, tables=["region", "school", "psgc"])
    assert out == ["school", "region"]        # children first; psgc (reference) skipped
    assert [s for s, _ in t.log] == ['TRUNCATE TABLE "school"', 'TRUNCATE TABLE "region"']


def test_writer_casts_values_to_target_column_type():
    reg = LrmisRegistry.from_discovery(
        [_col("t", "id", 1, key="PRI", extra="auto_increment"),
         _col("t", "n", 2)],   # data_type "int" -> INTEGER
        [])
    w = GenericWriter(get_dialect("postgres"), reg)
    t, c = FakeTarget(), FakeCentral()
    w.write_row(t, c, source_entity="e", external_reference="x",
                values_by_table={"t": {"n": "77"}})
    params = _target_inserts(t)[0][1]
    assert 77 in params and "77" not in params   # numeric string cast to int


def test_writer_casts_values_to_target_generic_type():
    reg = LrmisRegistry.from_discovery(
        [_col("t", "id", 1, key="PRI", extra="auto_increment"),
         _col("t", "code", 2)], [])          # code is data_type "int"
    w = GenericWriter(get_dialect("postgres"), reg)
    t, c = FakeTarget(), FakeCentral()
    w.write_row(t, c, source_entity="x", external_reference="e",
                values_by_table={"t": {"code": "42"}})
    _, params = _target_inserts(t)[0]
    assert 42 in params and "42" not in params   # numeric string cast to INTEGER


def test_row_exists_is_dialect_aware():
    w = GenericWriter(get_dialect("postgres"), REG)
    hit = FakeTarget(selects=[(1,)])
    assert w.row_exists(hit, "region", 5) is True
    assert any('SELECT 1 FROM "region" WHERE "id"' in s for s, _ in hit.log)
    miss = FakeTarget(selects=[None])
    assert w.row_exists(miss, "region", 999) is False


# --- 7.5: refresh_entity routes delivery through the injected writer --------

class _FakeWriter:
    def __init__(self):
        self.calls = []

    def delete_entity_rows(self, t, c, *, source_entity, target_system="LRMIS"):
        self.calls.append(("delete", source_entity))
        return {}

    def write_row(self, t, c, **kw):
        self.calls.append(("write", kw["values_by_table"]))
        return {"region": 1}

    def row_exists(self, t, table, target_id):
        return True


def test_record_delivery_audit_is_dialect_upsert():
    w = GenericWriter(get_dialect("postgres"), REG)
    t = FakeTarget()
    event = {"event_id": "e1", "external_reference": "x1", "source_system": "S",
             "operation": "upsert", "payload_checksum": "c", "mapping_version": 1}
    w.record_delivery_audit(t, event, active=True)
    sql, params = t.log[0]
    assert 'INSERT INTO "delivery_audit"' in sql
    assert 'ON CONFLICT ("event_id") DO UPDATE SET' in sql
    assert "e1" in params


def test_deliver_event_routes_through_injected_writer():
    from src.lrmis_delivery import deliver_event
    reg = LrmisRegistry.from_discovery(
        [_col("region", "id", 1, key="PRI", extra="auto_increment"),
         _col("region", "name", 2)], [])

    class _W:
        def __init__(self):
            self.calls = []

        def write_row(self, t, c, **kw):
            self.calls.append("write")
            return {"region": 1}

        def record_delivery_audit(self, t, e, a):
            self.calls.append("audit")

        def row_exists(self, t, table, tid):
            return True

    w = _W()
    event = {"event_id": "e", "external_reference": "x",
             "payload": {"nm": "A"}, "operation": "upsert"}
    out = deliver_event(object(), object(), entity_name="regions",
                        mappings=[{"source_column": "nm", "target_table": "region",
                                   "target_column": "name", "transform": "none"}],
                        event=event, registry=reg, writer=w)
    assert out["status"] == "delivered"
    assert w.calls == ["write", "audit"]


def test_refresh_entity_uses_injected_writer():
    from src.lrmis_delivery import refresh_entity
    reg = LrmisRegistry.from_discovery(
        [_col("region", "id", 1, key="PRI", extra="auto_increment"),
         _col("region", "name", 2)], [])
    fw = _FakeWriter()
    out = refresh_entity(
        object(), object(), entity_name="regions",
        mappings=[{"source_column": "nm", "target_table": "region",
                   "target_column": "name", "transform": "none"}],
        source_rows=[{"id": 1, "nm": "A"}, {"id": 2, "nm": "B"}],
        external_reference_of=lambda r: f"ext-{r['id']}",
        registry=reg, writer=fw)
    assert ("delete", "regions") in fw.calls
    assert sum(1 for c in fw.calls if c[0] == "write") == 2
    assert out["written"] == 2
