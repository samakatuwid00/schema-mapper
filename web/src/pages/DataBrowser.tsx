import { useEffect, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronUp, GitCompareArrows, KeyRound, Search } from "lucide-react";
import { compareSourceTarget, getDataRows, getDataTables } from "../api/client";
import type {
  DataColumn,
  DataSide,
  SourceTableSummary,
  SourceTargetCompareResponse,
  TargetTableSummary,
} from "../api/types";
import StatusChip from "../components/StatusChip";
import { errMsg } from "../utils";

const SOURCE_SCHEMA = "irimsv";
const PAGE_SIZES = [10, 25, 50, 100] as const;

/** Internal side value -> segmented-control label. */
const SIDE_LABEL: Record<DataSide, string> = {
  source: "Source DB",
  target: "Target DB",
};

interface Selection {
  side: DataSide;
  table: string;
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

/** A field-by-field comparison table. */
function CompareFields({
  fields,
  leftLabel,
  rightLabel,
}: {
  fields: { field: string; left: unknown; right: unknown; matches: boolean; compared: boolean }[];
  leftLabel: string;
  rightLabel: string;
}) {
  if (fields.length === 0) return null;
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="data-grid">
        <thead>
          <tr>
            <th style={{ cursor: "default" }}>Field</th>
            <th style={{ cursor: "default" }}>{leftLabel}</th>
            <th style={{ cursor: "default" }}>{rightLabel}</th>
            <th style={{ cursor: "default" }}>Match</th>
          </tr>
        </thead>
        <tbody>
          {fields.map((f) => {
            const tone = !f.compared
              ? "var(--st-idle)"
              : f.matches
                ? "var(--st-flowing)"
                : "var(--st-failed)";
            return (
              <tr key={f.field} style={{ borderLeft: `3px solid ${tone}` }}>
                <td className="mono">{f.field}</td>
                <td>
                  <Cell value={f.left} />
                </td>
                <td>
                  <Cell value={f.right} />
                </td>
                <td>
                  {!f.compared ? (
                    <StatusChip status="unknown" label="one side only" />
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
  );
}

function ModalShell({
  title,
  subtitle,
  onClose,
  children,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
}) {
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
          <h3 className="modal-title">{title}</h3>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        {subtitle && (
          <div className="fingerprint-row">
            {subtitle}
          </div>
        )}
        {children}
      </div>
    </div>
  );
}

/** Direct Source → LRMIS Target comparison of one source row (matched by primary key). */
function SourceTargetModal({
  entity,
  pk,
  onClose,
}: {
  entity: string;
  pk: string;
  onClose: () => void;
}) {
  const { data, isLoading, isError, error, refetch } = useQuery<SourceTargetCompareResponse>({
    queryKey: ["source-target-compare", entity, pk],
    queryFn: () => compareSourceTarget(entity, pk),
  });

  return (
    <ModalShell
      title={<>Source → Target — <span className="mono">{entity}</span></>}
      subtitle={
        <>
          <span className="dim">{data?.primary_key ?? "id"}</span> <span className="mono">{pk}</span>
          {data && data.target_tables.length > 0 && (
            <>
              <span className="lane-arrow">→</span>
              <span className="mono">{data.target_tables.join(", ")}</span>
            </>
          )}
        </>
      }
      onClose={onClose}
    >
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
          {data.missing_in_source && (
            <div className="alert alert-info" style={{ marginBottom: 12 }}>
              No source row found for this primary key.
            </div>
          )}
          {!data.missing_in_source && data.missing_in_target && (
            <div className="alert alert-danger" style={{ marginBottom: 12 }}>
              Not yet delivered to the LRMIS target — no target rows recorded for this source row.
            </div>
          )}
          <CompareFields
            leftLabel="Source"
            rightLabel="Target (LRMIS)"
            fields={data.fields.map((f) => ({
              field: f.field, left: f.source, right: f.target,
              matches: f.matches, compared: f.compared,
            }))}
          />
        </>
      )}
    </ModalShell>
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
  const [railSide, setRailSide] = useState<DataSide>("source");
  const [filter, setFilter] = useState("");
  const [hideViews, setHideViews] = useState(false);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState<number>(25);
  const [sort, setSort] = useState<string | undefined>(undefined);
  const [direction, setDirection] = useState<"asc" | "desc">("asc");
  const [compareMode, setCompareMode] = useState(false);
  const [stCompare, setStCompare] = useState<{ entity: string; pk: string } | null>(null);

  const tables = useQuery({
    queryKey: ["data-tables", SOURCE_SCHEMA],
    queryFn: () => getDataTables(SOURCE_SCHEMA),
  });

  // Auto-select the first source table (falling back to the first target table).
  useEffect(() => {
    if (selected || !tables.data) return;
    const src = tables.data.source.tables[0];
    if (src) {
      setSelected({ side: "source", table: src.table });
      return;
    }
    const tgt = tables.data.target.tables[0];
    if (tgt) setSelected({ side: "target", table: tgt.table });
  }, [tables.data, selected]);

  // Reset paging/sort whenever the selected table changes.
  useEffect(() => {
    setPage(1);
    setSort(undefined);
    setDirection("asc");
    setCompareMode(false);
    setStCompare(null);
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
  const pkColumn = columns.find((c) => c.is_primary_key)?.name;

  // Comparison is offered on the source side only: a source row against the
  // exact rows it produced in the LRMIS target (matched by primary key).
  const canCompare = selected?.side === "source" && Boolean(pkColumn);

  const hiddenNote = hideViews ? " (view-generated hidden)" : "";
  const activeList = railSide === "source" ? filtered.source : filtered.target;
  const visibleTables = activeList.filter((t) => {
    if (!hideViews) return true;
    return !t.table.includes("_for_lrmis");
  });

  const railHeader =
    railSide === "source"
      ? `Source (${tables.data?.source.schema ?? SOURCE_SCHEMA})`
      : `Target (${tables.data?.target.database ?? "lrmis_target"})`;

  const onSort = (col: string) => {
    if (sort === col) {
      setDirection((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSort(col);
      setDirection("asc");
    }
    setPage(1);
  };

  const selectFromRail = (t: SourceTableSummary | TargetTableSummary) => {
    setSelected({ side: railSide, table: t.table });
  };

  const startRowCompare = (row: Record<string, unknown>) => {
    if (!pkColumn || selected?.side !== "source") return;
    const pkVal = row[pkColumn];
    if (pkVal === null || pkVal === undefined) return;
    setStCompare({ entity: selected.table, pk: String(pkVal) });
  };

  const rowCompareEnabled = (row: Record<string, unknown>): boolean => {
    if (!pkColumn) return false;
    const v = row[pkColumn];
    return v !== null && v !== undefined;
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
          <label className="checkbox-label" style={{ marginBottom: 10, fontSize: 12 }}>
            <input
              type="checkbox"
              checked={hideViews}
              onChange={(e) => setHideViews(e.target.checked)}
            />
            Hide generated views
          </label>

          <label className="field" style={{ marginBottom: 12 }}>
            <span className="field-label">Database</span>
            <select
              className="input input-sm"
              value={railSide}
              onChange={(e) => setRailSide(e.target.value as DataSide)}
              aria-label="Database to browse"
            >
              {(["source", "target"] as DataSide[]).map((s) => (
                <option key={s} value={s}>
                  {SIDE_LABEL[s]}
                </option>
              ))}
            </select>
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
              <div className="nav-group-title">
                {railHeader}
                <span className="dim" style={{ fontSize: 11, marginLeft: 6 }}>
                  {visibleTables.length}{hiddenNote}
                </span>
              </div>
              {visibleTables.length === 0 && <p className="dim empty-note">No tables</p>}
              {visibleTables.map((t) => {
                const entityStatus =
                  "entity_status" in t ? ((t as { entity_status: string | null }).entity_status) : null;
                return (
                  <RailRow
                    key={`${railSide}-${t.table}`}
                    name={t.table}
                    rows={t.rows}
                    entityStatus={entityStatus}
                    active={selected?.side === railSide && selected.table === t.table}
                    onSelect={() => selectFromRail(t)}
                  />
                );
              })}
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
                    <span className="badge badge-type mono">{SIDE_LABEL[selected.side]}</span>
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
                    title="Compare a source row against the rows it produced in the LRMIS target"
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
                        const enabled = rowCompareEnabled(row);
                        return (
                          <tr key={i}>
                            {compareMode && (
                              <td>
                                <button
                                  type="button"
                                  className="btn btn-ghost btn-xs"
                                  disabled={!enabled}
                                  title={
                                    !enabled
                                      ? "No primary key on this row"
                                      : "Compare this source row against its LRMIS target rows"
                                  }
                                  onClick={() => enabled && startRowCompare(row)}
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

      {stCompare && (
        <SourceTargetModal
          entity={stCompare.entity}
          pk={stCompare.pk}
          onClose={() => setStCompare(null)}
        />
      )}
    </div>
  );
}
