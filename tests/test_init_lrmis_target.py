"""Phase 1 setup helpers: degenerate-FK stripping and seed extraction.

Pure-logic tests; no database or the 212MB dump required.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# The script lives in scripts/ (not a package); load it directly.
_SPEC = importlib.util.spec_from_file_location(
    "init_lrmis_target",
    Path(__file__).resolve().parents[1] / "scripts" / "init_lrmis_target.py",
)
init = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(init)


# ---------------------------------------------------------------------------
# sanitize_ddl
# ---------------------------------------------------------------------------

DEGENERATE = """CREATE TABLE `psgc` (
  `id` int NOT NULL,
  `parent_psgc` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `psgc_ibfk_1` FOREIGN KEY (`geolevel_id`) REFERENCES `geo_level` (`id`),
  CONSTRAINT `psgc_psgc_FK` FOREIGN KEY (`id`) REFERENCES `psgc` (`id`)
) ENGINE=InnoDB;"""

NORMAL_SELF = """CREATE TABLE `station` (
  `id` int NOT NULL,
  `parent_station` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `fk_parent_station` FOREIGN KEY (`parent_station`) REFERENCES `station` (`id`)
) ENGINE=InnoDB;"""


def test_strips_degenerate_self_fk():
    out = init.sanitize_ddl(DEGENERATE)
    assert "psgc_psgc_FK" not in out           # the id->id self-FK is gone
    assert "psgc_ibfk_1" in out                # the real FK to geo_level stays


def test_no_dangling_comma_after_strip():
    out = init.sanitize_ddl(DEGENERATE)
    lines = [l.rstrip() for l in out.splitlines()]
    close_idx = next(i for i, l in enumerate(lines) if l.lstrip().startswith(")"))
    assert not lines[close_idx - 1].endswith(","), "line before ) must not dangle a comma"


def test_keeps_legitimate_self_reference():
    out = init.sanitize_ddl(NORMAL_SELF)
    # parent_station -> id is a real hierarchy FK, different columns: keep it.
    assert "fk_parent_station" in out


def test_ddl_without_degenerate_fk_is_unchanged():
    plain = "CREATE TABLE `x` (\n  `id` int NOT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB;"
    assert init.sanitize_ddl(plain) == plain


# ---------------------------------------------------------------------------
# iter_seed_statements
# ---------------------------------------------------------------------------

DUMP = """/*!40000 ALTER TABLE `psgc` DISABLE KEYS */;
INSERT INTO `psgc` VALUES (1,'a'),(2,'b');
INSERT INTO `title` VALUES (1,'ignore me');
INSERT INTO `station_type`
VALUES (1,'primary'),
(2,'secondary');
INSERT INTO `geo_level` VALUES (1,'region');
"""


def test_extracts_only_wanted_tables(tmp_path):
    dump = tmp_path / "d.sql"
    dump.write_text(DUMP, encoding="utf8")
    wanted = {"psgc", "station_type"}
    got = list(init.iter_seed_statements(str(dump), wanted))
    tables = [t for t, _ in got]
    assert tables == ["psgc", "station_type"]     # title + geo_level skipped
    assert "ignore me" not in "".join(s for _, s in got)


def test_multiline_statement_captured_whole(tmp_path):
    dump = tmp_path / "d.sql"
    dump.write_text(DUMP, encoding="utf8")
    got = dict((t, s) for t, s in init.iter_seed_statements(str(dump), {"station_type"}))
    assert "primary" in got["station_type"]
    assert "secondary" in got["station_type"]     # spanned two lines, captured fully


def test_seed_tables_are_all_in_the_schema():
    from src.lrmis_registry import get_registry
    names = set(get_registry().table_names)
    assert set(init.SEED_TABLES) <= names
