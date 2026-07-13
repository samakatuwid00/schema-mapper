import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MappingReview from "../MappingReview";

vi.mock("../../api/client", () => ({
  getProposal: vi.fn(),
  getProposals: vi.fn(),
  resolveMapping: vi.fn(),
  approveMapping: vi.fn(),
  createJob: vi.fn(),
  getJob: vi.fn(),
  getLrmisSchema: vi.fn(),
}));

import * as api from "../../api/client";

const PROPOSAL = {
  proposal: {
    id: 1,
    entity_id: 5,
    source_schema: "irimsv",
    source_table: "authors",
    target_system: "LRMIS",
    status: "approved",
    source_fingerprint: "abc123def456",
    target_fingerprint: "def456abc123",
    unmet_required_columns: [] as string[],
  },
  fields: [],
};

function renderReview() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/mappings/1"]}>
        <Routes>
          <Route path="/mappings/:proposalId" element={<MappingReview />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("MappingReview — Deploy to target", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getProposal).mockResolvedValue(PROPOSAL as never);
    vi.mocked(api.getProposals).mockResolvedValue([] as never);
    vi.mocked(api.createJob).mockResolvedValue({ job_id: 42, created_at: "now" } as never);
    vi.mocked(api.getJob).mockResolvedValue({ status: "succeeded" } as never);
    vi.mocked(api.getLrmisSchema).mockResolvedValue({
      tables: { station: ["id", "geoloc"], author: ["id", "name"] },
    } as never);
  });

  it("resolves a pending field to a chosen LRMIS table.column", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getProposal).mockResolvedValue({
      proposal: { ...PROPOSAL.proposal },
      fields: [
        {
          id: 11,
          source_column: "school_name",
          suggested_target_table: null,
          suggested_target_column: null,
          resolved_target_column: null,
          confidence: 0,
          transform: "none",
          resolved_transform: null,
          status: "pending",
          reasoning: null,
        },
      ],
    } as never);
    vi.mocked(api.resolveMapping).mockResolvedValue({} as never);
    renderReview();

    await user.type(await screen.findByLabelText("Target table for school_name"), "station");
    await user.type(screen.getByLabelText("Target column for school_name"), "geoloc");
    await user.click(screen.getByRole("button", { name: "Resolve" }));

    expect(api.resolveMapping).toHaveBeenCalledWith(
      expect.objectContaining({
        source_column: "school_name",
        target_table: "station",
        target_column: "geoloc",
      }),
    );
  });

  it("bulk-resolves every column that carries a usable AI suggestion", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getProposal).mockResolvedValue({
      proposal: { ...PROPOSAL.proposal, status: "needs_review" },
      fields: [
        {
          id: 11, source_column: "author_name", suggested_target_table: "author",
          suggested_target_column: "name", resolved_target_column: null, confidence: 0.9,
          transform: "none", resolved_transform: null, status: "pending", reasoning: null,
        },
        {
          id: 12, source_column: "author_id", suggested_target_table: "author",
          suggested_target_column: "id", resolved_target_column: null, confidence: 0.95,
          transform: "cast:str->int", resolved_transform: null, status: "pending", reasoning: null,
        },
        {
          // legacy staging suggestion -> NOT bulk-resolvable, needs a human
          id: 13, source_column: "misc", suggested_target_table: "irimsv_misc_staging",
          suggested_target_column: "misc", resolved_target_column: null, confidence: 0.2,
          transform: "none", resolved_transform: null, status: "pending", reasoning: null,
        },
      ],
    } as never);
    vi.mocked(api.resolveMapping).mockResolvedValue({} as never);
    renderReview();

    // Only the two real-LRMIS suggestions are offered; the staging one is excluded.
    const bulk = await screen.findByRole("button", { name: /resolve all 2 suggested/i });
    await user.click(bulk);

    expect(api.resolveMapping).toHaveBeenCalledTimes(2);
    expect(api.resolveMapping).toHaveBeenCalledWith(
      expect.objectContaining({
        source_column: "author_name", target_table: "author", target_column: "name",
      }),
    );
    expect(api.resolveMapping).toHaveBeenCalledWith(
      expect.objectContaining({
        source_column: "author_id", target_table: "author", target_column: "id",
        transform: "cast:str->int",
      }),
    );
  });

  it("hides 'Resolve all' when no column has a usable suggestion", async () => {
    vi.mocked(api.getProposal).mockResolvedValue({
      proposal: { ...PROPOSAL.proposal, status: "needs_review" },
      fields: [
        {
          id: 11, source_column: "school_name", suggested_target_table: null,
          suggested_target_column: null, resolved_target_column: null, confidence: 0,
          transform: "none", resolved_transform: null, status: "pending", reasoning: null,
        },
      ],
    } as never);
    renderReview();

    await screen.findByLabelText("Target table for school_name");
    expect(screen.queryByRole("button", { name: /resolve all/i })).not.toBeInTheDocument();
  });

  it("shows an enabled 'Deploy to target' button for an approved proposal", async () => {
    renderReview();
    const btn = await screen.findByRole("button", { name: /deploy to target/i });
    expect(btn).toBeEnabled();
  });

  it("keeps 'Deploy to target' disabled until the proposal is approved", async () => {
    vi.mocked(api.getProposal).mockResolvedValue({
      ...PROPOSAL,
      proposal: { ...PROPOSAL.proposal, status: "needs_review" },
    } as never);
    renderReview();
    const btn = await screen.findByRole("button", { name: /deploy to target/i });
    expect(btn).toBeDisabled();
  });

  it("keeps 'Deploy to target' enabled despite the proposal's unmet_required_columns", async () => {
    // unmet_required_columns is a legacy whole-schema count and is misleadingly
    // high for the multi-table target; deploy_to_lrmis is the real gate, so the
    // UI must not block on it.
    vi.mocked(api.getProposal).mockResolvedValue({
      ...PROPOSAL,
      proposal: { ...PROPOSAL.proposal, unmet_required_columns: ["station_id", "beis.id"] },
    } as never);
    renderReview();
    const btn = await screen.findByRole("button", { name: /deploy to target/i });
    expect(btn).toBeEnabled();
  });

  it("enqueues the deploy_lrmis job with the proposal id and reason", async () => {
    const user = userEvent.setup();
    renderReview();

    await user.click(await screen.findByRole("button", { name: /deploy to target/i }));

    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Deploy to target" });
    expect(confirm).toBeDisabled(); // reason required first

    await user.type(within(dialog).getByLabelText("Reason"), "migrating authors to the LRMIS target");
    expect(confirm).toBeEnabled();
    await user.click(confirm);

    expect(api.createJob).toHaveBeenCalledWith({
      job_type: "deploy_lrmis",
      params: { proposal_id: 1 },
      reason: "migrating authors to the LRMIS target",
    });
  });
});

const DEPLOYABLE = [
  {
    proposal_id: 7, status: "approved", source_schema: "irimsv", source_table: "districts",
    target_system: "LRMIS", unmet_required_columns: [] as string[], on_target: false,
    has_lrmis_mapping: true,
    auto_approved_count: 0, needs_review_count: 0, rejected_count: 0,
    created_at: "", updated_at: "", reviewed_by: null, entity_id: 9,
    entity_status: "deployed", pending_fields: 0,
  },
  {
    proposal_id: 8, status: "approved", source_schema: "irimsv", source_table: "authors",
    target_system: "LRMIS", unmet_required_columns: [] as string[], on_target: true, // already migrated
    auto_approved_count: 0, needs_review_count: 0, rejected_count: 0,
    created_at: "", updated_at: "", reviewed_by: null, entity_id: 4,
    entity_status: "deployed", pending_fields: 0,
  },
  {
    // AI could not map it: auto_approved but no LRMIS mapping -> needs manual mapping
    proposal_id: 9, status: "auto_approved", source_schema: "irimsv", source_table: "schools",
    target_system: "LRMIS", unmet_required_columns: [] as string[], on_target: false,
    has_lrmis_mapping: false,
    auto_approved_count: 0, needs_review_count: 0, rejected_count: 5,
    created_at: "", updated_at: "", reviewed_by: null, entity_id: 12,
    entity_status: "deployed", pending_fields: 0,
  },
];

function renderPicker() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/mappings"]}>
        <Routes>
          <Route path="/mappings" element={<MappingReview />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("MappingReview — ready-to-deploy picker", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getProposals).mockImplementation((status?: string) =>
      Promise.resolve((status === "needs_review" ? [] : DEPLOYABLE) as never),
    );
    vi.mocked(api.createJob).mockResolvedValue({ job_id: 7, created_at: "now" } as never);
    vi.mocked(api.getJob).mockResolvedValue({ status: "succeeded" } as never);
  });

  it("lists approved, not-yet-deployed proposals and excludes on-target ones", async () => {
    renderPicker();
    expect(await screen.findByText("irimsv.districts")).toBeInTheDocument();
    expect(screen.queryByText("irimsv.authors")).not.toBeInTheDocument();
  });

  it("deploys the picked proposal via deploy_lrmis", async () => {
    const user = userEvent.setup();
    renderPicker();
    await screen.findByText("irimsv.districts");

    await user.click(screen.getByRole("button", { name: /deploy to target/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Reason"), "migrate districts");
    await user.click(within(dialog).getByRole("button", { name: "Deploy to target" }));

    expect(api.createJob).toHaveBeenCalledWith({
      job_type: "deploy_lrmis",
      params: { proposal_id: 7 },
      reason: "migrate districts",
    });
  });

  it("bulk-deploys all ready proposals via bulk_deploy_lrmis", async () => {
    const user = userEvent.setup();
    renderPicker();
    await screen.findByText("irimsv.districts");

    await user.click(screen.getByRole("button", { name: /deploy all/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Reason"), "migrate all ready");
    await user.click(within(dialog).getByRole("button", { name: "Deploy all" }));

    expect(api.createJob).toHaveBeenCalledWith({
      job_type: "bulk_deploy_lrmis",
      params: {},
      reason: "migrate all ready",
    });
  });

  it("surfaces un-mappable entities under 'Needs manual mapping' with a Map link", async () => {
    renderPicker();
    // schools has no LRMIS mapping -> appears in needs-mapping with a Map link
    expect(await screen.findByText("irimsv.schools")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /map →/i })).toHaveAttribute("href", "/mappings/9");
  });

  it("re-proposes all deployed entities via bulk_propose_lrmis", async () => {
    const user = userEvent.setup();
    renderPicker();

    await user.click(await screen.findByRole("button", { name: /re-propose all/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Reason"), "regenerate lrmis mappings");
    await user.click(within(dialog).getByRole("button", { name: "Re-propose all" }));

    expect(api.createJob).toHaveBeenCalledWith({
      job_type: "bulk_propose_lrmis",
      params: {},
      reason: "regenerate lrmis mappings",
    });
  });
});
