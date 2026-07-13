import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import PipelineDiagram from "../PipelineDiagram";

const base = {
  pending: 0,
  retry: 0,
  blocked: 0,
  delivered: 12,
  workerRunning: false,
  queueProcessing: false,
  deliveryProcessing: false,
};

describe("PipelineDiagram", () => {
  it("animates source-to-queue while a data preparation job is running", () => {
    const { container } = render(<PipelineDiagram {...base} queueProcessing />);

    expect(screen.getByText("preparing data")).toBeInTheDocument();
    expect(screen.getByText("reading source")).toBeInTheDocument();
    expect(container.querySelectorAll(".pipe-pulse")).toHaveLength(2);
  });

  it("animates through the target while delivery is processing", () => {
    const { container } = render(<PipelineDiagram {...base} deliveryProcessing />);

    expect(screen.getByText("delivering")).toBeInTheDocument();
    expect(screen.getByText(/receiving/)).toBeInTheDocument();
    expect(container.querySelectorAll(".pipe-pulse")).toHaveLength(4);
  });

  it("keeps an idle worker from showing false delivery activity", () => {
    const { container } = render(<PipelineDiagram {...base} workerRunning />);

    expect(screen.getByText("idle")).toBeInTheDocument();
    expect(container.querySelector(".pipe-pulse")).toBeNull();
  });
});
