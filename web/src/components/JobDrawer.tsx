import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { MessageSquareWarning } from "lucide-react";
import { listJobs } from "../api/client";
import { subscribeJobEvents } from "../api/sse";
import type { JobSummary, PinnedJobContext } from "../api/types";
import { fmtAgo } from "../utils";
import StatusChip from "./StatusChip";

const RECOVERABLE_JOB_TYPES = new Set(["deploy_lrmis", "deploy"]);

function jobRepairContext(job: JobSummary): PinnedJobContext {
  return {
    job_id: job.id, job_type: job.job_type, status: job.status,
    error_message: job.error_message, proposal_id: job.proposal_id,
  };
}

function JobProgress({ job }: { job: JobSummary }) {
  const total = job.progress_total ?? 0;
  const current = job.progress_current ?? 0;
  if (job.status !== "running" && job.status !== "queued" && total === 0) return null;
  const pct = total > 0 ? Math.min(100, (current / total) * 100) : job.status === "running" ? 100 : 0;
  return (
    <div className="progress" title={total > 0 ? `${current}/${total}` : undefined}>
      <div
        className={`progress-fill${total === 0 && job.status === "running" ? " indeterminate" : ""}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

export interface JobDrawerProps {
  onRepairWithAssistant?: (job: PinnedJobContext) => void;
}

/** Collapsible right-side drawer: last 20 jobs, live-updated via /api/events SSE. */
export default function JobDrawer({ onRepairWithAssistant }: JobDrawerProps) {
  const [open, setOpen] = useState(false);
  const [connected, setConnected] = useState(false);
  const queryClient = useQueryClient();

  const { data } = useQuery({
    queryKey: ["jobs"],
    queryFn: listJobs,
    refetchInterval: 15000,
  });

  useEffect(() => {
    // Coalesce bursts: a running job emits a progress event per table, and one
    // refetch per event would hammer the API. Refresh at most once per tick.
    let pending: number | undefined;
    const refreshSoon = () => {
      if (pending !== undefined) return;
      pending = window.setTimeout(() => {
        pending = undefined;
        void queryClient.invalidateQueries({ queryKey: ["jobs"] });
        void queryClient.invalidateQueries({ queryKey: ["job"] });
      }, 400);
    };

    const handle = subscribeJobEvents("/api/events", refreshSoon, setConnected);
    return () => {
      if (pending !== undefined) window.clearTimeout(pending);
      handle.close();
    };
  }, [queryClient]);

  const jobs = [...(data ?? [])]
    .sort((a, b) => (b.created_at > a.created_at ? 1 : -1))
    .slice(0, 20);
  const activeCount = jobs.filter((j) => j.status === "running" || j.status === "queued").length;

  return (
    <>
      <button
        type="button"
        className={`job-drawer-tab${activeCount > 0 ? " active" : ""}`}
        onClick={() => setOpen(!open)}
        title="Toggle job drawer"
      >
        Jobs{activeCount > 0 ? ` (${activeCount})` : ""}
      </button>
      <aside className={`job-drawer${open ? " open" : ""}`} aria-label="Job drawer" aria-hidden={!open}>
        <div className="job-drawer-header">
          <h3 className="panel-title">Recent jobs</h3>
          <span className={`sse-dot${connected ? " on" : ""}`} title={connected ? "live events connected" : "reconnecting…"} />
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setOpen(false)}>
            ✕
          </button>
        </div>
        <div className="job-drawer-body">
          {jobs.length === 0 && <p className="dim empty-note">No jobs yet.</p>}
          {jobs.map((job) => (
            <div key={job.id} className="job-item">
              <div className="job-item-top">
                <span className="mono job-type">
                  #{job.id} {job.job_type}
                </span>
                <StatusChip status={job.status} />
              </div>
              <JobProgress job={job} />
              <div className="job-item-meta dim">
                {job.requested_by && <span>by {job.requested_by}</span>}
                <span>{fmtAgo(job.created_at)} ago</span>
                {typeof job.progress_total === "number" && job.progress_total > 0 && (
                  <span className="mono">
                    {job.progress_current ?? 0}/{job.progress_total}
                  </span>
                )}
              </div>
              {job.error_message && <div className="job-error mono">{job.error_message}</div>}
              {job.status === "failed" && RECOVERABLE_JOB_TYPES.has(job.job_type)
                && job.proposal_id && (
                <div className="job-item-recovery">
                  <Link className="btn btn-ghost btn-xs" to={`/mappings/${job.proposal_id}`}>
                    Open proposal
                  </Link>
                  <button type="button" className="btn btn-ghost btn-xs"
                          onClick={() => onRepairWithAssistant?.(jobRepairContext(job))}>
                    <MessageSquareWarning size={12} aria-hidden="true" /> Repair with Assistant
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      </aside>
    </>
  );
}
