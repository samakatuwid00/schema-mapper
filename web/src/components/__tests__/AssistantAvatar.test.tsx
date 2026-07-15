import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import AssistantAvatar from "../AssistantAvatar";

describe("AssistantAvatar", () => {
  it("shows the idle state by default with no active class", () => {
    render(<AssistantAvatar />);
    const avatar = screen.getByRole("img", { name: "Assistant is idle" });
    expect(avatar).toHaveClass("agent-avatar--idle");
    expect(avatar).not.toHaveClass("agent-avatar--active");
  });

  it("switches to the active state, distinguishable without relying on motion", () => {
    render(<AssistantAvatar active />);
    const avatar = screen.getByRole("img", { name: "Assistant is thinking" });
    expect(avatar).toHaveClass("agent-avatar--active");
    expect(avatar).not.toHaveClass("agent-avatar--idle");
  });
});
