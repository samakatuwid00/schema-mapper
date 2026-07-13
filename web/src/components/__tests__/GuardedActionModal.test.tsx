import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import GuardedActionModal from "../GuardedActionModal";

describe("GuardedActionModal", () => {
  it("one-click tier: fires immediately with no modal", () => {
    const onConfirm = vi.fn();
    const onClose = vi.fn();
    render(
      <GuardedActionModal
        open
        tier="one-click"
        title="Replay event"
        onConfirm={onConfirm}
        onClose={onClose}
      />,
    );
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith("");
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("confirm tier: requires a non-empty reason before enabling", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(
      <GuardedActionModal
        open
        tier="confirm"
        title="Stop worker"
        actionLabel="Stop worker"
        onConfirm={onConfirm}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    const confirmBtn = screen.getByRole("button", { name: "Stop worker" });
    expect(confirmBtn).toBeDisabled();

    // whitespace-only reason does not count
    await user.type(screen.getByLabelText("Reason"), "   ");
    expect(confirmBtn).toBeDisabled();

    await user.clear(screen.getByLabelText("Reason"));
    await user.type(screen.getByLabelText("Reason"), "maintenance window");
    expect(confirmBtn).toBeEnabled();

    await user.click(confirmBtn);
    // confirm tier has no typed-confirmation input, so the second arg is empty.
    expect(onConfirm).toHaveBeenCalledWith("maintenance window", "");
  });

  it("typed tier: requires reason AND exact confirmation string, and shows the warning panel", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(
      <GuardedActionModal
        open
        tier="typed"
        title="Apply migration"
        actionLabel="Apply migration"
        confirmString="004_add_index.sql"
        warning={<p>This will run 42 statements against the target DB.</p>}
        onConfirm={onConfirm}
        onClose={vi.fn()}
      />,
    );

    // Warning / preview panel is displayed.
    expect(screen.getByTestId("warning-panel")).toHaveTextContent(
      "This will run 42 statements against the target DB.",
    );

    const confirmBtn = screen.getByRole("button", { name: "Apply migration" });
    expect(confirmBtn).toBeDisabled();

    // Reason alone is not enough.
    await user.type(screen.getByLabelText("Reason"), "hotfix index");
    expect(confirmBtn).toBeDisabled();

    // Wrong confirmation string keeps it disabled.
    const confirmInput = screen.getByLabelText("Confirmation text");
    await user.type(confirmInput, "004_add_index");
    expect(confirmBtn).toBeDisabled();

    // Exact string enables the button.
    await user.clear(confirmInput);
    await user.type(confirmInput, "004_add_index.sql");
    expect(confirmBtn).toBeEnabled();

    await user.click(confirmBtn);
    // typed tier passes both the reason and the exact confirmation string.
    expect(onConfirm).toHaveBeenCalledWith("hotfix index", "004_add_index.sql");
  });

  it("renders nothing when closed", () => {
    render(
      <GuardedActionModal
        open={false}
        tier="typed"
        title="Hidden"
        confirmString="x"
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
