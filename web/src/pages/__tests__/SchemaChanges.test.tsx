import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
vi.mock("../../hooks/useJobRunner", () => ({
  useJobRunner: () => ({
    run: vi.fn().mockResolvedValue(undefined),
    running: false,
    job: null,
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

describe("SchemaChanges — two-tree comparison views", () => {
  beforeEach(() => {
    vi.mocked(api.getSchemas).mockResolvedValue({
      source: tree("IRIMSV"),
      staging: tree("LRMIS"),
      target_b: tree("LRMIS_B"),
    } as never);
    vi.mocked(api.getDriftReports).mockResolvedValue([] as never);
    vi.mocked(api.getStatus).mockResolvedValue({
      entities: [], outbox_stats: [], queues: [], entity_controls: [],
      unresolved_quarantine: 0, unresolved_drift: 0,
    } as never);
  });

  it("shows the source and staging trees, then staging and target after Next", async () => {
    const user = userEvent.setup();
    renderPage();

    // First view: Source + Staging.
    expect(await screen.findByText("IRIMSV")).toBeInTheDocument();
    expect(screen.getByText("LRMIS")).toBeInTheDocument();
    expect(screen.queryByText("LRMIS_B")).not.toBeInTheDocument();

    // Advance to the Staging + Target view.
    await user.click(screen.getByRole("button", { name: /next/i }));
    expect(await screen.findByText("LRMIS_B")).toBeInTheDocument();
    expect(screen.queryByText("IRIMSV")).not.toBeInTheDocument();
  });

  it("relabels the scan input per view", async () => {
    const user = userEvent.setup();
    renderPage();

    expect(screen.getByRole("textbox", { name: "Source schema" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /next/i }));
    expect(await screen.findByRole("textbox", { name: "Staging schema" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Source schema" })).not.toBeInTheDocument();
  });

  it("filters drift reports to the active view's pair label", async () => {
    const user = userEvent.setup();
    renderPage();
    // Source ↔ Staging view defaults to the source->staging pair.
    expect((await screen.findAllByText("source->staging")).length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: /next/i }));
    expect((await screen.findAllByText("staging->target")).length).toBeGreaterThan(0);
  });
});
