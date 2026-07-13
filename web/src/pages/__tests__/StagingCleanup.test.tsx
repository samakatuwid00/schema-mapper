import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SchemaChanges from "../SchemaChanges";

vi.mock("../../api/client", () => ({
  getSchemas: vi.fn(),
  getDriftReports: vi.fn(),
  getStatus: vi.fn(),
  approveSchema: vi.fn(),
}));
vi.mock("../../api/sse", () => ({ subscribeJobEvents: () => ({ close: () => {} }) }));

// The cleanup runner is stubbed so we can drive its completed-job result state.
let cleanupJob: unknown = null;
vi.mock("../../hooks/useJobRunner", () => ({
  useJobRunner: () => ({
    run: vi.fn().mockResolvedValue(undefined),
    running: false,
    job: cleanupJob,
    jobId: null,
    submitError: null,
  }),
}));

import * as api from "../../api/client";

const tree = (name: string) => ({ fingerprint: `fp-${name}`, system_name: name, tables: [] });

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SchemaChanges />
    </QueryClientProvider>,
  );
}

describe("SchemaChanges — staging cleanup", () => {
  beforeEach(() => {
    cleanupJob = null;
    vi.mocked(api.getSchemas).mockResolvedValue({
      source: tree("IRIMSV"),
      staging: tree("LRMIS"),
      target_b: tree("LRMIS_B"),
    } as never);
    vi.mocked(api.getDriftReports).mockResolvedValue([] as never);
    vi.mocked(api.getStatus).mockResolvedValue({
      entities: [
        { id: 12, source_table: "farmers", staging_table: "irimsv_farmers_staging", status: "deployed" },
        { id: 13, source_table: "parcels", staging_table: "irimsv_parcels_staging", status: "paused" },
      ],
      outbox_stats: [], queues: [], entity_controls: [],
      unresolved_quarantine: 0, unresolved_drift: 0,
    } as never);
  });

  it("populates the Retire dropdown with deployed entities only", async () => {
    renderPage();
    const select = await screen.findByRole("combobox", { name: "Entity to retire" });
    // Deployed entity present…
    expect(await screen.findByRole("option", { name: /farmers → irimsv_farmers_staging/ })).toBeInTheDocument();
    // …paused entity excluded.
    expect(screen.queryByRole("option", { name: /parcels →/ })).not.toBeInTheDocument();
    expect(select).toBeInTheDocument();
  });

  it("renders a completed sweep job's structured result", async () => {
    cleanupJob = {
      id: 7, job_type: "sweep_staging", status: "succeeded",
      result: {
        orphans_found: ["irimsv_a_staging", "irimsv_b_staging"],
        dropped: ["irimsv_a_staging", "irimsv_b_staging"],
        snapshots: ["snap_a", "snap_b"],
        dry_run: false,
      },
    };
    renderPage();
    await waitFor(() => expect(screen.getByText(/Sweep complete/)).toBeInTheDocument());
    expect(screen.getByText(/2 orphans found, 2 dropped, 2 snapshots taken/)).toBeInTheDocument();
    // Appears in both the "Orphans found" and "Dropped" lists.
    expect(screen.getAllByText("irimsv_a_staging").length).toBeGreaterThan(0);
    expect(screen.getByText("snap_a")).toBeInTheDocument();
  });

  it("renders a completed retire job's structured result", async () => {
    cleanupJob = {
      id: 9, job_type: "retire_entity", status: "succeeded",
      result: {
        entity_id: 12, source_table: "farmers", staging_table: "irimsv_farmers_staging",
        status: "deployed", snapshot: "snap_farmers", dropped: true, dry_run: false,
      },
    };
    renderPage();
    await waitFor(() => expect(screen.getByText(/Retire complete/)).toBeInTheDocument());
    expect(screen.getByText(/staging table dropped/)).toBeInTheDocument();
    expect(screen.getByText("snap_farmers")).toBeInTheDocument();
  });
});
