"""Reconcile a source reference entity to an existing LRMIS reference table.

Some source entities (e.g. `usertypes`) correspond to a small, LRMIS-owned
*reference* table (`user_type`) that is seeded by LRMIS, never written by this
pipeline. We do not migrate rows into it; we match each source row to the seed
row that already represents it (by a natural key such as a name or shortcode)
and record that match in `integration.id_crosswalk`.

Once the crosswalk exists, cross-entity FK resolution
(`lrmis_delivery.resolve_cross_entity_fks`) can fill any target FK that points
at the reference table — e.g. `user.usertype_id` — straight from it.

Matching is deliberately conservative: exact, case-insensitive equality against
each candidate target column in order. Anything that does not match cleanly is
returned as `unmatched` for a human to decide — never guessed.
"""
from __future__ import annotations

from .connectors import safe_identifier
from .pipeline import generate_external_reference


def _norm(v) -> str:
    return "" if v is None else str(v).strip().casefold()


def reconcile_reference_crosswalk(
    central_conn, target_conn, *, source_entity: str, source_schema: str,
    source_pk: str, source_match_col: str, target_table: str, target_pk: str,
    target_match_cols: list[str], source_system: str = "IRIMSV_REGION_V",
    target_system: str = "LRMIS",
) -> dict:
    """Match source reference rows to existing target rows and record the
    crosswalk. Returns {matched: [...], unmatched: [...], recorded: int}.
    Idempotent: re-recording the same match is a no-op."""
    for ident in (source_entity, source_schema, source_pk, source_match_col,
                  target_table, target_pk, *target_match_cols):
        safe_identifier(ident)

    # Load target reference rows and build a lookup per candidate column.
    with target_conn.cursor(dictionary=True) as tcur:
        cols = ", ".join(f"`{c}`" for c in ({target_pk, *target_match_cols}))
        tcur.execute(f"SELECT {cols} FROM `{target_table}`")
        target_rows = tcur.fetchall()
    lookups = {c: {} for c in target_match_cols}
    for row in target_rows:
        for c in target_match_cols:
            key = _norm(row.get(c))
            if key and key not in lookups[c]:
                lookups[c][key] = row[target_pk]

    matched: list[dict] = []
    unmatched: list[dict] = []
    with central_conn.cursor() as scur:
        scur.execute(
            f'SELECT "{source_pk}", "{source_match_col}" '
            f'FROM "{source_schema}"."{source_entity}"')
        source_rows = scur.fetchall()

    recorded = 0
    for src_pk, match_val in source_rows:
        target_id = None
        via = None
        norm = _norm(match_val)
        for c in target_match_cols:
            if norm in lookups[c]:
                target_id = lookups[c][norm]
                via = c
                break
        if target_id is None:
            unmatched.append({"source_pk": str(src_pk), "value": match_val})
            continue
        ext_ref = str(generate_external_reference(
            source_system, source_schema, source_entity, [src_pk]))
        with central_conn.cursor() as cur:
            cur.execute("""
                INSERT INTO integration.id_crosswalk
                    (source_system, source_entity, external_reference,
                     target_system, target_table, target_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_system, source_entity, external_reference,
                             target_system, target_table)
                DO UPDATE SET target_id = EXCLUDED.target_id
            """, (source_system, source_entity, ext_ref,
                  target_system, target_table, str(target_id)))
        recorded += 1
        matched.append({"source_pk": str(src_pk), "value": match_val,
                        "target_id": target_id, "matched_on": via})
    central_conn.commit()
    return {"matched": matched, "unmatched": unmatched, "recorded": recorded}
