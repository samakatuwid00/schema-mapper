import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgentSidebar, { pageContextFor } from "../AgentSidebar";

vi.mock("../../api/client", () => ({
  listConversations: vi.fn(),
  getConversation: vi.fn(),
  deleteConversation: vi.fn(),
  streamAgentChat: vi.fn(),
}));
vi.mock("../../hooks/useAgentChat", () => ({ useAgentChat: vi.fn() }));

import * as api from "../../api/client";
import { useAgentChat } from "../../hooks/useAgentChat";

function chatState(overrides: Record<string, unknown> = {}) {
  return {
    messages: [], conversationId: null, tier: "propose_only",
    streaming: false, error: null, pendingConfirm: null,
    send: vi.fn().mockResolvedValue(undefined),
    confirmTool: vi.fn().mockResolvedValue(undefined),
    cancelTool: vi.fn(), setTier: vi.fn(),
    loadConversation: vi.fn().mockResolvedValue(undefined),
    reset: vi.fn(),
    ...overrides,
  };
}

function renderSidebar(state = chatState(), route = "/", open = true) {
  vi.mocked(useAgentChat).mockReturnValue(state as never);
  return render(
    <MemoryRouter initialEntries={[route]}>
      <AgentSidebar open={open} onClose={() => {}} />
    </MemoryRouter>,
  );
}

describe("AgentSidebar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listConversations).mockResolvedValue([] as never);
  });

  it("renders nothing when closed (closed by default)", () => {
    renderSidebar(chatState(), "/", false);
    expect(screen.queryByLabelText("Migration assistant")).toBeNull();
  });

  it("shows a bullet-list empty-state hint before any messages", () => {
    renderSidebar();
    expect(screen.getByText("status")).toBeInTheDocument();
    expect(screen.getByText("proposals")).toBeInTheDocument();
    expect(screen.getByText("blockers")).toBeInTheDocument();
    expect(screen.getByText("schemas")).toBeInTheDocument();
    expect(screen.getByText(/onboard/)).toBeInTheDocument();
  });

  it("sends the message with page-aware context from the route", () => {
    const state = chatState();
    renderSidebar(state, "/mappings/42");
    fireEvent.change(screen.getByLabelText("Message the assistant"), {
      target: { value: "why is this blocked?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(state.send).toHaveBeenCalledWith("why is this blocked?", {
      page: "/mappings/42",
      context: { proposal_id: 42 },
    });
  });

  it("derives plain page context off non-entity routes", () => {
    expect(pageContextFor("/schema")).toEqual({ page: "/schema" });
    expect(pageContextFor("/mappings/7")).toEqual({
      page: "/mappings/7", context: { proposal_id: 7 } });
  });

  it("offers exactly the two MVP autonomy tiers (no auto_all)", () => {
    renderSidebar();
    const options = screen.getByLabelText("Autonomy tier")
      .querySelectorAll("option");
    expect([...options].map((o) => o.getAttribute("value"))).toEqual(
      ["propose_only", "auto_safe"]);
  });

  it("renders streaming messages and forwards confirmation to the hook", () => {
    const call = { tool: "onboard_table", params: { source_table: "authors" },
                   requires_confirmation: true };
    const state = chatState({
      messages: [
        { role: "user", content: "onboard authors" },
        { role: "assistant", content: "Confirm to proceed.",
          tool_calls: [call] },
      ],
      pendingConfirm: call,
    });
    renderSidebar(state, "/tables");
    fireEvent.click(screen.getByRole("button", { name: /approve/i }));
    expect(state.confirmTool).toHaveBeenCalledWith(call, { page: "/tables" });
  });

  it("lists history, switches conversation, deletes with confirm", async () => {
    vi.mocked(api.listConversations).mockResolvedValue([
      { id: "c1", title: "What's blocking?", autonomy_tier: "propose_only",
        created_at: "t", updated_at: "t", message_count: 2 },
    ] as never);
    vi.mocked(api.deleteConversation).mockResolvedValue({ ok: true } as never);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const state = chatState();
    renderSidebar(state);

    fireEvent.click(screen.getByRole("button", { name: "Conversation history" }));
    const item = await screen.findByRole("button", { name: "What's blocking?" });
    fireEvent.click(item);
    expect(state.loadConversation).toHaveBeenCalledWith("c1");

    fireEvent.click(screen.getByRole("button", { name: "Conversation history" }));
    fireEvent.click(await screen.findByRole("button",
                                            { name: /delete what's blocking/i }));
    await waitFor(() => expect(api.deleteConversation).toHaveBeenCalledWith("c1"));
  });

  it("starts a fresh chat from the new-chat button", () => {
    const state = chatState();
    renderSidebar(state);
    fireEvent.click(screen.getByRole("button", { name: "New chat" }));
    expect(state.reset).toHaveBeenCalled();
  });

  it("surfaces stream errors", () => {
    renderSidebar(chatState({ error: "quota exhausted" }));
    expect(screen.getByText("quota exhausted")).toBeInTheDocument();
  });

  it("expands to full screen and back, preserving the draft", () => {
    renderSidebar(chatState(), "/tables");
    const draftInput = screen.getByLabelText("Message the assistant");
    fireEvent.change(draftInput, { target: { value: "still drafting" } });

    fireEvent.click(screen.getByRole("button", { name: "Full screen" }));
    expect(screen.getByLabelText("Migration assistant"))
      .toHaveClass("agent-panel--fullscreen");
    expect(screen.getByLabelText("Message the assistant"))
      .toHaveValue("still drafting");

    fireEvent.click(screen.getByRole("button", { name: "Exit full screen" }));
    expect(screen.getByLabelText("Migration assistant"))
      .not.toHaveClass("agent-panel--fullscreen");
    expect(screen.getByLabelText("Message the assistant"))
      .toHaveValue("still drafting");
  });

  it("collapses full screen on Escape", () => {
    renderSidebar(chatState());
    fireEvent.click(screen.getByRole("button", { name: "Full screen" }));
    expect(screen.getByLabelText("Migration assistant"))
      .toHaveClass("agent-panel--fullscreen");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByLabelText("Migration assistant"))
      .not.toHaveClass("agent-panel--fullscreen");
  });

  it("preserves messages across a full-screen toggle while streaming", () => {
    const state = chatState({
      streaming: true,
      messages: [
        { role: "user", content: "status?" },
        { role: "assistant", content: "checking" },
      ],
    });
    renderSidebar(state);
    fireEvent.click(screen.getByRole("button", { name: "Full screen" }));
    expect(screen.getByText("status?")).toBeInTheDocument();
    expect(screen.getByText("checking")).toBeInTheDocument();
  });

  it("opens the slash-command menu and filters as the user types", () => {
    renderSidebar();
    const input = screen.getByLabelText("Message the assistant");
    fireEvent.change(input, { target: { value: "/" } });
    const menu = screen.getByRole("listbox", { name: "Command suggestions" });
    expect(menu).toBeInTheDocument();
    // filter narrows the list to matching commands
    fireEvent.change(input, { target: { value: "/inspect" } });
    const options = within(
      screen.getByRole("listbox", { name: "Command suggestions" }),
    ).getAllByRole("option");
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveTextContent("inspect job <job_id>");
  });

  it("hides the menu when the draft does not start with a slash", () => {
    renderSidebar();
    const input = screen.getByLabelText("Message the assistant");
    fireEvent.change(input, { target: { value: "check status" } });
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("inserts the picked command instead of sending it", () => {
    const state = chatState();
    renderSidebar(state);
    const input = screen.getByLabelText("Message the assistant");
    fireEvent.change(input, { target: { value: "/inspect" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(input).toHaveValue("inspect job <job_id>");
    expect(state.send).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("navigates the menu with arrow keys before inserting", () => {
    renderSidebar();
    const input = screen.getByLabelText("Message the assistant");
    fireEvent.change(input, { target: { value: "/" } });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    // second command in the palette is "check status"
    expect(input).toHaveValue("check status");
  });

  it("Escape closes the menu without inserting", () => {
    renderSidebar();
    const input = screen.getByLabelText("Message the assistant");
    fireEvent.change(input, { target: { value: "/onboard" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(input).toHaveValue("/onboard");
  });
});
