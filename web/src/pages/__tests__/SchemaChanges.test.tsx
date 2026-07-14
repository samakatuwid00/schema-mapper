import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SchemaChanges from "../SchemaChanges";

vi.mock("../../api/client", () => ({
  getSchemas: vi.fn(),
  getDriftReports: vi.fn(),
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

describe("SchemaChanges — source ↔ target comparison", () => {
  beforeEach(() => {
    vi.mocked(api.getSchemas).mockResolvedValue({
      source: tree("IRIMSV"),
      target: tree("LRMIS"),
    } as never);
    vi.mocked(api.getDriftReports).mockResolvedValue([] as never);
  });

  it("shows the source and target trees side by side", async () => {
    renderPage();
    expect(await screen.findByText("IRIMSV")).toBeInTheDocument();
    expect(screen.getByText("LRMIS")).toBeInTheDocument();
  });

  it("scans the source (no staging scan mode)", async () => {
    renderPage();
    expect(await screen.findByRole("textbox", { name: "Source schema" })).toBeInTheDocument();
    // No per-view navigation remains — there is a single source → target scan.
    expect(screen.getByRole("button", { name: /scan source → target/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /next/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Staging schema" })).not.toBeInTheDocument();
  });
});
