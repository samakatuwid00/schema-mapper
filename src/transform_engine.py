"""
Takes one row from your central DB + the active mapping config + the
target Schema, and produces a target-shaped row, or a list of
validation errors if it can't safely do so.

This module has ZERO knowledge of any specific target system. Point it
at a different mapping config and target Schema and it behaves
identically. This is what makes the pipeline reusable across systems
with completely different schemas.
"""
from datetime import datetime, date
from .schema_models import Schema

_ENVELOPE_FIELDS = {
    "event_id", "external_reference", "source_system", "operation",
    "source_updated_at", "mapping_version", "payload_checksum", "accepted_at",
    "active",
}

_TRANSFORMS = {
    "none": lambda v: v,
    "trim": lambda v: v.strip() if isinstance(v, str) else v,
    "cast:date->datetime": lambda v: datetime.combine(v, datetime.min.time()) if isinstance(v, date) else v,
    "cast:str->int": lambda v: int(v) if v not in (None, "") else None,
    "cast:int->str": lambda v: str(v) if v is not None else None,
}


def _apply_transform(value, transform: str):
    if transform.startswith("enum_remap:"):
        import json as _json
        mapping = _json.loads(transform.split("enum_remap:", 1)[1])
        return mapping.get(value, value)
    fn = _TRANSFORMS.get(transform, _TRANSFORMS["none"])
    return fn(value)


def transform_row(source_row: dict, mapping_config: dict, target_schema: Schema) -> tuple[dict, list[str]]:
    """
    Returns (target_row, errors). If errors is non-empty, do NOT write
    target_row to the staging DB -- send it to a dead-letter/error log
    for manual inspection instead.
    """
    errors = []
    target_row = {}
    target_table_name = None

    for m in mapping_config.get("mappings", []):
        source_col = m["source_column"]
        target_col = m.get("target_column")
        target_table = m.get("target_table")
        
        # Skip mappings with no target column or no target table (rejected/ignored)
        if not target_col or not target_table:
            continue
            
        if source_col not in source_row:
            continue
        target_table_name = target_table
        value = source_row[source_col]
        
        # Default source_updated_at to now() if source value is None
        if target_col == "source_updated_at" and value is None:
            from datetime import datetime, timezone
            value = datetime.now(timezone.utc)
        
        # Default accepted_at to now() if source value is None
        if target_col == "accepted_at" and value is None:
            from datetime import datetime, timezone
            value = datetime.now(timezone.utc)
        
        try:
            value = _apply_transform(value, m.get("transform", "none"))
        except Exception as e:
            errors.append(f"transform failed for {source_col} -> {target_col}: {e}")
            continue
        target_row[target_col] = value

    if target_table_name:
        table = target_schema.get_table(target_table_name)
        if table:
            for col in table.columns:
                if col.name in _ENVELOPE_FIELDS:
                    continue
                if not col.nullable and target_row.get(col.name) in (None, ""):
                    errors.append(f"required target field '{col.name}' is missing or null")
                if col.enum_values and target_row.get(col.name) is not None:
                    if target_row[col.name] not in col.enum_values:
                        errors.append(f"'{target_row[col.name]}' is not a valid value for enum '{col.name}'")
        else:
            errors.append(f"target table '{target_table_name}' not found in target schema")

    return target_row, errors


def transform_batch(source_rows: list[dict], mapping_config: dict, target_schema: Schema):
    """Convenience wrapper: returns (good_rows, failed_rows_with_errors)."""
    good, failed = [], []
    for row in source_rows:
        target_row, errors = transform_row(row, mapping_config, target_schema)
        if errors:
            failed.append({"source_row": row, "errors": errors})
        else:
            good.append(target_row)
    return good, failed
