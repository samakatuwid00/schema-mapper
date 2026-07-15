import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ChatMessage from "../ChatMessage";

describe("ChatMessage", () => {
  it("renders user and assistant text", () => {
    render(<ChatMessage message={{ role: "user", content: "hello there" }} />);
    expect(screen.getByText("hello there")).toBeInTheDocument();
    expect(document.querySelector('[data-role="user"]')).toBeTruthy();
  });

  it("shows a compact tool indicator while streaming", () => {
    render(
      <ChatMessage
        streaming
        message={{ role: "assistant", content: "",
                   tool_calls: [{ tool: "check_status", params: {},
                                  requires_confirmation: false }] }}
      />,
    );
    expect(screen.getByTestId("tool-chip")).toHaveTextContent(
      "check_status (running…)");
  });

  it("renders inline approve/cancel for gated tool calls", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    const call = { tool: "onboard_table", params: { source_table: "authors" },
                   requires_confirmation: true };
    render(
      <ChatMessage
        message={{ role: "assistant", content: "Confirm to proceed.",
                   tool_calls: [call] }}
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /approve/i }));
    expect(onConfirm).toHaveBeenCalledWith(call);
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("shows no confirmation controls without a handler", () => {
    render(
      <ChatMessage
        message={{ role: "assistant", content: "done",
                   tool_calls: [{ tool: "x", params: {},
                                  requires_confirmation: true }] }}
      />,
    );
    expect(screen.queryByRole("button", { name: /approve/i })).toBeNull();
  });

  it("renders settled assistant markdown as formatted elements", () => {
    render(
      <ChatMessage
        message={{ role: "assistant",
                   content: "## Status\n\n- one\n- two\n\n`inline code`" }}
      />,
    );
    expect(screen.getByRole("heading", { name: "Status" })).toBeInTheDocument();
    expect(screen.getByText("one").closest("li")).toBeTruthy();
    expect(screen.getByText("inline code").tagName).toBe("CODE");
  });

  it("renders raw HTML in assistant content as inert text, not markup", () => {
    render(
      <ChatMessage
        message={{ role: "assistant",
                   content: '<img src=x onerror="window.__pwned=true">' }}
      />,
    );
    expect(document.querySelector("img")).toBeNull();
    expect((window as unknown as { __pwned?: boolean }).__pwned).toBeUndefined();
  });

  it("keeps the streaming tail as plain text, not markdown", () => {
    render(
      <ChatMessage
        streaming
        message={{ role: "assistant", content: "## still typing" }}
      />,
    );
    expect(screen.queryByRole("heading")).toBeNull();
    expect(screen.getByText(/## still typing/)).toBeInTheDocument();
  });
});
