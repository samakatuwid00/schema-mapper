-- Demo-only CDC trigger for the sample `irimsv.customer` table.
--
-- Split out of sql/001_integration_foundation.sql: the foundation migration
-- must run against any central database, but this trigger hard-codes
-- `irimsv.customer` -- the sample table sql/central_db_init.sql creates. Once
-- `irimsv` holds a restored real source (nightly rebuild), that table is gone
-- and the foundation migration would fail on it.
--
-- Not a managed migration (not in migrations.MIGRATION_FILES): it is mounted
-- into the Docker central init and is only meaningful alongside the sample
-- data that scripts/insert_sample_row.py writes. Live entities get their
-- delivery triggers at deploy time instead.
CREATE OR REPLACE FUNCTION integration.enqueue_customer_change() RETURNS trigger AS $$
DECLARE
    record_json JSONB;
    record_ref UUID;
    op integration.event_operation;
BEGIN
    record_json := to_jsonb(COALESCE(NEW, OLD));
    record_ref := COALESCE(NEW.external_reference, OLD.external_reference);
    op := CASE WHEN TG_OP = 'DELETE' THEN 'deactivate'::integration.event_operation
               WHEN TG_OP = 'INSERT' THEN 'insert'::integration.event_operation
               ELSE 'update'::integration.event_operation END;
    IF TG_OP = 'DELETE' THEN
        record_json := record_json || jsonb_build_object('active', false, 'deactivated_at', now());
    END IF;
    INSERT INTO integration.outbox
        (source_entity, external_reference, operation, payload, payload_checksum, source_updated_at)
    VALUES
        ('customer', record_ref, op, record_json,
         encode(digest(record_json::text, 'sha256'), 'hex'), now());
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_customer_integration_outbox ON irimsv.customer;
CREATE TRIGGER trg_customer_integration_outbox
AFTER INSERT OR UPDATE OR DELETE ON irimsv.customer
FOR EACH ROW EXECUTE FUNCTION integration.enqueue_customer_change();
