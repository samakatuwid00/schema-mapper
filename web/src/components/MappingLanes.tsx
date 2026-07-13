import { useState } from "react";
import type { ProposalField } from "../api/types";
import StatusChip from "./StatusChip";

export const TRANSFORM_OPTIONS = ["none", "cast:date->datetime", "cast:str->int", "cast:int->str"];

export interface MappingLanesProps {
  fields: ProposalField[];
  /** Called when a pending row is resolved inline. */
  onResolve?: (
    field: ProposalField,
    targetTable: string,
    targetColumn: string,
    transform: string,
  ) => void;
  /** id of the field currently being resolved (disables its button). */
  resolvingId?: number | null;
  /** LRMIS tables -> columns, for the manual mapping pickers. */
  lrmisTables?: Record<string, string[]>;
}

const GROUP_ORDER: Array<{ key: string; label: string }> = [
  { key: "accepted", label: "Accepted" },
  { key: "resolved", label: "Resolved" },
  { key: "pending", label: "Pending review" },
  { key: "rejected", label: "Rejected" },
];

function confidenceClass(value: number): string {
  if (value >= 0.8) return "conf-high";
  if (value >= 0.5) return "conf-mid";
  return "conf-low";
}

function PendingEditor({
  field,
  onResolve,
  busy,
  lrmisTables,
}: {
  field: ProposalField;
  onResolve: (targetTable: string, targetColumn: string, transform: string) => void;
  busy: boolean;
  lrmisTables?: Record<string, string[]>;
}) {
  // Pre-fill the target table only if the AI suggested a real LRMIS table (not
  // a legacy staging table); otherwise the operator picks one.
  const suggestedTable =
    field.suggested_target_table && !field.suggested_target_table.includes("_staging")
      ? field.suggested_target_table
      : "";
  const [table, setTable] = useState(suggestedTable);
  const [target, setTarget] = useState(
    field.resolved_target_column ?? field.suggested_target_column ?? "",
  );
  const [transform, setTransform] = useState(field.transform ?? "none");
  const columns = table && lrmisTables ? lrmisTables[table] ?? [] : [];

  return (
    <div className="lane-editor">
      <input
        list="lrmis-tables"
        className="input input-sm mono"
        value={table}
        onChange={(e) => setTable(e.target.value)}
        placeholder="LRMIS table"
        aria-label={`Target table for ${field.source_column}`}
      />
      <input
        list={`cols-${field.id}`}
        className="input input-sm mono"
        value={target}
        onChange={(e) => setTarget(e.target.value)}
        placeholder="target column"
        aria-label={`Target column for ${field.source_column}`}
      />
      <datalist id={`cols-${field.id}`}>
        {columns.map((c) => (
          <option key={c} value={c} />
        ))}
      </datalist>
      <select
        className="input input-sm"
        value={transform}
        onChange={(e) => setTransform(e.target.value)}
        aria-label={`Transform for ${field.source_column}`}
      >
        {TRANSFORM_OPTIONS.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="btn btn-primary btn-sm"
        disabled={busy || !table.trim() || !target.trim()}
        onClick={() => onResolve(table.trim(), target.trim(), transform)}
      >
        {busy ? "…" : "Resolve"}
      </button>
    </div>
  );
}

export default function MappingLanes({ fields, onResolve, resolvingId, lrmisTables }: MappingLanesProps) {
  const grouped = new Map<string, ProposalField[]>();
  for (const f of fields) {
    const key = GROUP_ORDER.some((g) => g.key === f.status) ? f.status : "pending";
    const list = grouped.get(key) ?? [];
    list.push(f);
    grouped.set(key, list);
  }

  return (
    <div className="mapping-lanes">
      {lrmisTables && (
        <datalist id="lrmis-tables">
          {Object.keys(lrmisTables).map((t) => (
            <option key={t} value={t} />
          ))}
        </datalist>
      )}
      {GROUP_ORDER.map(({ key, label }) => {
        const rows = grouped.get(key) ?? [];
        if (rows.length === 0) return null;
        return (
          <section key={key} className="lane-group">
            <h4 className="lane-group-title">
              {label} <span className="dim">({rows.length})</span>
            </h4>
            {rows.map((field) => {
              const conf = Math.max(0, Math.min(1, field.confidence > 1 ? field.confidence / 100 : field.confidence));
              const targetCol =
                field.resolved_target_column ??
                (field.suggested_target_table
                  ? `${field.suggested_target_table}.${field.suggested_target_column ?? "?"}`
                  : field.suggested_target_column ?? "—");
              const transform = field.resolved_transform ?? field.transform;
              return (
                <div key={field.id} className={`lane-row lane-${key}`}>
                  <div className="lane-main">
                    <span className="mono lane-source">{field.source_column}</span>
                    <span className="lane-arrow">→</span>
                    <span className="mono lane-target">{targetCol}</span>
                    <div className="confidence" title={`confidence ${(conf * 100).toFixed(0)}%`}>
                      <div
                        className={`confidence-fill ${confidenceClass(conf)}`}
                        style={{ width: `${conf * 100}%` }}
                      />
                    </div>
                    <span className="dim conf-pct">{(conf * 100).toFixed(0)}%</span>
                    {transform && transform !== "none" && (
                      <span className="badge badge-transform mono">{transform}</span>
                    )}
                    <StatusChip status={field.status} />
                  </div>
                  {field.reasoning && <div className="lane-reasoning dim">{field.reasoning}</div>}
                  {(key === "pending" || key === "rejected") && onResolve && (
                    <PendingEditor
                      field={field}
                      busy={resolvingId === field.id}
                      lrmisTables={lrmisTables}
                      onResolve={(tbl, col, tf) => onResolve(field, tbl, col, tf)}
                    />
                  )}
                </div>
              );
            })}
          </section>
        );
      })}
      {fields.length === 0 && <p className="dim empty-note">No mapping fields.</p>}
    </div>
  );
}
