import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { cancelQueue, getDataTables, getProposals, getStatus, generateView, applyView, getViewProposals } from "../api/client";
import type { EntityControl, OnboardingEntity, ProposalSummary, StatusResponse } from "../api/types";
import GuardedActionModal from "../components/GuardedActionModal";
import StatusChip from "../components/StatusChip";
import { useJobRunner } from "../hooks/useJobRunner";
import { errMsg } from "../utils";
import { subscribeJobEvents, type JobEvent } from "../api/sse";

const SOURCE_SCHEMA = "irimsv";
const TARGET_SYSTEM = "LRMIS";

type TableState = "not_connected" | "needs_review" | "syncing" | "paused";

interface RowState {
  state: TableState;
  chipStatus: string;
  text: string;
  proposalId?: number;
}

function controlFor(
  controls: EntityControl[],
  entity: OnboardingEntity,
): EntityControl | undefined {
  return controls.find(
    (c) =>
      (c.source_entity === entity.source_table ||
        c.source_entity === `${entity.source_schema}.${entity.source_table}`) &&
      c.target_system === entity.target_system,
  );
}

/** Fold entity status + kill-switch + a pending proposal into one manager-facing state. */
function computeState(
  table: string,
  entities: OnboardingEntity[],
  controls: EntityControl[],
  proposalByTable: Map<string, ProposalSummary>,
): RowState {
  const entity = entities.find((e) => e.source_table === table);
  const proposal = proposalByTable.get(table);

  if (entity && entity.status === "deployed") {
    const control = controlFor(controls, entity);
    const enabled = control ? control.enabled : true;
    return enabled
      ? { state: "syncing", chipStatus: "deployed", text: "Syncing" }
      : { state: "paused", chipStatus: "paused", text: "Paused" };
  }
  if (entity && (entity.status === "paused" || entity.status === "disabled")) {
    return { state: "paused", chipStatus: "paused", text: "Paused" };
  }
  if (proposal) {
    return {
      state: "needs_review",
      chipStatus: "pending",
      text: "Needs review",
      proposalId: proposal.proposal_id,
    };
  }
  return { state: "not_connected", chipStatus: "discovered", text: "Not connected" };
}

interface ResultRow {
  table?: string;
  proposal_id?: number;
  reason?: string;
  error?: string;
  [key: string]: unknown;
}

function asRows(value: unknown): ResultRow[] {
  return Array.isArray(value) ? (value as ResultRow[]) : [];
}

export default function Tables() {
  const queryClient = useQueryClient();
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [modalOpen, setModalOpen] = useState(false);
  const [refreshAllModal, setRefreshAllModal] = useState(false);
  const [pending, setPending] = useState<string[]>([]);
  const [pendingSchema, setPendingSchema] = useState(SOURCE_SCHEMA);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [generatingViews, setGeneratingViews] = useState(false);
  const [applyingView, setApplyingView] = useState<number | null>(null);
  const [applyingAll, setApplyingAll] = useState(false);
  const [viewApplyMessage, setViewApplyMessage] = useState<string | null>(null);
  const [viewApplyError, setViewApplyError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [cancelling, setCancelling] = useState<string | null>(null);

  const onboard = useJobRunner();
  const refreshAll = useJobRunner();

  const tables = useQuery({
    queryKey: ["data-tables", SOURCE_SCHEMA],
    queryFn: () => getDataTables(SOURCE_SCHEMA),
  });
  const status = useQuery<StatusResponse>({
    queryKey: ["status"],
    queryFn: getStatus,
    refetchInterval: 10000,
  });
  const proposals = useQuery({
    queryKey: ["needs-review-proposals"],
    queryFn: () => getProposals("needs_review"),
  });

  const viewProposals = useQuery({
    queryKey: ["view-proposals"],
    queryFn: () => getViewProposals(),
  });

  const sourceTables = tables.data?.source.tables ?? [];
  const entities = status.data?.entities ?? [];
  const controls = status.data?.entity_controls ?? [];

  const deployedCount = entities.filter((e) => e.status === "deployed").length;

  const proposalByTable = useMemo(() => {
    const map = new Map<string, ProposalSummary>();
    for (const p of proposals.data ?? []) if (!map.has(p.source_table)) map.set(p.source_table, p);
    return map;
  }, [proposals.data]);

  const tableStates = useMemo(() => {
    const map = new Map<string, string>();
    for (const t of sourceTables) {
      const rs = computeState(t.table, entities, controls, proposalByTable);
      map.set(t.table, rs.state);
    }
    return map;
  }, [sourceTables, entities, controls, proposalByTable]);

  const filteredSourceTables = useMemo(() => {
    if (statusFilter === "all") return sourceTables;
    return sourceTables.filter((t) => tableStates.get(t.table) === statusFilter);
  }, [sourceTables, tableStates, statusFilter]);

  const statusOptions: Array<{ value: string; label: string }> = [
    { value: "all", label: "All" },
    { value: "not_connected", label: "Not connected" },
    { value: "needs_review", label: "Needs review" },
    { value: "syncing", label: "Syncing" },
    { value: "paused", label: "Paused" },
  ];

  // Live per-table progress for the active bulk job.
  useEffect(() => {
    if (onboard.jobId === null) return;
    setEvents([]);
    const handle = subscribeJobEvents(`/api/jobs/${onboard.jobId}/events`, (event) => {
      setEvents((prev) => [...prev.slice(-99), event]);
    });
    return () => handle.close();
  }, [onboard.jobId]);

  // Refresh table/entity states once the batch finishes.
  useEffect(() => {
    if (onboard.job?.status === "succeeded") {
      void queryClient.invalidateQueries({ queryKey: ["data-tables"] });
      void queryClient.invalidateQueries({ queryKey: ["status"] });
      void queryClient.invalidateQueries({ queryKey: ["needs-review-proposals"] });
      setChecked(new Set());
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onboard.job?.status]);

  // Refresh state when refresh-all finishes.
  useEffect(() => {
    if (refreshAll.job?.status === "succeeded" || refreshAll.job?.status === "failed") {
      void queryClient.invalidateQueries({ queryKey: ["data-tables"] });
      void queryClient.invalidateQueries({ queryKey: ["status"] });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshAll.job?.status]);

  const needsReviewTables = proposals.data ?? [];
  const pendingViewTables = needsReviewTables.filter(
    (p) => p.rejected_count > 0 || (p.unmet_required_columns ?? []).length > 0,
  );

  const handleGenerateViews = async () => {
    setGeneratingViews(true);
    try {
      for (const p of pendingViewTables) {
        try {
          await generateView({
            entity_id: p.entity_id,
            source_table: p.source_table,
          });
        } catch {
          // continue with next entity
        }
      }
      void queryClient.invalidateQueries({ queryKey: ["view-proposals"] });
    } finally {
      setGeneratingViews(false);
    }
  };

  const handleApplyView = async (proposalId: number) => {
    setApplyingView(proposalId);
    setViewApplyError(null);
    try {
      await applyView({ proposal_id: proposalId });
      void queryClient.invalidateQueries({ queryKey: ["view-proposals"] });
      void queryClient.invalidateQueries({ queryKey: ["data-tables"] });
    } catch (error) {
      setViewApplyError(errMsg(error));
    } finally {
      setApplyingView(null);
    }
  };

  const handleCancelQueue = async (table: string) => {
    setCancelling(table);
    try {
      await cancelQueue(table);
      void queryClient.invalidateQueries({ queryKey: ["status"] });
    } catch {
      // ignore
    } finally {
      setCancelling(null);
    }
  };

  const handleApplyAllViews = async () => {
    setApplyingAll(true);
    setViewApplyMessage(null);
    setViewApplyError(null);
    let applied = 0;
    const failures: string[] = [];
    try {
      for (const vp of viewProposals.data ?? []) {
        if (vp.status !== "pending") continue;
        try {
          await applyView({ proposal_id: vp.id });
          applied += 1;
        } catch (error) {
          failures.push(`${vp.view_name}: ${errMsg(error)}`);
        }
      }
      void queryClient.invalidateQueries({ queryKey: ["view-proposals"] });
      void queryClient.invalidateQueries({ queryKey: ["data-tables"] });
      setViewApplyMessage(`${applied} view${applied === 1 ? "" : "s"} applied.`);
      if (failures.length > 0) {
        setViewApplyError(`${failures.length} failed: ${failures.join(" | ")}`);
      }
    } finally {
      setApplyingAll(false);
    }
  };

  const allChecked = filteredSourceTables.length > 0 &&
    filteredSourceTables.every((t) => checked.has(t.table));
  const displayedChecked = filteredSourceTables.filter((t) => checked.has(t.table)).length;
  const someChecked = displayedChecked > 0 && !allChecked;

  const toggleAll = () => {
    if (allChecked) {
      setChecked(new Set());
    } else {
      const next = new Set(checked);
      for (const t of filteredSourceTables) next.add(t.table);
      setChecked(next);
    }
  };
  const toggleOne = (table: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(table)) next.delete(table);
      else next.add(table);
      return next;
    });
  };

  const openModal = (list: string[], sourceSchema = SOURCE_SCHEMA) => {
    if (list.length === 0) return;
    setPending(list);
    setPendingSchema(sourceSchema);
    setModalOpen(true);
  };

  const submit = (reason: string) => {
    void onboard
      .run({
        job_type: "onboard_bulk",
        params: { source_schema: pendingSchema, tables: pending, target_system: TARGET_SYSTEM },
        reason,
      })
      .then(() => setModalOpen(false))
      .catch(() => setModalOpen(false)); // 409 / validation surfaced inline below
  };

  const result = onboard.job?.status === "succeeded" ? onboard.job.result ?? null : null;
  const onboarded = asRows(result?.onboarded);
  const needsReview = asRows(result?.needs_review);
  const skipped = asRows(result?.skipped_already_deployed);
  const failed = asRows(result?.failed);

  const latestProgress = [...events].reverse().find((e) => e.type === "progress");

  return (
    <div className="page">
      <div className="page-title-row">
        <h2 className="page-title">Tables</h2>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            className="btn btn-danger-outline"
            disabled={deployedCount === 0 || refreshAll.running}
            onClick={() => setRefreshAllModal(true)}
          >
            {refreshAll.running
              ? "Refreshing…"
              : `Refresh all deployed${deployedCount > 0 ? ` (${deployedCount})` : ""}`}
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            disabled={pendingViewTables.length === 0 || generatingViews}
            onClick={handleGenerateViews}
          >
            {generatingViews
              ? "Generating…"
              : `Generate views${pendingViewTables.length > 0 ? ` (${pendingViewTables.length})` : ""}`}
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={checked.size === 0 || onboard.running}
            onClick={() => openModal([...checked])}
          >
            Onboard selected{checked.size > 0 ? ` (${checked.size})` : ""}
          </button>
        </div>
      </div>

      {onboard.submitError && <div className="form-error">{onboard.submitError}</div>}

      {/* Live progress while the batch runs */}
      {onboard.running && (
        <section className="panel is-live">
          <div className="panel-header">
            <h3 className="panel-title">Onboarding in progress…</h3>
            <StatusChip status={onboard.job?.status ?? "queued"} />
          </div>
          <div className="scan-progress">
            <div className="progress">
              <div
                className={`progress-fill${!onboard.job?.progress_total ? " indeterminate" : ""}`}
                style={{
                  width: onboard.job?.progress_total
                    ? `${Math.min(
                        100,
                        ((onboard.job.progress_current ?? 0) / onboard.job.progress_total) * 100,
                      )}%`
                    : "100%",
                }}
              />
            </div>
          </div>
          {latestProgress && (
            <p className="dim mono" style={{ fontSize: 12 }}>
              {latestProgress.message}
              {typeof latestProgress.data?.current === "number" &&
              typeof latestProgress.data?.total === "number"
                ? ` — ${latestProgress.data.current}/${latestProgress.data.total}`
                : ""}
            </p>
          )}
        </section>
      )}

      {/* Result buckets in plain language */}
      {result && (
        <section className="panel">
          <div className="panel-header">
            <h3 className="panel-title">Onboarding results</h3>
            <button type="button" className="btn btn-sm" onClick={() => onboard.reset()}>
              Dismiss
            </button>
          </div>

          {onboarded.length > 0 && (
            <div className="alert alert-ok" style={{ marginBottom: 10 }}>
              <strong>✓ {onboarded.length} table{onboarded.length === 1 ? " is" : "s are"} now syncing</strong>
              <div style={{ marginTop: 6, display: "flex", gap: 8, flexWrap: "wrap" }}>
                {onboarded.map((r) => (
                  <Link key={String(r.table)} to="/data" className="badge badge-entity mono">
                    {String(r.table)}
                  </Link>
                ))}
              </div>
            </div>
          )}

          {needsReview.length > 0 && (
            <div className="alert alert-info" style={{ marginBottom: 10 }}>
              <strong>{needsReview.length} need your review</strong>
              <div style={{ marginTop: 6, display: "flex", gap: 8, flexWrap: "wrap" }}>
                {needsReview.map((r) => (
                  <Link
                    key={String(r.table)}
                    to={`/mappings/${r.proposal_id}`}
                    className="badge badge-warn mono"
                  >
                    {String(r.table)} →
                  </Link>
                ))}
              </div>
            </div>
          )}

          {skipped.length > 0 && (
            <p className="dim" style={{ marginBottom: 6 }}>
              {skipped.length} skipped (already syncing):{" "}
              <span className="mono">{skipped.map((r) => String(r.table)).join(", ")}</span>
            </p>
          )}

          {failed.length > 0 && (
            <div className="alert alert-danger">
              <strong>{failed.length} failed</strong>
              <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                {failed.map((r) => (
                  <li key={String(r.table)}>
                    <span className="mono">{String(r.table)}</span> — {String(r.error ?? "unknown error")}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      <section className="panel">
        <div className="panel-header">
          <h3 className="panel-title">Source tables — {SOURCE_SCHEMA}</h3>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span className="dim">{filteredSourceTables.length} of {sourceTables.length} tables</span>
            <select
              className="input input-sm"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              aria-label="Filter by status"
              style={{ width: 140 }}
            >
              {statusOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {tables.isLoading && <p className="dim">Loading tables…</p>}
        {tables.isError && (
          <div className="alert alert-danger">
            {errMsg(tables.error)}{" "}
            <button type="button" className="btn btn-ghost btn-xs" onClick={() => void tables.refetch()}>
              retry
            </button>
          </div>
        )}

        {tables.data && (
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 32 }}>
                  <input
                    type="checkbox"
                    checked={allChecked}
                    ref={(el) => {
                      if (el) el.indeterminate = someChecked;
                    }}
                    onChange={toggleAll}
                    aria-label="Select all tables"
                  />
                </th>
                <th>Table</th>
                <th>Rows</th>
                <th>State</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filteredSourceTables.map((t) => {
                const rs = computeState(t.table, entities, controls, proposalByTable);
                return (
                  <tr key={t.table}>
                    <td>
                      <input
                        type="checkbox"
                        checked={checked.has(t.table)}
                        onChange={() => toggleOne(t.table)}
                        aria-label={`Select ${t.table}`}
                      />
                    </td>
                    <td className="mono">{t.table}</td>
                    <td className="num mono">{t.rows.toLocaleString()}</td>
                    <td>
                      <StatusChip status={rs.chipStatus} label={rs.text} />
                    </td>
                    <td className="row-actions">
                      {rs.state === "not_connected" && (
                        <button
                          type="button"
                          className="btn btn-sm btn-primary"
                          disabled={onboard.running}
                          onClick={() => openModal([t.table])}
                        >
                          Onboard
                        </button>
                      )}
                      {rs.state === "needs_review" && (
                        <Link className="btn btn-sm" to={`/mappings/${rs.proposalId}`}>
                          Review
                        </Link>
                      )}
                      {(rs.state === "syncing" || rs.state === "paused") && (
                        <>
                          <Link className="btn btn-sm btn-ghost" to="/data">
                            View data
                          </Link>
                          <button
                            type="button"
                            className="btn btn-sm btn-ghost"
                            disabled={cancelling === t.table}
                            onClick={() => handleCancelQueue(t.table)}
                            title="Cancel queued events for this entity"
                          >
                            {cancelling === t.table ? "…" : "Cancel queue"}
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
              {filteredSourceTables.length === 0 && !tables.isLoading && (
                <tr>
                  <td colSpan={5} className="dim">
                    No source tables found in {SOURCE_SCHEMA}.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </section>

      {/* ---- View proposals ---- */}
      {viewProposals.data && viewProposals.data.length > 0 && (
        <section className="panel" style={{ marginTop: 16 }}>
          <div className="panel-header">
            <h3 className="panel-title">
              View proposals{" "}
              <span className="dim">
                ({viewProposals.data.filter((vp) => vp.status === "pending").length} pending)
              </span>
            </h3>
            <div style={{ display: "flex", gap: 6 }}>
              <button
                type="button"
                className="btn btn-sm btn-primary"
                disabled={
                  viewProposals.data.filter((vp) => vp.status === "pending").length === 0 ||
                  applyingAll
                }
                onClick={handleApplyAllViews}
              >
                {applyingAll ? "Applying all…" : "Apply all pending"}
              </button>
              <button
                type="button"
                className="btn btn-sm btn-ghost"
                onClick={() => void viewProposals.refetch()}
              >
                Refresh
              </button>
            </div>
          </div>
          {viewApplyMessage && <div className="alert alert-ok">{viewApplyMessage}</div>}
          {viewApplyError && <div className="alert alert-danger">{viewApplyError}</div>}
          <div className="table-scroll">
          {viewProposals.data.map((vp) => (
            <div
              key={vp.id}
              className="panel"
              style={{
                margin: 8,
                borderLeft: `4px solid ${
                  vp.status === "applied" ? "var(--color-ok)" : "var(--color-warn)"
                }`,
              }}
            >
              <div className="panel-header">
                <h4 className="mono" style={{ fontSize: 14 }}>
                  {vp.view_schema ?? "lrmis_projection"}.{vp.view_name}
                </h4>
                <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <span className={`chip chip-${vp.status === "applied" ? "flowing" : "idle"}`}>
                    {vp.status}
                  </span>
                  {vp.status === "pending" && (
                    <button
                      type="button"
                      className="btn btn-sm btn-primary"
                      disabled={applyingView === vp.id}
                      onClick={() => handleApplyView(vp.id)}
                    >
                      {applyingView === vp.id ? "Applying…" : "Apply view"}
                    </button>
                  )}
                  {vp.status === "applied" && !entities.some(
                    (entity) => entity.source_schema === (vp.view_schema ?? "lrmis_projection")
                      && entity.source_table === vp.view_name
                      && entity.status === "deployed",
                  ) && (
                    <button
                      type="button"
                      className="btn btn-sm btn-primary"
                      disabled={onboard.running}
                      onClick={() => openModal(
                        [vp.view_name],
                        vp.view_schema ?? "lrmis_projection",
                      )}
                    >
                      Onboard projection
                    </button>
                  )}
                </div>
              </div>
              <pre
                className="mono"
                style={{
                  fontSize: 12,
                  overflow: "auto",
                  maxHeight: 200,
                  padding: "8px 12px",
                  margin: 0,
                  background: "var(--color-bg-code)",
                  borderBottomLeftRadius: "var(--radius)",
                  borderBottomRightRadius: "var(--radius)",
                }}
              >
                {vp.view_sql}
              </pre>
              {vp.joined_tables && vp.joined_tables.length > 0 && (
                <p className="dim" style={{ padding: "6px 12px", fontSize: 12 }}>
                  Joins: {vp.joined_tables.map((j) => `${j.from_table}.${j.from_col} → ${j.to_table}.${j.to_col}`).join(", ")}
                </p>
              )}
            </div>
          ))}
          </div>
        </section>
      )}

      <GuardedActionModal
        open={modalOpen}
        tier="confirm"
        title={`Onboard ${pending.length} table${pending.length === 1 ? "" : "s"}`}
        description={
          <span>
            Set up syncing for{" "}
            <span className="mono">{pendingSchema}.{pending.join(", ")}</span>. Tables whose columns all match are
            turned on automatically; anything uncertain is routed to your review queue instead.
          </span>
        }
        actionLabel="Onboard"
        busy={onboard.running}
        error={onboard.submitError}
        onConfirm={submit}
        onClose={() => setModalOpen(false)}
      />
      <GuardedActionModal
        open={refreshAllModal}
        tier="typed"
        danger
        title={`Refresh all deployed tables (${deployedCount})`}
        description="Drop and recreate staging data for every deployed entity from the source."
        confirmString="REFRESH ALL"
        warning={
          <div>
            <p>
              <strong>This rewrites staging data for all {deployedCount} deployed entities.</strong>
            </p>
            <p>Deliveries for these entities may re-emit events after refresh.</p>
          </div>
        }
        actionLabel="Refresh all"
        busy={refreshAll.running}
        error={refreshAll.submitError}
        onConfirm={(reason) => {
          setRefreshAllModal(false);
          void refreshAll
            .run({
              job_type: "refresh_all",
              params: { source_schema: SOURCE_SCHEMA, target_system: TARGET_SYSTEM },
              reason,
              confirm: "REFRESH ALL",
            })
            .catch(() => {});
        }}
        onClose={() => setRefreshAllModal(false)}
      />
    </div>
  );
}
