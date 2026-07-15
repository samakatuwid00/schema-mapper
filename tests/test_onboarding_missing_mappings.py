from src.services import onboarding


class _FakePipeline:
    ALLOWED_TRANSFORMS = {"none", "trim"}

    def __init__(self):
        self.executed = []

    def _query(self, conn, sql, params=()):
        if "SELECT 1 FROM integration.onboarding_proposal" in sql:
            return [{"exists": 1}]
        if "SELECT DISTINCT source_column" in sql:
            return [{"source_column": "id"}, {"source_column": "legislative_district"}]
        if "status IN ('accepted', 'resolved')" in sql:
            target_table, target_column = params[1], params[2]
            if (target_table, target_column) == ("station_name", "id"):
                return [{"id": 1201, "source_column": "id", "status": "resolved"}]
            return []
        return []

    def _execute(self, conn, sql, params=()):
        self.executed.append((sql, params))

    def _fetchval(self, conn, sql, params=()):
        return 0


class _FakeCentral:
    def __init__(self):
        self.commits = 0

    def connection(self):
        outer = self

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def commit(self):
                outer.commits += 1

        return Conn()

    def close(self):
        pass


def test_add_missing_mappings_skips_existing_target_and_preserves_good_rows(monkeypatch):
    fake_pipeline = _FakePipeline()
    monkeypatch.setattr(onboarding, "_pipeline", lambda: fake_pipeline)
    central = _FakeCentral()

    result = onboarding.add_missing_mappings(
        582,
        [
            {"source_column": "id", "target_table": "station_name", "target_column": "id"},
            {"source_column": "id", "target_table": "station_address", "target_column": "id"},
        ],
        resolved_by="deped",
        central=central,
    )

    assert result["proposal_id"] == 582
    assert result["added"] == [{
        "source_column": "id",
        "target_table": "station_address",
        "target_column": "id",
        "transform": "none",
    }]
    assert result["skipped"][0]["target_table"] == "station_name"
    assert result["skipped"][0]["reason"] == "target column already has an accepted/resolved mapping"
    inserts = [sql for sql, _ in fake_pipeline.executed if "INSERT INTO integration.onboarding_field_review" in sql]
    assert len(inserts) == 1
    assert central.commits == 1


def test_add_missing_mappings_skips_unknown_source_column(monkeypatch):
    fake_pipeline = _FakePipeline()
    monkeypatch.setattr(onboarding, "_pipeline", lambda: fake_pipeline)

    result = onboarding.add_missing_mappings(
        582,
        [{"source_column": "missing_id", "target_table": "station_logo", "target_column": "id"}],
        central=_FakeCentral(),
    )

    assert result["added"] == []
    assert result["skipped"][0]["reason"] == "source column 'missing_id' is not on this proposal"
    inserts = [sql for sql, _ in fake_pipeline.executed if "INSERT INTO integration.onboarding_field_review" in sql]
    assert inserts == []

