"""Phase 6 deploy gating: status + coverage checks before an entity becomes
Path B. DB writes are covered by the live verification; here we exercise the
guards by faking the proposal load."""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from src.lrmis_registry import LrmisRegistry, parse_ddl
from src.services import lrmis_onboarding as O
from src.services.common import ValidationError

DDL = """
CREATE TABLE `station` (
  `id` int NOT NULL,
  `geoloc` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB;
"""
REG = LrmisRegistry(parse_ddl(DDL))


class _Central:
    @contextmanager
    def connection(self):
        yield object()

    def close(self):
        pass


def _patch(monkeypatch, proposal, mappings):
    monkeypatch.setattr(O, "_load_proposal_mappings",
                        lambda conn, pid: (proposal, mappings))


def test_rejects_unapproved_proposal(monkeypatch):
    _patch(monkeypatch, {"status": "needs_review", "entity_id": 1,
                         "source_table": "s", "target_system": "LRMIS"}, [])
    with pytest.raises(ValidationError):
        O.deploy_to_lrmis(1, "by", central=_Central(), registry=REG)


def test_rejects_when_no_mappings(monkeypatch):
    _patch(monkeypatch, {"status": "approved", "entity_id": 1,
                         "source_table": "s", "target_system": "LRMIS"}, [])
    with pytest.raises(ValidationError):
        O.deploy_to_lrmis(1, "by", central=_Central(), registry=REG)


def test_rejects_invalid_mapping(monkeypatch):
    # target column that does not exist -> validate_deployment raises
    _patch(monkeypatch, {"status": "approved", "entity_id": 1,
                         "source_table": "s", "target_system": "LRMIS"},
           [{"source_column": "x", "target_table": "station",
             "target_column": "ghost", "transform": "none"}])
    with pytest.raises(ValidationError):
        O.deploy_to_lrmis(1, "by", central=_Central(), registry=REG)
