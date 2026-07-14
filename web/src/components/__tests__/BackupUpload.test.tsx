import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import BackupUpload from "../BackupUpload";

vi.mock("../../api/client", () => ({
  uploadRecoveryFile: vi.fn(),
}));

import * as api from "../../api/client";

const row = (overrides: Record<string, unknown> = {}) => ({
  id: 1,
  kind: "source_dump",
  original_filename: "dump.sql",
  stored_path: "backups/uploads/abc-dump.sql",
  checksum: "c".repeat(64),
  size_bytes: 123,
  valid: true,
  invalid_reason: null,
  uploaded_by: "tester",
  uploaded_at: "2026-07-14T00:00:00Z",
  used_at: null,
  used_by: null,
  ...overrides,
});

function pickFile() {
  const input = screen.getByLabelText("source dump file", { selector: "input" });
  fireEvent.change(input, {
    target: { files: [new File(["CREATE SCHEMA irimsv;"], "dump.sql")] },
  });
}

describe("BackupUpload", () => {
  beforeEach(() => vi.clearAllMocks());

  it("uploads a picked file and reports a passing validation", async () => {
    vi.mocked(api.uploadRecoveryFile).mockResolvedValue(row() as never);
    render(<BackupUpload kind="source_dump" />);
    pickFile();
    expect(await screen.findByRole("status")).toHaveTextContent(
      /passed validation/i,
    );
    expect(api.uploadRecoveryFile).toHaveBeenCalledWith(
      expect.any(File), "source_dump", expect.any(Function));
  });

  it("surfaces the exact rejection reason for an invalid file", async () => {
    vi.mocked(api.uploadRecoveryFile).mockResolvedValue(
      row({ valid: false, invalid_reason: "file is UTF-16, expected UTF-8" }) as never,
    );
    render(<BackupUpload kind="source_dump" />);
    pickFile();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("file is UTF-16, expected UTF-8");
    expect(alert).toHaveTextContent(/can never be restored from/i);
  });

  it("notifies the parent when an upload lands", async () => {
    const uploaded = vi.fn();
    vi.mocked(api.uploadRecoveryFile).mockResolvedValue(row() as never);
    render(<BackupUpload kind="source_dump" onUploaded={uploaded} />);
    pickFile();
    await screen.findByRole("status");
    expect(uploaded).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }));
  });

  it("accepts a drag-and-drop file", async () => {
    vi.mocked(api.uploadRecoveryFile).mockResolvedValue(row() as never);
    render(<BackupUpload kind="source_dump" />);
    fireEvent.drop(screen.getByRole("button", { name: "Upload source dump" }), {
      dataTransfer: { files: [new File(["CREATE SCHEMA irimsv;"], "dump.sql")] },
    });
    expect(await screen.findByRole("status")).toBeInTheDocument();
  });

  it("shows a transport error distinctly from a validation rejection", async () => {
    vi.mocked(api.uploadRecoveryFile).mockRejectedValue(new Error("network error"));
    render(<BackupUpload kind="source_dump" />);
    pickFile();
    expect(await screen.findByText(/network error/i)).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
