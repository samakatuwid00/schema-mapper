import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { approveMapping, getProposal, resolveMapping } from "../api/client";
import type { ProposalField } from "../api/types";
import CopyButton from "../components/CopyButton";
import GuardedActionModal from "../components/GuardedActionModal";
import MappingLanes from "../components/MappingLanes";
import StatusChip from "../components/StatusChip";
import { errMsg, shortFp } from "../utils";

export default function MappingReview() {
  const { proposalId } = useParams<{ proposalId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [inputId, setInputId] = useState(proposalId ?? "");
  const [resolvingId, setResolvingId] = useState<number | null>(null);
  const [approveOpen, setApproveOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const enabled = proposalId !== undefined && /^\d+$/.test(proposalId);
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["proposal", proposalId],
    queryFn: () => getProposal(proposalId as string),
    enabled,
  });

  const resolve = useMutation({
    mutationFn: (args: { field: ProposalField; targetColumn: string; transform: string }) =>
      resolveMapping({
        proposal_id: data!.proposal.id,
        source_column: args.field.source_column,
        target_column: args.targetColumn,
        transform: args.transform,
      }),
    onMutate: (args) => setResolvingId(args.field.id),
    onSettled: () => setResolvingId(null),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["proposal", proposalId] }),
    onError: (err) => setActionError(errMsg(err)),
  });

  const approve = useMutation({
    mutationFn: (reason: string) => approveMapping({ mapping_id: data!.proposal.id, reason }),
    onSuccess: () => {
      setApproveOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["proposal", proposalId] });
    },
  });

  return (
    <div className="page">
      <h2 className="page-title">Mapping Review</h2>

      <div className="form-row">
        <label className="field field-inline">
          <span className="field-label">Proposal id</span>
          <input
            className="input input-sm mono"
            value={inputId}
            placeholder="42"
            onChange={(e) => setInputId(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && /^\d+$/.test(inputId)) navigate(`/mappings/${inputId}`);
            }}
          />
        </label>
        <button
          type="button"
          className="btn btn-primary"
          disabled={!/^\d+$/.test(inputId)}
          onClick={() => navigate(`/mappings/${inputId}`)}
        >
          Load proposal
        </button>
      </div>

      {!enabled && <p className="dim">Enter a proposal id to review its field mappings.</p>}
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
                <button type="button" className="btn btn-primary btn-sm" onClick={() => setApproveOpen(true)}>
                  Approve mapping…
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
            <MappingLanes
              fields={data.fields}
              resolvingId={resolvingId}
              onResolve={(field, targetColumn, transform) =>
                resolve.mutate({ field, targetColumn, transform })
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
    </div>
  );
}
