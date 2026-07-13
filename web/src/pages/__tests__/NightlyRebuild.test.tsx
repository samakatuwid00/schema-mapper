import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import NightlyRebuild from "../NightlyRebuild";

vi.mock("../../api/client", () => ({
  createJob: vi.fn(),
  getJob: vi.fn(),
}));

import * as api from "../../api/client";

function renderRebuild() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <NightlyRebuild />
    </QueryClientProvider>,
  );
}

describe("NightlyRebuild", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.createJob).mockResolvedValue({ job_id: 5, created_at: "now" } as never);
    vi.mocked(api.getJob).mockResolvedValue({
      status: "succeeded",
      result: { dry_run: true, entities: [] },
    } as never);
  });

  it("dry run enqueues nightly_refresh with dry_run and no confirmation", async () => {
    const user = userEvent.setup();
    renderRebuild();
    await user.click(screen.getByRole("button", { name: "Dry run" }));
    expect(api.createJob).toHaveBeenCalledWith({
      job_type: "nightly_refresh",
      params: { dry_run: true },
    });
  });

  it("real rebuild requires typing REBUILD and a reason", async () => {
    const user = userEvent.setup();
    renderRebuild();

    await user.click(screen.getByRole("button", { name: /run rebuild/i }));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Run rebuild" });
    expect(confirm).toBeDisabled();

    await user.type(within(dialog).getByLabelText("Reason"), "midnight rebuild");
    expect(confirm).toBeDisabled(); // still needs the typed confirmation

    await user.type(within(dialog).getByLabelText("Confirmation text"), "REBUILD");
    expect(confirm).toBeEnabled();
    await user.click(confirm);

    expect(api.createJob).toHaveBeenCalledWith({
      job_type: "nightly_refresh",
      params: { restore: false },
      reason: "midnight rebuild",
      confirm: "REBUILD",
    });
  });
});
