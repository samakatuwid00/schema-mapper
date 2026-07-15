import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import JobDrawer from "../JobDrawer";

vi.mock("../../api/client", () => ({ listJobs: vi.fn() }));
vi.mock("../../api/sse", () => ({ subscribeJobEvents: vi.fn(() => ({ close: vi.fn() })) }));

import * as api from "../../api/client";

function renderDrawer(jobs: unknown[], onRepairWithAssistant = vi.fn()) {
  vi.mocked(api.listJobs).mockResolvedValue(jobs as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <JobDrawer onRepairWithAssistant={onRepairWithAssistant} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByTitle("Toggle job drawer"));
  return utils;
}

const FAILED_DEPLOY_JOB = {
  id: 42, job_type: "deploy_lrmis", status: "failed",
  created_at: "2026-01-01T00:00:00Z",
  error_message: "mapping cannot be deployed", proposal_id: 582,
};

describe("JobDrawer recovery actions", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows Open proposal + Repair with Assistant for a recoverable failed deploy job", async () => {
    renderDrawer([FAILED_DEPLOY_JOB]);
    await waitFor(() => screen.getByText("Open proposal"));
    expect(screen.getByText("Open proposal").closest("a"))
      .toHaveAttribute("href", "/mappings/582");
    expect(screen.getByText(/Repair with Assistant/)).toBeInTheDocument();
  });

  it("calls onRepairWithAssistant with the job's repair context", async () => {
    const onRepair = vi.fn();
    renderDrawer([FAILED_DEPLOY_JOB], onRepair);
    await waitFor(() => screen.getByText(/Repair with Assistant/));
    fireEvent.click(screen.getByText(/Repair with Assistant/));
    expect(onRepair).toHaveBeenCalledWith({
      job_id: 42, job_type: "deploy_lrmis", status: "failed",
      error_message: "mapping cannot be deployed", proposal_id: 582,
    });
  });

  it("hides recovery actions for a failed job with no recoverable proposal id", async () => {
    renderDrawer([{ ...FAILED_DEPLOY_JOB, proposal_id: null }]);
    await waitFor(() => screen.getByText(/#42/));
    expect(screen.queryByText("Open proposal")).toBeNull();
    expect(screen.queryByText(/Repair with Assistant/)).toBeNull();
  });

  it("hides recovery actions for a non-deploy job type", async () => {
    renderDrawer([{ ...FAILED_DEPLOY_JOB, job_type: "refresh_all" }]);
    await waitFor(() => screen.getByText(/#42/));
    expect(screen.queryByText("Open proposal")).toBeNull();
  });

  it("hides recovery actions for a job that is not failed", async () => {
    renderDrawer([{ ...FAILED_DEPLOY_JOB, status: "succeeded" }]);
    await waitFor(() => screen.getByText(/#42/));
    expect(screen.queryByText("Open proposal")).toBeNull();
  });
});
