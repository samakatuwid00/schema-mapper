import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getProposal, getStatus } from "../api/client";
import type { JobDetail } from "../api/types";
import GuardedActionModal, { type GuardTier } from "../components/GuardedActionModal";
import StatusChip from "../components/StatusChip";
import { useJobRunner } from "../hooks/useJobRunner";
import { errMsg, errStatus, prettyJson } from "../utils";

const STEPS = ["Discover", "Propose", "Review", "Deploy", "Backfill"] as const;

interface DiscoveredTable {
  name: string;
  columnCount: number | null;
  pk: string;
  updatedAt: string;
  candidates: string;
  entityId: string;
}

/** Pull table cards out of a discover job result, tolerating shape differences. */
function extractTables(job: JobDetail | null): DiscoveredTable[] {
  const result = job?.result;
  if (!result) return [];
  const raw =
    (Array.isArray(result.tables) && result.tables) ||
    (Array.isArray(result.discovered) && result.discovered) ||
    (Array.isArray(result.entities) && result.entities) ||
    null;
  if (!raw) return [];
  return (raw as Array<Record<string, unknown>>).map((t) => {
    const cols = t.columns;
    const columnCount = Array.isArray(cols)
      ? cols.length
      : typeof t.column_count === "number"
        ? t.column_count
        : typeof cols === "number"
          ? cols
          : null;
    const pk = t.primary_key ?? t.pk;
    const candidates = t.target_candidates ?? t.candidates;
    return {
      name: String(t.source_table ?? t.table ?? t.name ?? "?"),
      columnCount,
      pk: Array.isArray(pk) ? pk.map(String).join(", ") : pk ? String(pk) : "—",
      updatedAt: String(t.updated_at_column ?? t.updated_at ?? "—"),
      candidates: Array.isArray(candidates) ? candidates.map(String).join(", ") : candidates ? String(candidates) : "—",
      entityId: t.entity_id !== undefined ? String(t.entity_id) : t.id !== undefined ? String(t.id) : "—",
    };
  });
}

export default function Onboarding() {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);

  // Wizard state shared across steps.
  const [sourceSchema, setSourceSchema] = useState("irimsv");
  const [targetSystem, setTargetSystem] = useState("LRMIS");
  const [selectedTable, setSelectedTable] = useState("");
  const [proposalId, setProposalId] = useState("");
  const [backfillEntity, setBackfillEntity] = useState("");

  const [deployModalOpen, setDeployModalOpen] = useState(false);
  const [deployTier, setDeployTier] = useState<GuardTier>("confirm");
  const [deployError, setDeployError] = useState<string | null>(null);
  const [backfillModalOpen, setBackfillModalOpen] = useState(false);

  const discover = useJobRunner();
  const propose = useJobRunner();
  const deploy = useJobRunner();
  const backfill = useJobRunner();

  const status = useQuery({ queryKey: ["status"], queryFn: getStatus, refetchInterval: 10000 });

  // Proposal detail (for deploy confirmation string + redeploy detection).
  const proposal = useQuery({
    queryKey: ["proposal", proposalId],
    queryFn: () => getProposal(proposalId),
    enabled: /^\d+$/.test(proposalId),
  });

  const proposalTable = proposal.data?.proposal.source_table ?? "";
  const entityDeployed =
    status.data?.entities.some(
      (e) => e.source_table === proposalTable && e.status === "deployed",
    ) ?? false;

  const discoveredTables = extractTables(discover.job);
  const statusEntities = (status.data?.entities ?? []).filter(
    (e) => e.source_schema === sourceSchema || sourceSchema === "",
  );

  const openDeployModal = () => {
    setDeployError(null);
    setDeployTier(entityDeployed ? "typed" : "confirm");
    setDeployModalOpen(true);
  };

  const submitDeploy = (reason: string) => {
    setDeployError(null);
    const payload = {
      job_type: "deploy",
      params: { proposal_id: Number(proposalId) },
      reason,
      ...(deployTier === "typed" ? { confirm: proposalTable } : {}),
    };
    void deploy
      .run(payload)
      .then(() => {
        setDeployModalOpen(false);
        if (proposalTable) setBackfillEntity(proposalTable);
      })
      .catch((err) => {
        const s = errStatus(err);
        setDeployError(errMsg(err));
        if (s === 422) {
          // Server demands a typed confirmation (redeploy of a live entity).
          setDeployTier("typed");
        }
      });
  };

  const proposeResult = propose.job?.result ?? null;
  const proposeProposalId =
    proposeResult && typeof proposeResult.proposal_id === "number"
      ? String(proposeResult.proposal_id)
      : proposeResult && typeof proposeResult.id === "number"
        ? String(proposeResult.id)
        : null;

  return (
    <div className="page">
      <h2 className="page-title">Onboarding Wizard</h2>

      <ol className="stepper">
        {STEPS.map((label, i) => (
          <li key={label} className={`step${i === step ? " current" : ""}${i < step ? " done" : ""}`}>
            <button type="button" className="step-btn" onClick={() => setStep(i)}>
              <span className="step-num">{i + 1}</span>
              <span className="step-label">{label}</span>
            </button>
          </li>
        ))}
      </ol>

      {/* ---- Step 1: Discover ---- */}
      {step === 0 && (
        <section className="panel">
          <div className="panel-header">
            <h3 className="panel-title">1 · Discover source tables</h3>
            {discover.job && <StatusChip status={discover.job.status} />}
          </div>
          <div className="form-row">
            <label className="field field-inline">
              <span className="field-label">Source schema</span>
              <input
                className="input input-sm mono"
                value={sourceSchema}
                onChange={(e) => setSourceSchema(e.target.value)}
              />
            </label>
            <label className="field field-inline">
              <span className="field-label">Target system</span>
              <input
                className="input input-sm mono"
                value={targetSystem}
                onChange={(e) => setTargetSystem(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="btn btn-primary"
              disabled={discover.running}
              onClick={() =>
                void discover
                  .run({
                    job_type: "discover",
                    params: { source_schema: sourceSchema, target_system: targetSystem },
                  })
                  .catch(() => undefined)
              }
            >
              {discover.running ? "Discovering…" : "Run discovery"}
            </button>
          </div>
          {discover.submitError && <div className="form-error">{discover.submitError}</div>}
          {discover.job?.status === "failed" && (
            <div className="alert alert-danger">Discovery failed: {discover.job.error_message}</div>
          )}

          {discoveredTables.length > 0 && (
            <div className="card-grid">
              {discoveredTables.map((t) => (
                <button
                  type="button"
                  key={t.name}
                  className={`table-card${selectedTable === t.name ? " selected" : ""}`}
                  onClick={() => setSelectedTable(t.name)}
                >
                  <div className="mono table-card-name">{t.name}</div>
                  <dl className="kv kv-sm">
                    <div>
                      <dt>Columns</dt>
                      <dd>{t.columnCount ?? "—"}</dd>
                    </div>
                    <div>
                      <dt>PK</dt>
                      <dd className="mono">{t.pk}</dd>
                    </div>
                    <div>
                      <dt>updated_at</dt>
                      <dd className="mono">{t.updatedAt}</dd>
                    </div>
                    <div>
                      <dt>Target candidates</dt>
                      <dd className="mono">{t.candidates}</dd>
                    </div>
                    <div>
                      <dt>Entity id</dt>
                      <dd className="mono">{t.entityId}</dd>
                    </div>
                  </dl>
                </button>
              ))}
            </div>
          )}

          {statusEntities.length > 0 && (
            <>
              <h4 className="section-sub">Known entities in {sourceSchema || "all schemas"}</h4>
              <table className="table">
                <thead>
                  <tr>
                    <th>Entity</th>
                    <th>Status</th>
                    <th>Id</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {statusEntities.map((e) => (
                    <tr key={e.id}>
                      <td className="mono">{e.source_table}</td>
                      <td>
                        <StatusChip status={e.status} />
                      </td>
                      <td className="mono dim">{e.id}</td>
                      <td>
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={() => {
                            setSelectedTable(e.source_table);
                            setStep(1);
                          }}
                        >
                          Propose →
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          <div className="wizard-nav">
            <span />
            <button type="button" className="btn" onClick={() => setStep(1)}>
              Next: Propose →
            </button>
          </div>
        </section>
      )}

      {/* ---- Step 2: Propose ---- */}
      {step === 1 && (
        <section className="panel">
          <div className="panel-header">
            <h3 className="panel-title">2 · Propose mapping</h3>
            {propose.job && <StatusChip status={propose.job.status} />}
          </div>
          <div className="form-row">
            <label className="field field-inline grow">
              <span className="field-label">Source table</span>
              <input
                className="input input-sm mono"
                value={selectedTable}
                placeholder="farmers"
                onChange={(e) => setSelectedTable(e.target.value)}
                list="onboarding-tables"
              />
              <datalist id="onboarding-tables">
                {statusEntities.map((e) => (
                  <option key={e.id} value={e.source_table} />
                ))}
              </datalist>
            </label>
            <button
              type="button"
              className="btn btn-primary"
              disabled={propose.running || !selectedTable.trim()}
              onClick={() =>
                void propose
                  .run({
                    job_type: "propose",
                    params: {
                      source_schema: sourceSchema,
                      source_table: selectedTable.trim(),
                      target_system: targetSystem,
                    },
                  })
                  .catch(() => undefined)
              }
            >
              {propose.running ? "Proposing…" : "Run propose"}
            </button>
          </div>
          {propose.submitError && <div className="form-error">{propose.submitError}</div>}
          {propose.job?.status === "failed" && (
            <div className="alert alert-danger">Propose failed: {propose.job.error_message}</div>
          )}
          {propose.job?.status === "succeeded" && (
            <div className="scan-result">
              {proposeProposalId && (
                <div className="alert alert-ok">
                  Proposal <span className="mono">#{proposeProposalId}</span> created for{" "}
                  <span className="mono">{selectedTable}</span>.{" "}
                  <button
                    type="button"
                    className="btn btn-sm btn-primary"
                    onClick={() => {
                      setProposalId(proposeProposalId);
                      setStep(2);
                    }}
                  >
                    Review mapping →
                  </button>
                </div>
              )}
              {proposeResult && (
                <details>
                  <summary className="dim">Propose result</summary>
                  <pre className="json-block">{prettyJson(proposeResult)}</pre>
                </details>
              )}
            </div>
          )}
          <div className="wizard-nav">
            <button type="button" className="btn" onClick={() => setStep(0)}>
              ← Back
            </button>
            <button type="button" className="btn" onClick={() => setStep(2)}>
              Next: Review →
            </button>
          </div>
        </section>
      )}

      {/* ---- Step 3: Review ---- */}
      {step === 2 && (
        <section className="panel">
          <div className="panel-header">
            <h3 className="panel-title">3 · Review proposal</h3>
          </div>
          <div className="form-row">
            <label className="field field-inline">
              <span className="field-label">Proposal id</span>
              <input
                className="input input-sm mono"
                value={proposalId}
                placeholder="42"
                onChange={(e) => setProposalId(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="btn btn-primary"
              disabled={!/^\d+$/.test(proposalId)}
              onClick={() => navigate(`/mappings/${proposalId}`)}
            >
              Open Mapping Review
            </button>
          </div>
          <p className="dim">
            Accept or resolve each suggested column mapping, then return here to deploy. Unmet required
            target columns must be resolved before deploying.
          </p>
          <div className="wizard-nav">
            <button type="button" className="btn" onClick={() => setStep(1)}>
              ← Back
            </button>
            <button type="button" className="btn" onClick={() => setStep(3)}>
              Next: Deploy →
            </button>
          </div>
        </section>
      )}

      {/* ---- Step 4: Deploy ---- */}
      {step === 3 && (
        <section className="panel">
          <div className="panel-header">
            <h3 className="panel-title">4 · Deploy</h3>
            {deploy.job && <StatusChip status={deploy.job.status} />}
          </div>
          <div className="form-row">
            <label className="field field-inline">
              <span className="field-label">Proposal id</span>
              <input
                className="input input-sm mono"
                value={proposalId}
                onChange={(e) => setProposalId(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="btn btn-primary"
              disabled={!/^\d+$/.test(proposalId) || deploy.running}
              onClick={openDeployModal}
            >
              Deploy…
            </button>
          </div>
          {proposal.data && (
            <p className="dim">
              Proposal #{proposal.data.proposal.id}: <span className="mono">{proposal.data.proposal.source_schema}.{proposalTable}</span>{" "}
              → <span className="mono">{proposal.data.proposal.target_system}</span>
              {entityDeployed && (
                <span className="redeploy-note"> — entity already deployed; redeploy requires typed confirmation.</span>
              )}
            </p>
          )}
          {deploy.job?.status === "failed" && (
            <div className="alert alert-danger">Deploy failed: {deploy.job.error_message}</div>
          )}
          {deploy.job?.status === "succeeded" && (
            <div className="alert alert-ok">
              Deploy succeeded.{" "}
              <button type="button" className="btn btn-sm btn-primary" onClick={() => setStep(4)}>
                Next: Backfill →
              </button>
            </div>
          )}
          <div className="wizard-nav">
            <button type="button" className="btn" onClick={() => setStep(2)}>
              ← Back
            </button>
            <button type="button" className="btn" onClick={() => setStep(4)}>
              Next: Backfill →
            </button>
          </div>
        </section>
      )}

      {/* ---- Step 5: Backfill ---- */}
      {step === 4 && (
        <section className="panel">
          <div className="panel-header">
            <h3 className="panel-title">5 · Backfill</h3>
            {backfill.job && <StatusChip status={backfill.job.status} />}
          </div>
          <div className="form-row">
            <label className="field field-inline">
              <span className="field-label">Entity</span>
              <input
                className="input input-sm mono"
                value={backfillEntity}
                placeholder="farmers"
                onChange={(e) => setBackfillEntity(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="btn btn-primary"
              disabled={!backfillEntity.trim() || backfill.running}
              onClick={() => setBackfillModalOpen(true)}
            >
              Backfill…
            </button>
          </div>
          {backfill.submitError && <div className="form-error">{backfill.submitError}</div>}
          {backfill.job?.status === "failed" && (
            <div className="alert alert-danger">Backfill failed: {backfill.job.error_message}</div>
          )}
          {backfill.job?.status === "succeeded" && (
            <div className="alert alert-ok">Backfill complete — the entity is now live and syncing.</div>
          )}
          <div className="wizard-nav">
            <button type="button" className="btn" onClick={() => setStep(3)}>
              ← Back
            </button>
            <span />
          </div>
        </section>
      )}

      <GuardedActionModal
        open={deployModalOpen}
        tier={deployTier}
        danger={deployTier === "typed"}
        title={`Deploy proposal #${proposalId}`}
        description={
          deployTier === "typed" ? (
            <span>
              <strong>This entity is already deployed.</strong> The server requires you to retype the source
              table name to confirm the redeploy.
            </span>
          ) : (
            <span>
              Set up delivery of{" "}
              <code className="mono">{proposalTable || `proposal #${proposalId}`}</code> into the
              LRMIS target.
            </span>
          )
        }
        confirmString={proposalTable}
        warning={
          <div>
            <p>
              <strong>Redeploying a live entity replaces its routing and target mapping.</strong>
            </p>
            <p className="mono">
              entity: {proposalTable} · target: {proposal.data?.proposal.target_system ?? targetSystem}
            </p>
            {proposal.data && proposal.data.proposal.unmet_required_columns.length > 0 && (
              <p className="danger-text">
                Unmet required columns: {proposal.data.proposal.unmet_required_columns.join(", ")}
              </p>
            )}
          </div>
        }
        actionLabel="Deploy"
        busy={deploy.running}
        error={deployError}
        onConfirm={submitDeploy}
        onClose={() => setDeployModalOpen(false)}
      />

      <GuardedActionModal
        open={backfillModalOpen}
        tier="confirm"
        title={`Backfill ${backfillEntity}`}
        description={
          <span>
            Copy all existing rows of <code className="mono">{backfillEntity}</code> into the outbox for
            delivery to the target system. This can generate a large number of events.
          </span>
        }
        actionLabel="Run backfill"
        onConfirm={(reason) => {
          setBackfillModalOpen(false);
          void backfill
            .run({ job_type: "backfill", params: { entity: backfillEntity.trim() }, reason })
            .catch(() => undefined);
        }}
        onClose={() => setBackfillModalOpen(false)}
      />
    </div>
  );
}
