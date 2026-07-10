import { useEffect, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronUp, GitCompareArrows, KeyRound, Search } from "lucide-react";
import { compareRow, getDataRows, getDataTables } from "../api/client";
import type {
  CompareResponse,
  DataColumn,
  DataSide,
  SourceTableSummary,
  TargetTableSummary,
} from "../api/types";
import StatusChip from "../components/StatusChip";
import { errMsg } from "../utils";

const SOURCE_SCHEMA = "irimsv";
const PAGE_SIZES = [10, 25, 50, 100] as const;

interface Selection {
  side: DataSide;
  table: string;
  /** Present on source tables that map to a deployed entity. */
  stagingTable: string | null;
}

/** Render one cell value with the read-only conventions the spec requires. */
function Cell({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    return (
      <span className="null-cell" title="NULL" aria-label="null">
        ∅
      </span>
    );
  }
  if (typeof value === "boolean") {
    return <span className={`chip ${value ? "chip-flowing" : "chip-idle"}`}>{String(value)}</span>;
  }
  if (typeof value === "object") {
    return <span title={JSON.stringify(value, null, 2)}>{JSON.stringify(value)}</span>;
  }
  return <>{String(value)}</>;
}

/** Side-by-side source↔target comparison of one logical record. */
function CompareModal({
  entity,
  externalReference,
  onClose,
}: {
  entity: string;
  externalReference: string;
  onClose: () => void;
}) {
  const { data, isLoading, isError, error, refetch } = useQuery<CompareResponse>({
    queryKey: ["data-compare", entity, externalReference],
    queryFn: () => compareRow(entity, externalReference),
  });

  return (
    <div className="modal-overlay" role="presentation" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label="Compare row"
        style={{ width: "min(760px, 100%)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h3 className="modal-title">
            Compare — <span className="mono">{entity}</span>
          </h3>
          <div className="panel-header-actions">
            {data?.delivery_status && <StatusChip status={data.delivery_status} />}
            <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">
              ✕
            </button>
          </div>
        </div>

        <div className="fingerprint-row">
          <span className="dim">external_reference</span>
          <span className="mono">{externalReference}</span>
        </div>

        {isLoading && <p className="dim">Loading comparison…</p>}
        {isError && (
          <div className="alert alert-danger">
            {errMsg(error)}{" "}
            <button type="button" className="btn btn-ghost btn-xs" onClick={() => void refetch()}>
              retry
            </button>
          </div>
        )}

        {data && (
          <>
            {data.missing_in_target && (
              <div className="alert alert-danger" style={{ marginBottom: 12 }}>
                Not yet delivered to staging — this record has no row in{" "}
                <span className="mono">{data.staging_table ?? "the staging table"}</span> yet.
              </div>
            )}
            {data.missing_in_source && (
              <div className="alert alert-info" style={{ marginBottom: 12 }}>
                No source payload was found for this reference in the sync queue.
              </div>
            )}

            {data.fields.length > 0 && (
              <div style={{ overflowX: "auto" }}>
                <table className="data-grid">
                  <thead>
                    <tr>
                      <th style={{ cursor: "default" }}>Field</th>
                      <th style={{ cursor: "default" }}>Source</th>
                      <th style={{ cursor: "default" }}>Target</th>
                      <th style={{ cursor: "default" }}>Match</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.fields.map((f) => {
                      const tone = !f.compared
                        ? "var(--st-idle)"
                        : f.matches
                          ? "var(--st-flowing)"
                          : "var(--st-failed)";
                      return (
                        <tr key={f.field} style={{ borderLeft: `3px solid ${tone}` }}>
                          <td className="mono">{f.field}</td>
                          <td>
                            <Cell value={f.source} />
                          </td>
                          <td>
                            <Cell value={f.target} />
                          </td>
                          <td>
                            {!f.compared ? (
                              <StatusChip status="unknown" label="target only" />
                            ) : f.matches ? (
                              <StatusChip status="delivered" label="match" />
                            ) : (
                              <StatusChip status="failed" label="differs" />
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/** One selectable table row in the left rail. */
function RailRow({
  name,
  rows,
  entityStatus,
  active,
  onSelect,
}: {
  name: string;
  rows: number;
  entityStatus: string | null;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className={`tree-node${active ? " tree-schema" : ""}`}
      onClick={onSelect}
      title={name}
    >
      <span className="mono" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
        {name}
      </span>
      <span className="tree-count" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
        {entityStatus && <StatusChip status={entityStatus} />}
        <span className="badge badge-type mono">{rows.toLocaleString()}</span>
      </span>
    </button>
  );
}

export default function DataBrowser() {
  const [selected, setSelected] = useState<Selection | null>(null);
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState<number>(25);
  const [sort, setSort] = useState<string | undefined>(undefined);
  const [direction, setDirection] = useState<"asc" | "desc">("asc");
  const [compareMode, setCompareMode] = useState(false);
  const [compareRef, setCompareRef] = useState<string | null>(null);

  const tables = useQuery({
    queryKey: ["data-tables", SOURCE_SCHEMA],
    queryFn: () => getDataTables(SOURCE_SCHEMA),
  });

  // Auto-select the first source table (falling back to the first staging table).
  useEffect(() => {
    if (selected || !tables.data) return;
    const src = tables.data.source.tables[0];
    if (src) {
      setSelected({ side: "source", table: src.table, stagingTable: src.staging_table });
      return;
    }
    const tgt = tables.data.target.tables[0];
    if (tgt) setSelected({ side: "target", table: tgt.table, stagingTable: null });
  }, [tables.data, selected]);

  // Reset paging/sort whenever the selected table changes.
  useEffect(() => {
    setPage(1);
    setSort(undefined);
    setDirection("asc");
    setCompareMode(false);
    setCompareRef(null);
  }, [selected?.side, selected?.table]);

  const rows = useQuery({
    queryKey: ["data-rows", selected?.side, selected?.table, page, size, sort, direction],
    queryFn: () =>
      getDataRows({
        side: selected!.side,
        table: selected!.table,
        page,
        size,
        sort,
        direction,
        sourceSchema: SOURCE_SCHEMA,
      }),
    enabled: selected !== null,
    placeholderData: keepPreviousData,
  });

  const filtered = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const match = <T extends { table: string }>(list: T[]) =>
      needle ? list.filter((t) => t.table.toLowerCase().includes(needle)) : list;
    return {
      source: match<SourceTableSummary>(tables.data?.source.tables ?? []),
      target: match<TargetTableSummary>(tables.data?.target.tables ?? []),
    };
  }, [tables.data, filter]);

  const columns: DataColumn[] = rows.data?.columns ?? [];
  const hasExternalRef = columns.some((c) => c.name === "external_reference");
  const canCompare = selected?.side === "source" && Boolean(selected.stagingTable);

  const onSort = (col: string) => {
    if (sort === col) {
      setDirection((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSort(col);
      setDirection("asc");
    }
    setPage(1);
  };

  return (
    <div className="page">
      <h2 className="page-title">Data Browser</h2>

      <div style={{ display: "grid", gridTemplateColumns: "240px 1fr", gap: 20, alignItems: "start" }}>
        {/* ---- Left rail ---- */}
        <aside className="panel" style={{ padding: 12, position: "sticky", top: 76 }}>
          <label className="field" style={{ margin: "0 0 12px" }}>
            <span className="field-label" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <Search size={12} strokeWidth={2} aria-hidden="true" /> Find table
            </span>
            <input
              className="input input-sm mono"
              value={filter}
              placeholder="filter…"
              onChange={(e) => setFilter(e.target.value)}
              aria-label="Find table"
            />
          </label>

          {tables.isLoading && <p className="dim">Loading tables…</p>}
          {tables.isError && (
            <div className="form-error">
              {errMsg(tables.error)}{" "}
              <button type="button" className="btn btn-ghost btn-xs" onClick={() => void tables.refetch()}>
                retry
              </button>
            </div>
          )}

          {tables.data && (
            <div className="tree">
              <div className="nav-group-title">Source ({tables.data.source.schema})</div>
              {filtered.source.length === 0 && <p className="dim empty-note">No tables</p>}
              {filtered.source.map((t) => (
                <RailRow
                  key={`s-${t.table}`}
                  name={t.table}
                  rows={t.rows}
                  entityStatus={t.entity_status}
                  active={selected?.side === "source" && selected.table === t.table}
                  onSelect={() =>
                    setSelected({ side: "source", table: t.table, stagingTable: t.staging_table })
                  }
                />
              ))}

              <div className="nav-group-title" style={{ marginTop: 12 }}>
                Staging ({tables.data.target.database})
              </div>
              {filtered.target.length === 0 && <p className="dim empty-note">No tables</p>}
              {filtered.target.map((t) => (
                <RailRow
                  key={`t-${t.table}`}
                  name={t.table}
                  rows={t.rows}
                  entityStatus={t.entity_status}
                  active={selected?.side === "target" && selected.table === t.table}
                  onSelect={() => setSelected({ side: "target", table: t.table, stagingTable: null })}
                />
              ))}
            </div>
          )}
        </aside>

        {/* ---- Main grid ---- */}
        <section className="panel">
          {!selected && <p className="dim">Select a table from the left to browse its rows.</p>}

          {selected && (
            <>
              <div className="panel-header">
                <div>
                  <h3 className="panel-title">
                    <span className="mono">{selected.table}</span>{" "}
                    <span className="badge badge-type mono">{selected.side}</span>
                  </h3>
                  <div className="dim" style={{ fontSize: 12, marginTop: 3 }}>
                    {(rows.data?.total ?? 0).toLocaleString()} rows
                  </div>
                </div>
                {canCompare && (
                  <button
                    type="button"
                    className={`btn btn-sm${compareMode ? " btn-primary" : ""}`}
                    onClick={() => setCompareMode((v) => !v)}
                    title="Compare a source row against its delivered staging row"
                  >
                    <GitCompareArrows size={13} strokeWidth={2} aria-hidden="true" />{" "}
                    {compareMode ? "Comparing" : "Compare"}
                  </button>
                )}
              </div>

              {rows.isError && (
                <div className="alert alert-danger">
                  {errMsg(rows.error)}{" "}
                  <button type="button" className="btn btn-ghost btn-xs" onClick={() => void rows.refetch()}>
                    retry
                  </button>
                </div>
              )}

              <div style={{ overflowX: "auto" }}>
                <table className="data-grid">
                  <thead>
                    <tr>
                      {compareMode && <th style={{ cursor: "default" }}>Compare</th>}
                      {columns.map((c) => {
                        const isSorted = sort === c.name;
                        return (
                          <th key={c.name} onClick={() => onSort(c.name)} title={`Sort by ${c.name}`}>
                            <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                              {c.is_primary_key && (
                                <KeyRound size={11} strokeWidth={2} aria-label="primary key" />
                              )}
                              {c.name}
                              <span className="badge badge-type mono">{c.data_type}</span>
                              {c.nullable && <span className="dim" style={{ fontSize: 9 }}>?</span>}
                              {isSorted &&
                                (direction === "asc" ? (
                                  <ChevronUp size={12} strokeWidth={2} aria-label="ascending" />
                                ) : (
                                  <ChevronDown size={12} strokeWidth={2} aria-label="descending" />
                                ))}
                            </span>
                          </th>
                        );
                      })}
                    </tr>
                  </thead>
                  <tbody>
                    {/* Loading skeleton */}
                    {rows.isLoading &&
                      Array.from({ length: 8 }).map((_, r) => (
                        <tr key={`sk-${r}`}>
                          {compareMode && <td />}
                          {(columns.length ? columns : [{ name: "c" } as DataColumn]).map((c, i) => (
                            <td key={`${c.name}-${i}`}>
                              <span
                                aria-hidden="true"
                                style={{
                                  display: "inline-block",
                                  height: "0.7em",
                                  width: "70%",
                                  borderRadius: 3,
                                  background: "var(--panel-inset)",
                                }}
                              />
                            </td>
                          ))}
                        </tr>
                      ))}

                    {/* Rows */}
                    {!rows.isLoading &&
                      rows.data?.rows.map((row, i) => {
                        const ref = row["external_reference"];
                        const refStr = ref === null || ref === undefined ? "" : String(ref);
                        const canRow = hasExternalRef && refStr !== "";
                        return (
                          <tr key={i}>
                            {compareMode && (
                              <td>
                                <button
                                  type="button"
                                  className="btn btn-ghost btn-xs"
                                  disabled={!canRow}
                                  title={
                                    canRow
                                      ? "Compare this row against staging"
                                      : "No external reference on this row yet"
                                  }
                                  onClick={() => canRow && setCompareRef(refStr)}
                                >
                                  Compare row
                                </button>
                              </td>
                            )}
                            {columns.map((c) => (
                              <td key={c.name}>
                                <Cell value={row[c.name]} />
                              </td>
                            ))}
                          </tr>
                        );
                      })}

                    {/* Empty */}
                    {!rows.isLoading && rows.data && rows.data.rows.length === 0 && (
                      <tr>
                        <td colSpan={Math.max(1, columns.length + (compareMode ? 1 : 0))} className="dim">
                          No rows
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>

              {/* Pager */}
              <div
                className="pipeline"
                style={{ justifyContent: "space-between", marginTop: 14 }}
              >
                <label className="checkbox-label dim" style={{ padding: 0 }}>
                  Rows per page
                  <select
                    className="input input-sm"
                    style={{ width: "auto" }}
                    value={size}
                    onChange={(e) => {
                      setSize(Number(e.target.value));
                      setPage(1);
                    }}
                    aria-label="Rows per page"
                  >
                    {PAGE_SIZES.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </label>

                <div className="pipeline">
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={page <= 1 || rows.isFetching}
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                  >
                    Prev
                  </button>
                  <span className="dim mono" style={{ fontSize: 12 }}>
                    page {rows.data?.page ?? page} of {rows.data?.pages ?? 1}
                  </span>
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={(rows.data ? page >= rows.data.pages : true) || rows.isFetching}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    Next
                  </button>
                </div>
              </div>
            </>
          )}
        </section>
      </div>

      {compareRef !== null && selected && (
        <CompareModal
          entity={selected.table}
          externalReference={compareRef}
          onClose={() => setCompareRef(null)}
        />
      )}
    </div>
  );
}
