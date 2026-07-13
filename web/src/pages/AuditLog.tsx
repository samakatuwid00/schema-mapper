import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getAudit } from "../api/client";
import StatusChip from "../components/StatusChip";
import { errMsg, fmtDate, prettyJson } from "../utils";

export default function AuditLog() {
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const [limit, setLimit] = useState(100);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["audit", actor, action, limit],
    queryFn: () => getAudit({ limit, actor: actor || undefined, action: action || undefined }),
    refetchInterval: 10000,
  });

  const rows = [...(data ?? [])].sort((a, b) => (b.performed_at > a.performed_at ? 1 : -1));

  return (
    <div className="page">
      <h2 className="page-title">Audit Log</h2>

      <div className="form-row">
        <label className="field field-inline">
          <span className="field-label">Actor</span>
          <input
            className="input input-sm mono"
            value={actor}
            placeholder="any"
            onChange={(e) => setActor(e.target.value)}
          />
        </label>
        <label className="field field-inline">
          <span className="field-label">Action</span>
          <input
            className="input input-sm mono"
            value={action}
            placeholder="any"
            onChange={(e) => setAction(e.target.value)}
          />
        </label>
        <label className="field field-inline">
          <span className="field-label">Limit</span>
          <select className="input input-sm" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
            {[50, 100, 250, 500].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      </div>

      {isError && <div className="alert alert-danger">{errMsg(error)}</div>}

      <section className="panel">
        {isLoading && <p className="dim">Loading…</p>}
        <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Actor</th>
              <th>Action</th>
              <th>Target</th>
              <th>Reason</th>
              <th>Result</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td className="dim nowrap">{fmtDate(row.performed_at)}</td>
                <td className="mono">{row.actor}</td>
                <td className="mono">{row.action}</td>
                <td className="mono dim">
                  {row.target_type ?? ""}
                  {row.target_id ? `:${row.target_id}` : ""}
                </td>
                <td className="reason-cell">
                  {row.reason ?? <span className="dim">—</span>}
                  {row.details !== undefined && row.details !== null && (
                    <details>
                      <summary className="dim">details</summary>
                      <pre className="json-block">{prettyJson(row.details)}</pre>
                    </details>
                  )}
                </td>
                <td>
                  <StatusChip status={row.result ?? "ok"} title={row.error_message ?? undefined} />
                  {row.error_message && <div className="error-cell mono">{row.error_message}</div>}
                </td>
              </tr>
            ))}
            {rows.length === 0 && !isLoading && (
              <tr>
                <td colSpan={6} className="dim">
                  No audit entries match the current filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        </div>
      </section>
    </div>
  );
}
