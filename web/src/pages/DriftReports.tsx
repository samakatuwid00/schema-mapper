import { useQuery } from "@tanstack/react-query";
import { getDriftReports } from "../api/client";
import CopyButton from "../components/CopyButton";
import StatusChip from "../components/StatusChip";
import { useJobRunner } from "../hooks/useJobRunner";
import { errMsg, fmtDate, prettyJson, shortFp } from "../utils";

export default function DriftReports() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["drift-reports"],
    queryFn: getDriftReports,
    refetchInterval: 30000,
  });

  const monitor = useJobRunner();

  return (
    <div className="page">
      <div className="page-title-row">
        <h2 className="page-title">Drift Reports</h2>
        <button
          type="button"
          className="btn"
          disabled={monitor.running}
          onClick={() => void monitor.run({ job_type: "monitor", params: {} }).catch(() => undefined)}
        >
          {monitor.running ? "Checking…" : "Run drift check now"}
        </button>
      </div>
      {monitor.submitError && <div className="form-error">{monitor.submitError}</div>}
      {monitor.job?.status === "succeeded" && (
        <div className="alert alert-ok">Drift check complete — job #{monitor.job.id}.</div>
      )}

      {isLoading && <p className="dim">Loading…</p>}
      {isError && <div className="alert alert-danger">{errMsg(error)}</div>}
      {data?.length === 0 && (
        <div className="panel">
          <p className="dim">No drift reports — observed target schemas match their approved fingerprints.</p>
        </div>
      )}

      {data?.map((report) => (
        <section key={report.id} className={`panel drift-card${report.breaking && !report.resolved_at ? " drift-breaking" : ""}`}>
          <div className="panel-header">
            <h3 className="panel-title">
              #{report.id} · <span className="mono">{report.target_system}</span>
            </h3>
            <div className="panel-header-actions">
              {report.breaking && <StatusChip status="breaking" label="breaking" />}
              <StatusChip
                status={report.resolved_at ? "resolved" : "pending"}
                label={report.resolved_at ? "resolved" : "unresolved"}
              />
            </div>
          </div>

          <dl className="kv">
            <div>
              <dt>Detected</dt>
              <dd>{fmtDate(report.created_at)}</dd>
            </div>
            {report.resolved_at && (
              <div>
                <dt>Resolved</dt>
                <dd>{fmtDate(report.resolved_at)}</dd>
              </div>
            )}
            <div>
              <dt>Fingerprint</dt>
              <dd className="fingerprint-row">
                <span className="mono dim" title={report.previous_fingerprint}>
                  {shortFp(report.previous_fingerprint)}
                </span>
                <CopyButton text={report.previous_fingerprint} />
                <span className="lane-arrow">→</span>
                <span className="mono" title={report.observed_fingerprint}>
                  {shortFp(report.observed_fingerprint)}
                </span>
                <CopyButton text={report.observed_fingerprint} />
              </dd>
            </div>
            <div>
              <dt>Impacted entities</dt>
              <dd>
                {report.impacted_entities.length === 0 && <span className="dim">none</span>}
                {report.impacted_entities.map((entity) => (
                  <span key={entity} className="badge badge-entity mono">
                    {entity}
                  </span>
                ))}
              </dd>
            </div>
          </dl>

          <details>
            <summary className="dim">Differences</summary>
            <pre className="json-block">{prettyJson(report.differences)}</pre>
          </details>
        </section>
      ))}
    </div>
  );
}
