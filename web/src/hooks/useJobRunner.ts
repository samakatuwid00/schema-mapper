import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createJob, getJob } from "../api/client";
import type { CreateJobPayload, JobDetail } from "../api/types";
import { errMsg } from "../utils";

export interface JobRunner {
  /** Submit a job; resolves with the job id. Rethrows API errors. */
  run: (payload: CreateJobPayload) => Promise<number>;
  jobId: number | null;
  job: JobDetail | null;
  running: boolean;
  submitError: string | null;
  reset: () => void;
}

/** Submits a job then polls GET /api/jobs/{id} until it finishes. */
export function useJobRunner(): JobRunner {
  const [jobId, setJobId] = useState<number | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data: job } = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => getJob(jobId as number),
    enabled: jobId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" || status === undefined ? 1500 : false;
    },
  });

  const run = async (payload: CreateJobPayload): Promise<number> => {
    setSubmitError(null);
    try {
      const res = await createJob(payload);
      setJobId(res.job_id);
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      return res.job_id;
    } catch (err) {
      setSubmitError(errMsg(err));
      throw err;
    }
  };

  const running = job ? job.status === "queued" || job.status === "running" : jobId !== null && !job;

  return {
    run,
    jobId,
    job: job ?? null,
    running,
    submitError,
    reset: () => {
      setJobId(null);
      setSubmitError(null);
    },
  };
}
