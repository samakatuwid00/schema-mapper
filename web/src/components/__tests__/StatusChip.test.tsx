import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import StatusChip from "../StatusChip";

describe("StatusChip", () => {
  const cases: Array<[string, string]> = [
    ["pending", "chip-waiting"],
    ["queued", "chip-waiting"],
    ["processing", "chip-active"],
    ["running", "chip-active"],
    ["delivered", "chip-flowing"],
    ["succeeded", "chip-flowing"],
    ["deployed", "chip-flowing"],
    ["applied", "chip-flowing"],
    ["retry", "chip-retry"],
    ["quarantined", "chip-blocked"],
    ["dead_letter", "chip-failed"],
    ["failed", "chip-failed"],
    ["rejected", "chip-failed"],
    ["accepted", "chip-info"],
    ["resolved", "chip-info"],
    ["reviewed", "chip-info"],
    ["paused", "chip-idle"],
    ["disabled", "chip-idle"],
  ];

  it.each(cases)("renders %s with its text label and the %s color class", (status, expectedClass) => {
    render(<StatusChip status={status} />);
    // Every chip must render its text label, never color alone.
    const chip = screen.getByText(status);
    expect(chip).toHaveClass("chip");
    expect(chip).toHaveClass(expectedClass);
  });

  it("falls back to idle for unknown statuses", () => {
    render(<StatusChip status="mystery_state" />);
    expect(screen.getByText("mystery_state")).toHaveClass("chip-idle");
  });

  it("renders a label override while keeping the status color", () => {
    render(<StatusChip status="running" label="worker running" />);
    const chip = screen.getByText("worker running");
    expect(chip).toHaveClass("chip-active");
    expect(chip).toHaveAttribute("data-status", "running");
  });

  it("does NOT color accepted/resolved/reviewed the same as delivered", () => {
    render(
      <>
        <StatusChip status="delivered" />
        <StatusChip status="accepted" />
        <StatusChip status="resolved" />
        <StatusChip status="reviewed" />
      </>,
    );
    const flowingClass = screen.getByText("delivered").className;
    for (const reviewed of ["accepted", "resolved", "reviewed"]) {
      const chip = screen.getByText(reviewed);
      // reviewed/acknowledged states must read as info (cyan), not flowing (green)
      expect(chip).toHaveClass("chip-info");
      expect(chip).not.toHaveClass("chip-flowing");
      expect(chip.className).not.toEqual(flowingClass);
    }
  });

  it("can render an optional leading dot", () => {
    const { container } = render(<StatusChip status="running" dot />);
    expect(container.querySelector(".chip-dot")).not.toBeNull();
    // text label still present alongside the dot
    expect(screen.getByText("running")).toHaveClass("chip");
  });
});
