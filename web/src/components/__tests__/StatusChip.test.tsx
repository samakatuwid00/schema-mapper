import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import StatusChip from "../StatusChip";

describe("StatusChip", () => {
  const cases: Array<[string, string]> = [
    ["pending", "chip-amber"],
    ["processing", "chip-blue"],
    ["delivered", "chip-green"],
    ["succeeded", "chip-green"],
    ["deployed", "chip-green"],
    ["retry", "chip-orange"],
    ["quarantined", "chip-purple"],
    ["dead_letter", "chip-red"],
    ["failed", "chip-red"],
    ["paused", "chip-gray"],
    ["disabled", "chip-gray"],
  ];

  it.each(cases)("renders %s with the %s color class", (status, expectedClass) => {
    render(<StatusChip status={status} />);
    const chip = screen.getByText(status);
    expect(chip).toHaveClass("chip");
    expect(chip).toHaveClass(expectedClass);
  });

  it("falls back to gray for unknown statuses", () => {
    render(<StatusChip status="mystery_state" />);
    expect(screen.getByText("mystery_state")).toHaveClass("chip-gray");
  });

  it("renders a label override while keeping the status color", () => {
    render(<StatusChip status="running" label="worker running" />);
    const chip = screen.getByText("worker running");
    expect(chip).toHaveClass("chip-blue");
    expect(chip).toHaveAttribute("data-status", "running");
  });
});
