import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { approveMapping, getLrmisSchema, getProposal, getProposals, resolveMapping } from "../api/client";
import type { ProposalField, ProposalSummary } from "../api/types";
import CopyButton from "../components/CopyButton";
import GuardedActionModal from "../components/GuardedActionModal";
import MappingLanes, { TRANSFORM_OPTIONS } from "../components/MappingLanes";
import StatusChip from "../components/StatusChip";
import { useJobRunner } from "../hooks/useJobRunner";
import { errMsg, shortFp } from "../utils";

type DeployTarget = { proposalId: number; label: string };

type ResolveItem = {
  field: ProposalField;
  targetTable: string;
  targetColumn: string;
  transform: string;
};

// Mirror PendingEditor's pre-fill: a column is bulk-resolvable only when the AI
// suggested a real LRMIS target table (not a legacy _staging table) and a target
// column — exactly the case where the per-row Resolve button would be enabled
// with no manual edits. Transform is clamped to the allowlist so the backend
// does not reject the batch. Returns null for columns that still need a human.
function autoResolveTarget(f: ProposalField): ResolveItem | null {
  const table =
    f.suggested_target_table && !f.suggested_target_table.includes("_staging")
      ? f.suggested_target_table.trim()
      : "";
  const column = (f.resolved_target_column ?? f.suggested_target_column ?? "").trim();
  if (!table || !column) return null;
  const raw = f.resolved_transform ?? f.transform ?? "none";
  const transform = TRANSFORM_OPTIONS.includes(raw) ? raw : "none";
  return { field: f, targetTable: table, targetColumn: column, transform };
}

export default function MappingReview() {
  const { proposalId } = useParams<{ proposalId: string }>();
  const queryClient = useQueryClient();
  const [resolvingId, setResolvingId] = useState<number | null>(null);
  const [approveOpen, setApproveOpen] = useState(false);
  const [deployTarget, setDeployTarget] = useState<DeployTarget | null>(null);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [reproposeOpen, setReproposeOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const deployRunner = useJobRunner();
  const bulkRunner = useJobRunner();
  const reproposeRunner = useJobRunner();

  const enabled = proposalId !== undefined && /^\d+$/.test(proposalId);
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["proposal", proposalId],
    queryFn: () => getProposal(proposalId as string),
    enabled,
  });

  // LRMIS tables -> columns, for the manual multi-table mapping pickers.
  const lrmisSchema = useQuery({
    queryKey: ["lrmis-schema"],
    queryFn: getLrmisSchema,
    enabled,
    staleTime: Infinity,
  });

  // Picker: the tables still awaiting review, shown when no proposal is open.
  const queue = useQuery({
    queryKey: ["needs-review-proposals"],
    queryFn: () => getProposals("needs_review"),
    enabled: !enabled,
  });

  // Picker: approved proposals whose entity is not yet on the LRMIS target —
  // i.e. ready to deploy. Without this list an approved proposal has no home
  // (it left the review queue), so its Deploy button was unreachable.
  const deployable = useQuery({
    queryKey: ["deployable-proposals"],
    queryFn: () => getProposals(),
    enabled: !enabled,
  });
  // An entity accumulates many approved proposals over time (legacy staging +
  // re-proposed LRMIS). Show only the LATEST per entity — the same one bulk
  // deploy targets — so the stale staging proposals (which fail to deploy) are
  // not offered here.
  const readyToDeploy = (() => {
    const byEntity = new Map<number, ProposalSummary>();
    for (const p of deployable.data ?? []) {
      // Skip on-target, unapproved, and proposals with no real LRMIS mapping
      // (legacy staging-only or all-rejected — they cannot deploy to the target).
      if (
        p.on_target ||
        !p.has_lrmis_mapping ||
        (p.status !== "approved" && p.status !== "auto_approved")
      )
        continue;
      const existing = byEntity.get(p.entity_id);
      if (!existing || p.proposal_id > existing.proposal_id) byEntity.set(p.entity_id, p);
    }
    return [...byEntity.values()].sort((a, b) => a.source_table.localeCompare(b.source_table));
  })();

  // Entities the AI could not map (latest proposal has no LRMIS mapping and is
  // not in the review queue) — otherwise invisible. They need hand-mapping.
  const needsMapping = (() => {
    const byEntity = new Map<number, ProposalSummary>();
    for (const p of deployable.data ?? []) {
      if (
        p.on_target ||
        p.has_lrmis_mapping ||
        p.source_schema !== "irimsv" ||
        p.status === "rejected" ||
        p.status === "needs_review"
      )
        continue;
      const existing = byEntity.get(p.entity_id);
      if (!existing || p.proposal_id > existing.proposal_id) byEntity.set(p.entity_id, p);
    }
    return [...byEntity.values()].sort((a, b) => a.source_table.localeCompare(b.source_table));
  })();

  const resolve = useMutation({
    mutationFn: (args: {
      field: ProposalField;
      targetTable: string;
      targetColumn: string;
      transform: string;
    }) =>
      resolveMapping({
        proposal_id: data!.proposal.id,
        source_column: args.field.source_column,
        target_table: args.targetTable || undefined,
        target_column: args.targetColumn,
        transform: args.transform,
      }),
    onMutate: (args) => setResolvingId(args.field.id),
    onSettled: () => setResolvingId(null),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["proposal", proposalId] }),
    onError: (err) => setActionError(errMsg(err)),
  });

  // Bulk resolve: accept the AI's suggested LRMIS target for every remaining
  // column that has one, in a single click. Runs sequentially so a mid-batch
  // failure surfaces its error while the columns resolved before it stay saved;
  // the proposal query is refetched on settle to reflect whatever committed.
  const resolveAll = useMutation({
    mutationFn: async (items: ResolveItem[]) => {
      for (const it of items) {
        await resolveMapping({
          proposal_id: data!.proposal.id,
          source_column: it.field.source_column,
          target_table: it.targetTable || undefined,
          target_column: it.targetColumn,
          transform: it.transform,
        });
      }
    },
    onError: (err) => setActionError(errMsg(err)),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ["proposal", proposalId] }),
  });

  // Columns the operator can resolve in one pass (pending or previously rejected
  // rows carrying a usable AI suggestion). Empty once every column is settled.
  const resolvableFields: ResolveItem[] = (data?.fields ?? [])
    .filter((f) => f.status === "pending" || f.status === "rejected")
    .map(autoResolveTarget)
    .filter((x): x is ResolveItem => x !== null);

  const approve = useMutation({
    mutationFn: (reason: string) => approveMapping({ mapping_id: data!.proposal.id, reason }),
    onSuccess: () => {
      setApproveOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["proposal", proposalId] });
    },
  });

  const runDeploy = (reason: string) => {
    if (!deployTarget) return;
    void deployRunner
      .run({ job_type: "deploy_lrmis", params: { proposal_id: deployTarget.proposalId }, reason })
      .then(() => {
        setDeployTarget(null);
        void queryClient.invalidateQueries({ queryKey: ["proposal", proposalId] });
        void queryClient.invalidateQueries({ queryKey: ["deployable-proposals"] });
        void queryClient.invalidateQueries({ queryKey: ["needs-review-proposals"] });
      })
      .catch(() => {});
  };

  const runBulk = (reason: string) => {
    void bulkRunner
      .run({ job_type: "bulk_deploy_lrmis", params: {}, reason })
      .then(() => {
        setBulkOpen(false);
        void queryClient.invalidateQueries({ queryKey: ["deployable-proposals"] });
      })
      .catch(() => {});
  };

  const runRepropose = (reason: string) => {
    void reproposeRunner
      .run({ job_type: "bulk_propose_lrmis", params: {}, reason })
      .then(() => {
        setReproposeOpen(false);
        void queryClient.invalidateQueries({ queryKey: ["needs-review-proposals"] });
        void queryClient.invalidateQueries({ queryKey: ["deployable-proposals"] });
      })
      .catch(() => {});
  };

  // deploy_to_lrmis runs the real Path B coverage gate (only the mapped tables),
  // so the UI does not block on the proposal's unmet_required_columns — a legacy
  // whole-schema count that is misleadingly high for the multi-table target. A
  // genuine coverage gap surfaces as a clear error from the deploy job instead.
  const canDeployOpen =
    !!data &&
    (data.proposal.status === "approved" || data.proposal.status === "auto_approved");

  return (
    <div className="page">
      <h2 className="page-title">Mapping Review</h2>

      {!enabled && (
        <>
          <section className="panel">
            <div className="panel-header">
              <h3 className="panel-title">Migrate to the LRMIS target</h3>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                disabled={reproposeRunner.running}
                onClick={() => setReproposeOpen(true)}
              >
                {reproposeRunner.running ? "Re-proposing…" : "Re-propose all against LRMIS →"}
              </button>
            </div>
            <p className="dim">
              Generates a fresh LRMIS mapping for every deployed entity not yet on the target — one AI
              (Gemini) call per table. New proposals appear under "Tables awaiting review" and, once
              approved, under "ready to deploy". Existing legacy staging mappings are left untouched.
            </p>
            {(reproposeRunner.job || reproposeRunner.submitError) && (
              <div
                className={
                  reproposeRunner.job?.status === "failed" || reproposeRunner.submitError
                    ? "alert alert-danger"
                    : "alert"
                }
              >
                {reproposeRunner.submitError
                  ? reproposeRunner.submitError
                  : reproposeRunner.job?.status === "succeeded"
                    ? "Re-propose finished — review the new proposals below, then Deploy. See Sync Queue for per-table results."
                    : reproposeRunner.job?.status === "failed"
                      ? "Re-propose failed — open Sync Queue for details."
                      : "Re-proposing every deployed entity against LRMIS… (one Gemini call per table)"}
              </div>
            )}
          </section>

          <section className="panel">
            <div className="panel-header">
              <h3 className="panel-title">Tables awaiting review</h3>
              {queue.data && queue.data.length > 0 && (
                <span className="dim">{queue.data.length} pending</span>
              )}
            </div>
            {queue.isLoading && <p className="dim">Loading…</p>}
            {queue.isError && <div className="alert alert-danger">{errMsg(queue.error)}</div>}
            {queue.data && queue.data.length === 0 && (
              <p className="dim">Nothing to review — every table's columns are matched.</p>
            )}
            {queue.data && queue.data.length > 0 && (
              <table className="table">
                <thead>
                  <tr>
                    <th>Table</th>
                    <th>Columns to match</th>
                    <th>Target</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {queue.data.map((p) => (
                    <tr key={p.proposal_id}>
                      <td className="mono">
                        {p.source_schema}.{p.source_table}
                      </td>
                      <td className="num mono">{p.pending_fields}</td>
                      <td className="mono">{p.target_system}</td>
                      <td className="row-actions">
                        <Link className="btn btn-sm btn-primary" to={`/mappings/${p.proposal_id}`}>
                          Review →
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="panel">
            <div className="panel-header">
              <h3 className="panel-title">Needs manual mapping</h3>
              {needsMapping.length > 0 && <span className="dim">{needsMapping.length}</span>}
            </div>
            {needsMapping.length === 0 ? (
              <p className="dim">
                Nothing here — every re-proposed table has at least one LRMIS mapping.
              </p>
            ) : (
              <>
                <p className="dim">
                  The AI could not confidently map these tables. Open one and assign each column to an
                  LRMIS table and column by hand (pending and rejected columns are editable).
                </p>
                <table className="table">
                  <thead>
                    <tr>
                      <th>Table</th>
                      <th>Status</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {needsMapping.map((p) => (
                      <tr key={p.proposal_id}>
                        <td className="mono">
                          {p.source_schema}.{p.source_table}
                        </td>
                        <td>
                          <StatusChip status={p.status} />
                        </td>
                        <td className="row-actions">
                          <Link className="btn btn-sm btn-primary" to={`/mappings/${p.proposal_id}`}>
                            Map →
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </section>

          <section className="panel">
            <div className="panel-header">
              <h3 className="panel-title">Approved — ready to deploy to target</h3>
              <div className="panel-header-actions">
                {readyToDeploy.length > 0 && <span className="dim">{readyToDeploy.length} ready</span>}
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  disabled={readyToDeploy.length === 0 || bulkRunner.running}
                  title="Deploy every ready entity to the LRMIS target in one pass"
                  onClick={() => setBulkOpen(true)}
                >
                  {bulkRunner.running ? "Deploying all…" : `Deploy all ${readyToDeploy.length} →`}
                </button>
              </div>
            </div>
            {deployable.isLoading && <p className="dim">Loading…</p>}
            {deployable.isError && <div className="alert alert-danger">{errMsg(deployable.error)}</div>}
            {deployable.data && readyToDeploy.length === 0 && (
              <p className="dim">
                Nothing waiting — approved tables appear here until they are delivering to the LRMIS target.
              </p>
            )}
            {readyToDeploy.length > 0 && (
              <table className="table">
                <thead>
                  <tr>
                    <th>Table</th>
                    <th>Status</th>
                    <th>Target</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {readyToDeploy.map((p) => (
                    <tr key={p.proposal_id}>
                      <td className="mono">
                        {p.source_schema}.{p.source_table}
                      </td>
                      <td>
                        <StatusChip status={p.status} />
                      </td>
                      <td className="mono">{p.target_system}</td>
                      <td className="row-actions">
                        <Link className="btn btn-sm btn-ghost" to={`/mappings/${p.proposal_id}`}>
                          Open
                        </Link>
                        <button
                          type="button"
                          className="btn btn-sm btn-primary"
                          disabled={deployRunner.running}
                          title="Deploy this entity directly to the real LRMIS target"
                          onClick={() =>
                            setDeployTarget({ proposalId: p.proposal_id, label: p.source_table })
                          }
                        >
                          Deploy to target →
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}

      {isLoading && enabled && <p className="dim">Loading proposal…</p>}
      {isError && <div className="alert alert-danger">{errMsg(error)}</div>}
      {actionError && (
        <div className="alert alert-danger" role="alert">
          {actionError}{" "}
          <button type="button" className="btn btn-ghost btn-xs" onClick={() => setActionError(null)}>
            dismiss
          </button>
        </div>
      )}

      {(deployRunner.job || deployRunner.submitError) && (
        <div
          className={
            deployRunner.job?.status === "failed" || deployRunner.submitError
              ? "alert alert-danger"
              : "alert"
          }
        >
          {deployRunner.submitError
            ? deployRunner.submitError
            : deployRunner.job?.status === "succeeded"
              ? "Deployed to the LRMIS target — this entity now delivers directly to the real tables. Next: backfill it to populate the target."
              : deployRunner.job?.status === "failed"
                ? "Deploy to target failed — open Worker & Queues for details."
                : "Deploying to the LRMIS target…"}
        </div>
      )}

      {(bulkRunner.job || bulkRunner.submitError) && (
        <div
          className={
            bulkRunner.job?.status === "failed" || bulkRunner.submitError
              ? "alert alert-danger"
              : "alert"
          }
        >
          {bulkRunner.submitError
            ? bulkRunner.submitError
            : bulkRunner.job?.status === "succeeded"
              ? "Bulk deploy finished — see the job in Sync Queue for per-entity results. Backfill the deployed entities to populate the target."
              : bulkRunner.job?.status === "failed"
                ? "Bulk deploy failed — open Sync Queue for details."
                : "Deploying all ready entities to the LRMIS target…"}
        </div>
      )}

      {data && (
        <>
          <section className="panel">
            <div className="panel-header">
              <div>
                <h3 className="panel-title">
                  Proposal #{data.proposal.id} —{" "}
                  <span className="mono">
                    {data.proposal.source_schema}.{data.proposal.source_table}
                  </span>{" "}
                  → <span className="mono">{data.proposal.target_system}</span>
                </h3>
                <div className="fingerprint-row">
                  <span className="mono dim" title={data.proposal.source_fingerprint}>
                    src:{shortFp(data.proposal.source_fingerprint)}
                  </span>
                  <CopyButton text={data.proposal.source_fingerprint} />
                  <span className="mono dim" title={data.proposal.target_fingerprint}>
                    tgt:{shortFp(data.proposal.target_fingerprint)}
                  </span>
                  <CopyButton text={data.proposal.target_fingerprint} />
                </div>
              </div>
              <div className="panel-header-actions">
                <StatusChip status={data.proposal.status} />
                <button type="button" className="btn btn-sm" onClick={() => setApproveOpen(true)}>
                  Approve mapping…
                </button>
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  disabled={!canDeployOpen || deployRunner.running}
                  title={
                    canDeployOpen
                      ? "Deploy this mapping directly to the real LRMIS target"
                      : "Approve the mapping first"
                  }
                  onClick={() =>
                    setDeployTarget({ proposalId: data.proposal.id, label: data.proposal.source_table })
                  }
                >
                  {deployRunner.running ? "Deploying…" : "Deploy to target →"}
                </button>
              </div>
            </div>

            {data.proposal.unmet_required_columns.length > 0 && (
              <div className="alert alert-danger">
                <strong>Unmet required target columns:</strong>{" "}
                <span className="mono">{data.proposal.unmet_required_columns.join(", ")}</span> — resolve a
                source column to each before this mapping can be deployed.
              </div>
            )}
          </section>

          <section className="panel">
            <div className="panel-header">
              <h3 className="panel-title">Column mappings</h3>
              {resolvableFields.length > 0 && (
                <div className="panel-header-actions">
                  <span className="dim">{resolvableFields.length} with a suggestion</span>
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    disabled={resolveAll.isPending || resolvingId !== null}
                    title="Accept the AI's suggested LRMIS target for every remaining column that has one"
                    onClick={() => resolveAll.mutate(resolvableFields)}
                  >
                    {resolveAll.isPending
                      ? "Resolving…"
                      : `Resolve all ${resolvableFields.length} suggested →`}
                  </button>
                </div>
              )}
            </div>
            <MappingLanes
              fields={data.fields}
              resolvingId={resolvingId}
              lrmisTables={lrmisSchema.data?.tables}
              onResolve={(field, targetTable, targetColumn, transform) =>
                resolve.mutate({ field, targetTable, targetColumn, transform })
              }
            />
          </section>
        </>
      )}

      <GuardedActionModal
        open={approveOpen}
        tier="confirm"
        title={`Approve mapping for ${data?.proposal.source_table ?? ""}`}
        description="Mark this mapping proposal as reviewed and approved."
        actionLabel="Approve mapping"
        busy={approve.isPending}
        error={approve.isError ? errMsg(approve.error) : null}
        onConfirm={(reason) => approve.mutate(reason)}
        onClose={() => setApproveOpen(false)}
      />

      <GuardedActionModal
        open={deployTarget !== null}
        tier="confirm"
        title={`Deploy ${deployTarget?.label ?? ""} to the LRMIS target`}
        description="Send this entity's data directly into the real LRMIS tables. Non-destructive — no tables are created. After it deploys, backfill the entity to populate the target."
        actionLabel="Deploy to target"
        busy={deployRunner.running}
        error={deployRunner.submitError}
        onConfirm={(reason) => runDeploy(reason)}
        onClose={() => setDeployTarget(null)}
      />

      <GuardedActionModal
        open={bulkOpen}
        tier="confirm"
        title={`Deploy all ${readyToDeploy.length} ready ${readyToDeploy.length === 1 ? "entity" : "entities"} to the LRMIS target`}
        description="Deploys every approved, not-yet-migrated entity to the real LRMIS tables in one pass. Non-destructive — it validates each mapping and reports any with gaps, continuing past failures. Backfill each afterward to populate the target."
        actionLabel="Deploy all"
        busy={bulkRunner.running}
        error={bulkRunner.submitError}
        onConfirm={(reason) => runBulk(reason)}
        onClose={() => setBulkOpen(false)}
      />

      <GuardedActionModal
        open={reproposeOpen}
        tier="confirm"
        title="Re-propose all deployed entities against LRMIS"
        description="Runs the AI mapper once per deployed entity not yet on the target, creating fresh LRMIS-target proposals. This calls Gemini for each table and may take a few minutes."
        actionLabel="Re-propose all"
        busy={reproposeRunner.running}
        error={reproposeRunner.submitError}
        onConfirm={(reason) => runRepropose(reason)}
        onClose={() => setReproposeOpen(false)}
      />
    </div>
  );
}
