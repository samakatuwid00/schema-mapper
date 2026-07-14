import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Recovery from "../Recovery";
import { NAV_GROUPS } from "../../App";

vi.mock("../../api/client", () => ({
  getRecoveryBackups: vi.fn(),
  restoreTarget: vi.fn(),
  restoreSource: vi.fn(),
  uploadRecoveryFile: vi.fn(),
}));

import * as api from "../../api/client";

const BACKUP = {
  id: "lrmis_target-20260714-010203.sql",
  path: "backups/lrmis_target-20260714-010203.sql",
  size_bytes: 4096,
  modified_at: "2026-07-14T01:02:03+00:00",
};

const VALID_SOURCE_UPLOAD = {
  id: 9,
  kind: "source_dump",
  original_filename: "replacement.sql",
  stored_path: "backups/uploads/x-replacement.sql",
  checksum: "c".repeat(64),
  size_bytes: 2048,
  valid: true,
  invalid_reason: null,
  uploaded_by: "tester",
  uploaded_at: "2026-07-14T02:00:00Z",
  used_at: null,
  used_by: null,
};

const INVALID_UPLOAD = {
  ...VALID_SOURCE_UPLOAD,
  id: 10,
  original_filename: "bad.sql",
  valid: false,
  invalid_reason: "file is UTF-16, expected UTF-8",
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Recovery />
    </QueryClientProvider>,
  );
}

describe("Recovery page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getRecoveryBackups).mockResolvedValue({
      target_backups: [BACKUP],
      uploads: [VALID_SOURCE_UPLOAD, INVALID_UPLOAD],
    } as never);
  });

  it("is reachable from the Maintain nav group", () => {
    const maintain = NAV_GROUPS.find((group) => group.title === "Maintain");
    expect(maintain?.items.map((item) => item.label)).toContain("Recovery");
    expect(maintain?.items.find((item) => item.label === "Recovery")?.to).toBe("/recovery");
  });

  it("lists automatic target backups and uploaded files", async () => {
    renderPage();
    expect(await screen.findByText(BACKUP.id)).toBeInTheDocument();
    expect(screen.getByText("replacement.sql")).toBeInTheDocument();
    expect(screen.getByText("bad.sql")).toBeInTheDocument();
  });

  it("shows the rejection reason and offers no restore for invalid uploads", async () => {
    renderPage();
    await screen.findByText("bad.sql");
    expect(
      screen.getByText(/rejected: file is UTF-16, expected UTF-8/i),
    ).toBeInTheDocument();
    // one restore button per VALID upload only
    expect(screen.getAllByRole("button", { name: /restore source…/i })).toHaveLength(1);
  });

  it("gates a target restore behind reason + typed confirmation", async () => {
    vi.mocked(api.restoreTarget).mockResolvedValue({ executed: true } as never);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Restore…" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeInTheDocument();
    const confirmButton = screen.getByRole("button", { name: "Restore target" });
    expect(confirmButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Reason"), {
      target: { value: "rebuild failed partway" },
    });
    expect(confirmButton).toBeDisabled(); // reason alone is not enough

    fireEvent.change(screen.getByLabelText("Confirmation text"), {
      target: { value: "lrmis_target" },
    });
    expect(confirmButton).toBeEnabled();

    fireEvent.click(confirmButton);
    await waitFor(() =>
      expect(api.restoreTarget).toHaveBeenCalledWith({
        backup_id: BACKUP.id,
        confirm: "lrmis_target",
        reason: "rebuild failed partway",
      }),
    );
  });

  it("triggers a source restore from a validated upload", async () => {
    vi.mocked(api.restoreSource).mockResolvedValue({ executed: true } as never);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /restore source…/i }));

    fireEvent.change(screen.getByLabelText("Reason"), {
      target: { value: "nightly dump unreadable" },
    });
    fireEvent.change(screen.getByLabelText("Confirmation text"), {
      target: { value: "irimsv" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Restore source" }));

    await waitFor(() =>
      expect(api.restoreSource).toHaveBeenCalledWith({
        upload_id: 9,
        confirm: "irimsv",
        reason: "nightly dump unreadable",
      }),
    );
    expect(await screen.findByText(/last restore/i)).toBeInTheDocument();
  });

  it("surfaces a restore error inside the modal", async () => {
    vi.mocked(api.restoreTarget).mockRejectedValue(
      new Error("typed confirmation mismatch - type 'lrmis_target' to proceed"),
    );
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Restore…" }));
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "x" } });
    fireEvent.change(screen.getByLabelText("Confirmation text"), {
      target: { value: "lrmis_target" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Restore target" }));
    expect(
      await screen.findByText(/typed confirmation mismatch/i),
    ).toBeInTheDocument();
  });
});
