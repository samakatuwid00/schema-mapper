import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Tables from "../Tables";

vi.mock("../../api/client", () => ({
  getDataTables: vi.fn(),
  getStatus: vi.fn(),
  getProposals: vi.fn(),
  createJob: vi.fn(),
  getJob: vi.fn(),
}));

// Avoid touching EventSource in jsdom; the batch job stream is irrelevant here.
vi.mock("../../api/sse", () => ({
  subscribeJobEvents: vi.fn(() => ({ close: vi.fn() })),
}));

import * as api from "../../api/client";

const TABLES = {
  source: {
    schema: "irimsv",
    tables: [
      { table: "farmers", columns: 3, rows: 10, entity_status: null, staging_table: null },
      { table: "parcels", columns: 4, rows: 20, entity_status: null, staging_table: null },
    ],
  },
  target: { database: "lrmis_staging", tables: [] },
};

const STATUS = {
  entities: [],
  outbox_stats: [],
  queues: [],
  entity_controls: [],
  unresolved_quarantine: 0,
  unresolved_drift: 0,
};

function renderTables() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Tables />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Tables (bulk onboard)", () => {
  beforeEach(() => {
    vi.mocked(api.getDataTables).mockResolvedValue(TABLES);
    vi.mocked(api.getStatus).mockResolvedValue(STATUS);
    vi.mocked(api.getProposals).mockResolvedValue([]);
  });

  it("disables 'Onboard selected' when nothing is checked", async () => {
    renderTables();
    const bulk = await screen.findByRole("button", { name: /onboard selected/i });
    expect(bulk).toBeDisabled();
  });

  it("select-all checks every table and enables the bulk action", async () => {
    const user = userEvent.setup();
    renderTables();
    await screen.findByText("farmers");

    await user.click(screen.getByLabelText("Select all tables"));

    expect(screen.getByLabelText("Select farmers")).toBeChecked();
    expect(screen.getByLabelText("Select parcels")).toBeChecked();
    expect(screen.getByRole("button", { name: /onboard selected/i })).toBeEnabled();
  });

  it("requires a reason before the onboard confirm button enables", async () => {
    const user = userEvent.setup();
    renderTables();
    await screen.findByText("farmers");

    await user.click(screen.getByLabelText("Select farmers"));
    await user.click(screen.getByRole("button", { name: /onboard selected/i }));

    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Onboard" });
    expect(confirm).toBeDisabled();

    await user.type(within(dialog).getByLabelText("Reason"), "onboarding farmers for Q3");
    expect(confirm).toBeEnabled();
  });
});
