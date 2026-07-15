from src.services import operator_diagnostics as diag


def test_explain_deploy_error_extracts_missing_required_columns():
    result = diag.explain_deploy_error(
        "Deploy to target failed - mapping cannot be deployed:\n"
        "- station_name.id is required but no source column maps to it\n"
        "- station_logo.id is required but no source column maps to it",
        entity="districts",
        proposal_id=581,
    )

    assert result["entity"] == "districts"
    assert result["proposal_id"] == 581
    assert result["missing_required"] == [
        {"target_table": "station_name", "target_column": "id"},
        {"target_table": "station_logo", "target_column": "id"},
    ]
    assert result["suggested_actions"][0]["source_column"] == "id"


def test_explain_deploy_error_drafts_missing_id_bundle_from_job(monkeypatch):
    job_id = "ff2c6da7-30e7-4711-b159-282e96c23704"
    monkeypatch.setattr(
        diag, "inspect_job",
        lambda found, central=None: {
            "job_id": found,
            "job_type": "deploy_lrmis",
            "status": "failed",
            "params": {"proposal_id": 582},
        })

    result = diag.explain_deploy_error(
        f"#{job_id} deploy_lrmis failed\n"
        "mapping cannot be deployed:\n"
        "- station_name.id is required but no source column maps to it\n"
        "- legislative_dristrict.id is required but no source column maps to it")

    assert result["proposal_id"] == 582
    assert result["job"]["job_id"] == job_id
    bundle = result["suggested_actions"][0]
    assert bundle["action"] == "add_missing_mappings"
    assert bundle["proposal_id"] == 582
    assert bundle["mappings"] == [
        {"source_column": "id", "target_table": "station_name",
         "target_column": "id", "transform": "none"},
        {"source_column": "id", "target_table": "legislative_dristrict",
         "target_column": "id", "transform": "none"},
    ]


class _FakeJobCentral:
    """Minimal central stub for `resolve_deploy_job_repair`'s raw admin_job
    lookup and `_proposal_has_source_column`'s field-review check."""

    def __init__(self, job_row=None, has_id_source=True):
        self.job_row = job_row
        self.has_id_source = has_id_source

    def connection(self):
        outer = self

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, params=None):
                if "FROM integration.admin_job WHERE id" in sql:
                    self._result = outer.job_row
                elif "FROM integration.onboarding_field_review" in sql:
                    self._result = {"1": 1} if outer.has_id_source else None
                else:
                    self._result = None

            def fetchone(self):
                return self._result

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def cursor(self, *args, **kwargs):
                return Cur()

        return Conn()

    def close(self):
        pass


def test_resolve_deploy_job_repair_recovers_proposal_id_and_drafts_id_fanout():
    job_id = "ff2c6da7-30e7-4711-b159-282e96c23704"
    central = _FakeJobCentral(job_row={
        "id": job_id, "job_type": "deploy_lrmis", "status": "failed",
        "params": {"proposal_id": 582, "_actor": "op"},
        "error_message": ("mapping cannot be deployed:\n"
                          "- station_name.id is required but no source column maps to it"),
        "result": None, "progress_current": None, "progress_total": None,
        "requested_by": "op", "created_at": None, "started_at": None,
        "finished_at": None,
    }, has_id_source=True)

    result = diag.resolve_deploy_job_repair(
        f"#{job_id} deploy_lrmis failed", central=central)

    assert result["proposal_id"] == 582
    assert result["proposal_recovered"] is True
    assert result["draft_mappings"] == [
        {"source_column": "id", "target_table": "station_name",
         "target_column": "id"}]
    assert result["manual_required"] == []
    assert {"type": "open_proposal", "proposal_id": 582} in result["actions"]
    gated = next(a for a in result["actions"] if a["type"] == "gated_repair")
    assert gated["tool"] == "add_missing_mappings"
    assert gated["params"]["proposal_id"] == 582


def test_resolve_deploy_job_repair_falls_back_when_job_not_found():
    job_id = "84ad54c8-399b-4a6d-8808-c74259ef6a5b"
    central = _FakeJobCentral(job_row=None)

    result = diag.resolve_deploy_job_repair(
        f"#{job_id} deploy_lrmis failed\nmapping cannot be deployed:\n"
        "- station_name.id is required but no source column maps to it",
        central=central)

    assert result["proposal_id"] is None
    assert result["proposal_recovered"] is False
    assert result["missing_required"] == [
        {"target_table": "station_name", "target_column": "id"}]
    assert result["actions"] == []


def test_resolve_deploy_job_repair_requires_manual_choice_without_id_source():
    job_id = "ff2c6da7-30e7-4711-b159-282e96c23704"
    central = _FakeJobCentral(job_row={
        "id": job_id, "job_type": "deploy_lrmis", "status": "failed",
        "params": {"proposal_id": 582}, "result": None,
        "error_message": ("mapping cannot be deployed:\n"
                          "- station_name.id is required but no source column maps to it"),
        "progress_current": None, "progress_total": None,
        "requested_by": "op", "created_at": None, "started_at": None,
        "finished_at": None,
    }, has_id_source=False)

    result = diag.resolve_deploy_job_repair(
        f"#{job_id} deploy_lrmis failed", central=central)

    assert result["draft_mappings"] == []
    assert result["manual_required"] == [
        {"target_table": "station_name", "target_column": "id"}]
    assert not any(a["type"] == "gated_repair" for a in result["actions"])


def test_extract_job_id_finds_worker_uuid():
    assert diag.extract_job_id(
        "#84ad54c8-399b-4a6d-8808-c74259ef6a5b refresh_all"
    ) == "84ad54c8-399b-4a6d-8808-c74259ef6a5b"


class _FakeCentral:
    def __init__(self, conflicts=None):
        self.conflicts = conflicts or []
        self.inserts = []

    def connection(self):
        outer = self

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, params=None):
                self.one = None
                self.all = []
                if "SELECT * FROM integration.onboarding_entity" in sql:
                    self.one = {
                        "id": 7,
                        "source_system": "IRIMSV_REGION_V",
                        "source_schema": "irimsv",
                        "source_table": "division_libraries",
                        "primary_key_columns": ["id"],
                        "target_system": "LRMIS",
                    }
                elif "FROM integration.onboarding_field_review" in sql:
                    self.one = {
                        "review_id": 99,
                        "proposal_id": 583,
                        "source_column": "id",
                        "suggested_target_table": "profile",
                        "suggested_target_column": "id",
                        "resolved_target_column": None,
                    }
                elif "FROM integration.id_crosswalk" in sql and sql.lstrip().upper().startswith("SELECT"):
                    self.all = list(outer.conflicts)
                elif "INSERT INTO integration.id_crosswalk" in sql:
                    outer.inserts.append(("crosswalk", params))
                elif "INSERT INTO integration.onboarding_audit" in sql:
                    outer.inserts.append(("audit", params))

            def fetchone(self):
                return self.one

            def fetchall(self):
                return self.all

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def cursor(self, *args, **kwargs):
                return Cur()

            def commit(self):
                outer.inserts.append(("commit", None))

        return Conn()

    def close(self):
        pass


class _FakeTarget:
    def fetch_row_by(self, table, column, value):
        return {"exists": True} if (table, column, value) == ("profile", "id", "abc") else None


def test_diagnose_duplicate_key_plans_safe_crosswalk_repair():
    result = diag.diagnose_duplicate_key(
        "division_libraries",
        error='duplicate key value violates unique constraint "profile_pkey"\n'
              "DETAIL:  Key (id)=(abc) already exists.",
        central=_FakeCentral(),
        target=_FakeTarget(),
    )

    assert result["safe_to_repair"] is True
    assert result["target_table"] == "profile"
    assert result["target_id"] == "abc"
    assert result["external_reference"]


def test_diagnose_duplicate_key_refuses_conflicting_crosswalk_claim():
    result = diag.diagnose_duplicate_key(
        "division_libraries",
        target_table="profile",
        target_column="id",
        target_id="abc",
        central=_FakeCentral(conflicts=[{
            "source_system": "IRIMSV_REGION_V",
            "source_entity": "other",
            "external_reference": "x",
            "target_system": "LRMIS",
            "target_table": "profile",
            "target_id": "abc",
        }]),
        target=_FakeTarget(),
    )

    assert result["safe_to_repair"] is False
    assert "already claimed" in "; ".join(result["reasons"])


def test_repair_duplicate_key_records_crosswalk_and_audit():
    central = _FakeCentral()

    result = diag.repair_duplicate_key(
        "division_libraries",
        target_table="profile",
        target_column="id",
        target_id="abc",
        actor="deped",
        central=central,
        target=_FakeTarget(),
    )

    assert result["applied"] is True
    assert [kind for kind, _ in central.inserts] == ["crosswalk", "audit", "commit"]


def test_plan_refresh_failure_repair_suggests_gated_duplicate_repair(monkeypatch):
    monkeypatch.setattr(
        diag, "inspect_job",
        lambda job_id, central=None: {
            "job_id": job_id,
            "status": "succeeded",
            "failures": [{
                "entity": "division_libraries",
                "error": 'duplicate key value violates unique constraint "profile_pkey"\n'
                         "DETAIL:  Key (id)=(abc) already exists.",
            }],
        })
    monkeypatch.setattr(
        diag, "diagnose_duplicate_key",
        lambda entity, **kwargs: {
            "entity": entity,
            "target_table": "profile",
            "target_column": "id",
            "target_id": "abc",
            "safe_to_repair": True,
        })

    result = diag.plan_refresh_failure_repair("job-1")

    item = result["items"][0]
    assert item["category"] == "duplicate_key"
    assert item["gated_tool"]["tool"] == "repair_duplicate_key"
    assert result["read_only"] is True


def test_plan_refresh_failure_repair_classifies_read_only_reference(monkeypatch):
    monkeypatch.setattr(
        diag, "inspect_job",
        lambda job_id, central=None: {
            "job_id": job_id,
            "status": "succeeded",
            "failures": [{
                "entity": "divisions",
                "error": "region.id='2683041c-fce2-4898-97bd-97fc8b807007' "
                         "does not exist; region is read-only",
            }],
        })

    result = diag.plan_refresh_failure_repair("job-2")

    item = result["items"][0]
    assert item["category"] == "read_only_reference_missing"
    assert item["gated_tool"] is None
    assert "reference table" in item["diagnosis"]


def test_plan_refresh_failure_repair_identifies_reference_match_review(monkeypatch):
    monkeypatch.setattr(
        diag, "inspect_job",
        lambda job_id, central=None: {
            "job_id": job_id,
            "status": "succeeded",
            "failures": [{
                "entity": "divisions",
                "error": "no row in reference table station_address matches "
                         "{'station_id': 'edfe1995-b9cc-47b5-b192-6261dc98daa2'}",
            }],
        })
    monkeypatch.setattr(
        diag, "_mapping_review_for_target",
        lambda entity, target_system, target_table, target_column, central=None: {
            "review_id": 1208,
            "proposal_id": 582,
            "source_column": "station_id",
            "target_table": target_table,
            "target_column": target_column,
        })

    result = diag.plan_refresh_failure_repair("job-4")

    item = result["items"][0]
    assert item["category"] == "reference_match_missing"
    assert item["diagnostic"]["target_table"] == "station_address"
    assert item["diagnostic"]["target_column"] == "station_id"
    assert item["diagnostic"]["criteria"] == {
        "station_id": "edfe1995-b9cc-47b5-b192-6261dc98daa2",
    }
    assert "review proposal 582 field review 1208" in item["next_steps"][0]
    assert item["gated_tools"][0]["tool"] == "reopen_mapping_review"
    assert item["gated_tools"][0]["params"] == {"review_id": 1208}


def test_plan_refresh_failure_repair_reopens_suspect_reference_mapping(monkeypatch):
    monkeypatch.setattr(
        diag, "inspect_job",
        lambda job_id, central=None: {
            "job_id": job_id,
            "status": "succeeded",
            "failures": [{
                "entity": "divisions",
                "error": "no row in reference table station_address matches "
                         "{'station_id': 'edfe1995-b9cc-47b5-b192-6261dc98daa2'}",
            }],
        })
    monkeypatch.setattr(
        diag, "_mapping_review_for_target",
        lambda entity, target_system, target_table, target_column, central=None: None)
    monkeypatch.setattr(
        diag, "_mapping_reviews_for_table",
        lambda entity, target_system, target_table, central=None: [
            {
                "review_id": 1217,
                "proposal_id": 582,
                "source_column": "address",
                "target_table": target_table,
                "target_column": "id",
            },
            {
                "review_id": 1199,
                "proposal_id": 582,
                "source_column": "address",
                "target_table": target_table,
                "target_column": "street",
            },
        ])

    result = diag.plan_refresh_failure_repair("job-5")

    item = result["items"][0]
    assert item["category"] == "reference_match_missing"
    assert item["diagnostic"]["suspect_mapping_reviews"][0]["review_id"] == 1217
    assert item["gated_tools"] == [{
        "tool": "reopen_mapping_review",
        "params": {"review_id": 1217},
        "reason": "return this suspect accepted mapping to the normal review queue for correction",
    }]
    assert "review proposal 582 field review 1217" in item["next_steps"][0]


def test_plan_refresh_failure_repair_suggests_targeted_reject_for_unsafe_duplicate(monkeypatch):
    monkeypatch.setattr(
        diag, "inspect_job",
        lambda job_id, central=None: {
            "job_id": job_id,
            "status": "succeeded",
            "failures": [{
                "entity": "division_libraries",
                "error": 'duplicate key value violates unique constraint "profile_pkey"\n'
                         "DETAIL:  Key (id)=(abc) already exists.",
            }],
        })
    monkeypatch.setattr(
        diag, "diagnose_duplicate_key",
        lambda entity, **kwargs: {
            "entity": entity,
            "target_table": "profile",
            "target_column": "id",
            "target_id": "abc",
            "safe_to_repair": False,
            "reasons": ["profile.id is mapped from 'librarian'"],
            "mapping_review": {
                "review_id": 99,
                "proposal_id": 583,
                "source_column": "librarian",
                "target_table": "profile",
                "target_column": "id",
            },
        })

    result = diag.plan_refresh_failure_repair("job-3")

    item = result["items"][0]
    assert item["gated_tools"][0]["tool"] == "reject_mapping_review"
    assert item["gated_tools"][0]["params"] == {"review_id": 99}
    assert "review proposal 583 field review 99" in item["next_steps"][0]
