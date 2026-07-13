import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DataBrowser from "../DataBrowser";

vi.mock("../../api/client", () => ({
  getDataTables: vi.fn(),
  getDataRows: vi.fn(),
  compareRow: vi.fn(),
  compareStagingTarget: vi.fn(),
}));

import * as api from "../../api/client";

const TABLES = {
  source: {
    schema: "irimsv",
    tables: [
      {
        table: "farmers",
        columns: 2,
        rows: 2,
        entity_status: "deployed",
        staging_table: "stg_farmers",
      },
    ],
  },
  target: { database: "lrmis_staging", tables: [] },
};

const ROWS = {
  side: "source" as const,
  table: "farmers",
  columns: [
    { name: "id", data_type: "int", nullable: false, is_primary_key: true },
    { name: "name", data_type: "text", nullable: true, is_primary_key: false },
  ],
  rows: [
    { id: 1, name: "Alice" },
    { id: 2, name: null },
  ],
  total: 2,
  page: 1,
  size: 25,
  pages: 1,
};

function renderBrowser() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DataBrowser />
    </QueryClientProvider>,
  );
}

describe("DataBrowser", () => {
  beforeEach(() => {
    vi.mocked(api.getDataTables).mockResolvedValue(TABLES);
    vi.mocked(api.getDataRows).mockResolvedValue(ROWS);
  });

  it("renders the grid rows and shows NULL as ∅", async () => {
    renderBrowser();
    // A real value from the page of rows.
    expect(await screen.findByText("Alice")).toBeInTheDocument();
    // The null value renders as a dim ∅, never a blank cell.
    expect(screen.getByText("∅")).toBeInTheDocument();
  });

  it("re-fetches sorted when a column header is clicked", async () => {
    const user = userEvent.setup();
    renderBrowser();
    await screen.findByText("Alice");

    vi.mocked(api.getDataRows).mockClear();
    await user.click(screen.getByText("name")); // the "name" column header

    await waitFor(() =>
      expect(api.getDataRows).toHaveBeenCalledWith(
        expect.objectContaining({ sort: "name", direction: "asc", table: "farmers" }),
      ),
    );
  });

  it("shows one database table list at a time", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getDataTables).mockResolvedValue({
      ...TABLES,
      target: {
        database: "lrmis_staging",
        tables: [{ table: "stg_farmers", source_table: "farmers", columns: 2, rows: 2, entity_status: "deployed" }],
      },
    });
    renderBrowser();

    expect(await screen.findByText("farmers")).toBeInTheDocument();
    expect(screen.queryByText("stg_farmers")).not.toBeInTheDocument();

    // Path A staging is now the "Staging DB" option (a third "Target DB" = Path B).
    await user.selectOptions(screen.getByRole("combobox", { name: /database/i }), "target");
    expect(await screen.findByText("stg_farmers")).toBeInTheDocument();
    expect(screen.queryByTitle("farmers")).not.toBeInTheDocument();
  });

  it("exposes no insert / edit / delete controls (read-only)", async () => {
    renderBrowser();
    await screen.findByText("Alice");
    expect(
      screen.queryByRole("button", {
        name: /insert|add|edit|delete|save|update|remove|new row/i,
      }),
    ).toBeNull();
  });
});
