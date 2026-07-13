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
    tables: [{ table: "farmers", columns: 2, rows: 1, entity_status: "deployed", staging_table: "stg_farmers" }],
  },
  target: {
    database: "lrmis_staging",
    tables: [{ table: "stg_parcels", columns: 2, rows: 1, entity_status: "deployed", source_table: "parcels_src" }],
  },
  path_b: {
    database: "lrmis_target",
    tables: [{ table: "parcels", columns: 2, rows: 1, staging_table: "stg_parcels" }],
  },
};

function rowsFor(side: string) {
  if (side === "source") {
    return {
      side, table: "farmers",
      columns: [
        { name: "id", data_type: "int", nullable: false, is_primary_key: true },
        { name: "external_reference", data_type: "char", nullable: true, is_primary_key: false },
      ],
      rows: [{ id: 1, external_reference: "ref-1" }],
      total: 1, page: 1, size: 25, pages: 1,
    };
  }
  return {
    side, table: side === "path_b" ? "parcels" : "stg_parcels",
    columns: [
      { name: "parcel_id", data_type: "int", nullable: false, is_primary_key: true },
      { name: "area", data_type: "decimal", nullable: true, is_primary_key: false },
    ],
    rows: [{ parcel_id: 1, area: 10 }],
    total: 1, page: 1, size: 25, pages: 1,
  };
}

function renderBrowser() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DataBrowser />
    </QueryClientProvider>,
  );
}

describe("DataBrowser — three-way comparison rules", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getDataTables).mockResolvedValue(TABLES);
    vi.mocked(api.getDataRows).mockImplementation((p) => Promise.resolve(rowsFor(p.side) as never));
    vi.mocked(api.compareRow).mockResolvedValue({
      entity: "farmers", external_reference: "ref-1", staging_table: "stg_farmers",
      delivery_status: "delivered", source_row: {}, target_row: {},
      missing_in_target: false, missing_in_source: false, fields: [],
    } as never);
    vi.mocked(api.compareStagingTarget).mockResolvedValue({
      staging_table: "stg_parcels", path_b_table: "parcels", primary_key: "parcel_id",
      primary_key_value: 1, staging_row: {}, target_row: {},
      missing_in_target: false, missing_in_staging: false, fields: [],
    } as never);
  });

  it("offers all three database options", async () => {
    renderBrowser();
    await screen.findByText("farmers");
    expect(screen.getByRole("combobox", { name: /database/i })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Source DB" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Staging DB" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Target DB" })).toBeInTheDocument();
  });

  it("Target (Path B) compares to staging by primary key", async () => {
    const user = userEvent.setup();
    renderBrowser();
    await screen.findByText("farmers");

    await user.selectOptions(screen.getByRole("combobox", { name: /database/i }), "path_b");
    await user.click(await screen.findByText("parcels"));
    // grid loaded for path_b
    await screen.findByText("area");

    await user.click(screen.getByRole("button", { name: /Compare/ }));
    await user.click(await screen.findByRole("button", { name: "Compare row" }));

    await waitFor(() =>
      expect(api.compareStagingTarget).toHaveBeenCalledWith("stg_parcels", "1"),
    );
  });

  it("Source compares to staging by external_reference, never to Path B", async () => {
    const user = userEvent.setup();
    renderBrowser();
    await screen.findByText("farmers");

    await user.click(screen.getByRole("button", { name: /Compare/ }));
    await user.click(await screen.findByRole("button", { name: "Compare row" }));

    await waitFor(() => expect(api.compareRow).toHaveBeenCalledWith("farmers", "ref-1"));
    // Source ↔ Target (Path B) is never offered.
    expect(api.compareStagingTarget).not.toHaveBeenCalled();
  });
});
