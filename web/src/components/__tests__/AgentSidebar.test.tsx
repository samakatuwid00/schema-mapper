import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgentSidebar, { pageContextFor } from "../AgentSidebar";

vi.mock("../../api/client", () => ({
  listConversations: vi.fn(),
  getConversation: vi.fn(),
  deleteConversation: vi.fn(),
  bulkDeleteConversations: vi.fn(),
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

function renderSidebar(state = chatState(), route = "/", open = true,
                       extraProps: Record<string, unknown> = {}) {
  vi.mocked(useAgentChat).mockReturnValue(state as never);
  return render(
    <MemoryRouter initialEntries={[route]}>
      <AgentSidebar open={open} onClose={() => {}} {...extraProps} />
    </MemoryRouter>,
  );
}

describe("AgentSidebar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listConversations).mockResolvedValue([] as never);
  });

  it("stays mounted but hidden and non-interactive when closed (design D7 slide)", () => {
    renderSidebar(chatState(), "/", false);
    const panel = screen.getByLabelText("Migration assistant");
    expect(panel).toHaveAttribute("aria-hidden", "true");
    expect(panel).not.toHaveClass("agent-panel--open");
  });

  it("marks the panel open and not aria-hidden once opened", () => {
    renderSidebar();
    const panel = screen.getByLabelText("Migration assistant");
    expect(panel).toHaveAttribute("aria-hidden", "false");
    expect(panel).toHaveClass("agent-panel--open");
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

  it("offers exactly the two MVP autonomy tiers with friendly labels (no auto_all)", () => {
    renderSidebar();
    const options = within(
      screen.getByRole("radiogroup", { name: "Autonomy tier" }),
    ).getAllByRole("radio");
    expect(options.map((o) => o.textContent)).toEqual(["Ask first", "Auto safe"]);
  });

  it("defaults to Ask first (propose_only) checked, and explains the mode", () => {
    renderSidebar(chatState({ tier: "propose_only" }));
    const group = screen.getByRole("radiogroup", { name: "Autonomy tier" });
    expect(within(group).getByRole("radio", { name: "Ask first" }))
      .toHaveAttribute("aria-checked", "true");
    expect(within(group).getByRole("radio", { name: "Auto safe" }))
      .toHaveAttribute("aria-checked", "false");
    expect(screen.getByText(/always asks for your approval first/))
      .toBeInTheDocument();
    expect(screen.getByText("propose_only")).toBeInTheDocument();
  });

  it("switching to Auto safe persists the backend enum value and updates help text", () => {
    const state = chatState({ tier: "propose_only" });
    renderSidebar(state);
    fireEvent.click(screen.getByRole("radio", { name: "Auto safe" }));
    expect(state.setTier).toHaveBeenCalledWith("auto_safe");
  });

  it("shows Auto-safe mode's destructive-confirmation caveat when selected", () => {
    renderSidebar(chatState({ tier: "auto_safe" }));
    expect(screen.getByText(/Destructive actions still require your approval/))
      .toBeInTheDocument();
    expect(screen.getByText("auto_safe")).toBeInTheDocument();
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

  it("searches history and shows an empty state for no matches", async () => {
    vi.mocked(api.listConversations).mockImplementation((q) =>
      Promise.resolve((!q ? [
        { id: "c1", title: "What's blocking?", autonomy_tier: "propose_only",
          created_at: "t", updated_at: "t", message_count: 2 },
      ] : []) as never));
    renderSidebar();

    fireEvent.click(screen.getByRole("button", { name: "Conversation history" }));
    await screen.findByRole("button", { name: "What's blocking?" });

    fireEvent.change(screen.getByLabelText("Search conversation history"),
                     { target: { value: "nothing matches this" } });
    await waitFor(() => expect(api.listConversations)
      .toHaveBeenCalledWith("nothing matches this"));
    expect(await screen.findByText("No conversations match your search."))
      .toBeInTheDocument();
  });

  it("bulk-deletes selected conversations after a count-based confirm", async () => {
    vi.mocked(api.listConversations).mockResolvedValue([
      { id: "c1", title: "First", autonomy_tier: "propose_only",
        created_at: "t", updated_at: "t", message_count: 2 },
      { id: "c2", title: "Second", autonomy_tier: "propose_only",
        created_at: "t", updated_at: "t", message_count: 2 },
    ] as never);
    vi.mocked(api.bulkDeleteConversations).mockResolvedValue({ deleted: 2 } as never);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderSidebar();

    fireEvent.click(screen.getByRole("button", { name: "Conversation history" }));
    await screen.findByRole("button", { name: "First" });
    fireEvent.click(screen.getByLabelText("Select First"));
    fireEvent.click(screen.getByLabelText("Select Second"));

    expect(screen.getByText("2 selected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /delete selected/i }));

    expect(confirmSpy).toHaveBeenCalledWith("Delete 2 selected conversations?");
    await waitFor(() => expect(api.bulkDeleteConversations)
      .toHaveBeenCalledWith(["c1", "c2"]));
  });

  it("starts a fresh conversation when the loaded chat is bulk-deleted", async () => {
    vi.mocked(api.listConversations).mockResolvedValue([
      { id: "c1", title: "Loaded", autonomy_tier: "propose_only",
        created_at: "t", updated_at: "t", message_count: 2 },
    ] as never);
    vi.mocked(api.bulkDeleteConversations).mockResolvedValue({ deleted: 1 } as never);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const state = chatState({ conversationId: "c1" });
    renderSidebar(state);

    fireEvent.click(screen.getByRole("button", { name: "Conversation history" }));
    await screen.findByRole("button", { name: "Loaded" });
    fireEvent.click(screen.getByLabelText("Select Loaded"));
    fireEvent.click(screen.getByRole("button", { name: /delete selected/i }));

    await waitFor(() => expect(state.reset).toHaveBeenCalled());
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

  const PINNED_JOB = {
    job_id: 42, job_type: "deploy_lrmis", status: "failed",
    error_message: "mapping cannot be deployed", proposal_id: 582,
  };

  it("shows a pinned job card and prefills a repair draft", () => {
    renderSidebar(chatState(), "/", true, { pinnedJob: PINNED_JOB });
    expect(screen.getByText("#42 deploy_lrmis")).toBeInTheDocument();
    expect(screen.getByText("mapping cannot be deployed")).toBeInTheDocument();
    expect(screen.getByText("Open proposal 582").closest("a"))
      .toHaveAttribute("href", "/mappings/582");
    expect(screen.getByLabelText("Message the assistant"))
      .toHaveValue("#42 deploy_lrmis failed mapping cannot be deployed");
  });

  it("does not clobber an in-progress draft when a job gets pinned", () => {
    const state = chatState();
    const { rerender } = renderSidebar(state, "/", true, { pinnedJob: null });
    fireEvent.change(screen.getByLabelText("Message the assistant"),
                     { target: { value: "already typing" } });
    rerender(
      <MemoryRouter>
        <AgentSidebar open onClose={() => {}} pinnedJob={PINNED_JOB} />
      </MemoryRouter>,
    );
    expect(screen.getByLabelText("Message the assistant"))
      .toHaveValue("already typing");
  });

  it("unpins the job via the dismiss control", () => {
    const onClear = vi.fn();
    renderSidebar(chatState(), "/", true,
                 { pinnedJob: PINNED_JOB, onClearPinnedJob: onClear });
    fireEvent.click(screen.getByLabelText("Unpin job"));
    expect(onClear).toHaveBeenCalled();
  });

  it("keeps the pinned job card visible in full-screen mode", () => {
    renderSidebar(chatState(), "/", true, { pinnedJob: PINNED_JOB });
    fireEvent.click(screen.getByRole("button", { name: "Full screen" }));
    expect(screen.getByText("#42 deploy_lrmis")).toBeInTheDocument();
  });

  it("includes the pinned job id and proposal id in the sent context", () => {
    const state = chatState();
    renderSidebar(state, "/tables", true, { pinnedJob: PINNED_JOB });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(state.send).toHaveBeenCalledWith(
      "#42 deploy_lrmis failed mapping cannot be deployed",
      { page: "/tables", context: { job_id: 42, proposal_id: 582 } },
    );
  });
});
