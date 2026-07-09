import { useState, type ReactNode } from "react";
import type { SchemaSystem } from "../api/types";
import { shortFp } from "../utils";
import CopyButton from "./CopyButton";

export interface SchemaTreeProps {
  title: string;
  system: SchemaSystem;
  headerExtra?: ReactNode;
}

/** Expandable schema -> table -> column tree with type / PK badges. */
export default function SchemaTree({ title, system, headerExtra }: SchemaTreeProps) {
  const [schemaOpen, setSchemaOpen] = useState(true);
  const [openTables, setOpenTables] = useState<Set<string>>(new Set());

  const toggleTable = (name: string) => {
    setOpenTables((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  return (
    <div className="panel schema-tree">
      <div className="panel-header">
        <div>
          <h3 className="panel-title">{title}</h3>
          <div className="fingerprint-row">
            <span className="mono dim" title={system.fingerprint}>
              fp:{shortFp(system.fingerprint)}
            </span>
            <CopyButton text={system.fingerprint} />
          </div>
        </div>
        {headerExtra}
      </div>

      <div className="tree">
        <button type="button" className="tree-node tree-schema" onClick={() => setSchemaOpen(!schemaOpen)}>
          <span className="caret">{schemaOpen ? "▾" : "▸"}</span>
          <span className="mono">{system.system_name}</span>
          <span className="dim tree-count">{system.tables.length} tables</span>
        </button>

        {schemaOpen &&
          system.tables.map((table) => {
            const isOpen = openTables.has(table.name);
            return (
              <div key={table.name} className="tree-table-block">
                <button type="button" className="tree-node tree-table" onClick={() => toggleTable(table.name)}>
                  <span className="caret">{isOpen ? "▾" : "▸"}</span>
                  <span className="mono">{table.name}</span>
                  <span className="dim tree-count">{table.columns.length} cols</span>
                </button>
                {isOpen && (
                  <ul className="tree-columns">
                    {table.columns.map((col) => (
                      <li key={col.name} className="tree-column">
                        <span className="mono col-name">{col.name}</span>
                        <span className="badge badge-type mono">{col.data_type}</span>
                        {col.is_primary_key && <span className="badge badge-pk">PK</span>}
                        {col.nullable && <span className="badge badge-null">null</span>}
                        {col.description && <span className="dim col-desc">{col.description}</span>}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
      </div>
    </div>
  );
}
