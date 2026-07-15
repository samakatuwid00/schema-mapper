import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { History, Maximize2, Minimize2, Plus, SendHorizonal, Trash2, X } from "lucide-react";
import {
  deleteConversation,
  listConversations,
  type AutonomyTier,
  type ConversationSummary,
} from "../api/client";
import { useAgentChat } from "../hooks/useAgentChat";
import { errMsg } from "../utils";
import { filterCommands, PLACEHOLDER_RE, type AgentCommand } from "../agentCommands";
import AssistantAvatar from "./AssistantAvatar";
import ChatMessage from "./ChatMessage";
import CommandMenu from "./CommandMenu";

export interface AgentSidebarProps {
  open: boolean;
  onClose: () => void;
}

/** Route → page-aware context (design D5): route + IDs only, never data. */
export function pageContextFor(pathname: string): Record<string, unknown> {
  const context: Record<string, unknown> = {};
  const mapping = pathname.match(/^\/mappings\/(\d+)/);
  if (mapping) context.proposal_id = Number(mapping[1]);
  return { page: pathname, ...(Object.keys(context).length ? { context } : {}) };
}

/**
 * Collapsible right-hand assistant panel, visible across all admin pages.
 * Sends the current route (+ proposal id when on a mapping page) with every
 * message; renders streamed responses progressively; gated actions get
 * inline Approve/Cancel. Only the two MVP autonomy tiers exist here.
 */
export default function AgentSidebar({ open, onClose }: AgentSidebarProps) {
  const chat = useAgentChat();
  const location = useLocation();
  const [draft, setDraft] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<ConversationSummary[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuIndex, setMenuIndex] = useState(0);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Slash-command palette: "/" as the first character opens the suggestion
  // list, filtered by whatever follows it.
  const menuCommands = useMemo(
    () => (menuOpen ? filterCommands(draft.slice(1)) : []),
    [menuOpen, draft],
  );

  useEffect(() => {
    const el = scrollRef.current;
    if (el && typeof el.scrollTo === "function") {
      el.scrollTo({ top: el.scrollHeight });
    }
  }, [chat.messages]);

  useEffect(() => {
    if (!open) setFullscreen(false);
  }, [open]);

  useEffect(() => {
    if (!fullscreen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setFullscreen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [fullscreen]);

  const refreshHistory = () => {
    listConversations()
      .then(setHistory)
      .catch((exc: unknown) => setHistoryError(errMsg(exc)));
  };

  useEffect(() => {
    if (open && historyOpen) refreshHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, historyOpen]);

  if (!open) return null;

  const context = pageContextFor(location.pathname);

  const submit = () => {
    const message = draft.trim();
    if (!message || chat.streaming) return;
    setDraft("");
    setMenuOpen(false);
    void chat.send(message, context);
  };

  const onDraftChange = (value: string) => {
    setDraft(value);
    setMenuOpen(value.startsWith("/"));
    setMenuIndex(0);
  };

  // Insert the picked command; select its first <placeholder> so the user types
  // straight over it. Runs after the controlled value re-renders.
  const applyCommand = (command: AgentCommand) => {
    setDraft(command.template);
    setMenuOpen(false);
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (!el) return;
      el.focus();
      const match = command.template.match(PLACEHOLDER_RE);
      if (match && match.index != null) {
        el.setSelectionRange(match.index, match.index + match[0].length);
      } else {
        el.setSelectionRange(command.template.length, command.template.length);
      }
    });
  };

  const onComposerKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (menuOpen && menuCommands.length > 0) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setMenuIndex((i) => (i + 1) % menuCommands.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setMenuIndex((i) => (i - 1 + menuCommands.length) % menuCommands.length);
        return;
      }
      if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        applyCommand(menuCommands[menuIndex]);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setMenuOpen(false);
        return;
      }
    }
    if (event.key === "Enter") submit();
  };

  return (
    <aside
      aria-label="Migration assistant"
      className={`agent-panel${fullscreen ? " agent-panel--fullscreen" : ""}`}
    >
      <div className="agent-header">
        <div className="agent-header-title">
          <AssistantAvatar active={chat.streaming} />
          <strong>Migration assistant</strong>
        </div>
        <button type="button" className="btn btn-ghost btn-sm" title="New chat"
                aria-label="New chat"
                onClick={() => { chat.reset(); setHistoryOpen(false); }}>
          <Plus size={14} />
        </button>
        <button type="button" className="btn btn-ghost btn-sm"
                title="Conversation history" aria-label="Conversation history"
                onClick={() => setHistoryOpen((v) => !v)}>
          <History size={14} />
        </button>
        <button type="button" className="btn btn-ghost btn-sm"
                title={fullscreen ? "Exit full screen" : "Full screen"}
                aria-label={fullscreen ? "Exit full screen" : "Full screen"}
                onClick={() => setFullscreen((v) => !v)}>
          {fullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
        </button>
        <button type="button" className="btn btn-ghost btn-sm"
                aria-label="Close assistant" onClick={onClose}>
          <X size={14} />
        </button>
      </div>

      <label className="field" style={{ padding: "0 16px" }}>
        <span className="field-label dim">Autonomy</span>
        <select className="input" aria-label="Autonomy tier" value={chat.tier}
                onChange={(e) => chat.setTier(e.target.value as AutonomyTier)}>
          <option value="propose_only">Propose only (default)</option>
          <option value="auto_safe">Auto-apply safe reads</option>
        </select>
      </label>

      {historyOpen && (
        <div className="agent-history">
          {historyError && <div className="form-error">{historyError}</div>}
          {history.length === 0 ? (
            <span className="dim">No previous conversations.</span>
          ) : (
            history.map((conversation) => (
              <div key={conversation.id} className="agent-history-row">
                <button type="button" className="btn btn-ghost btn-sm"
                        style={{ flex: 1, textAlign: "left", minWidth: 0,
                                 overflow: "hidden", textOverflow: "ellipsis" }}
                        onClick={() => {
                          void chat.loadConversation(conversation.id);
                          setHistoryOpen(false);
                        }}>
                  {conversation.title || "(untitled)"}
                </button>
                <button type="button" className="btn btn-ghost btn-sm"
                        aria-label={`Delete ${conversation.title || conversation.id}`}
                        onClick={() => {
                          if (window.confirm("Delete this conversation?")) {
                            void deleteConversation(conversation.id)
                              .then(refreshHistory);
                          }
                        }}>
                  <Trash2 size={13} />
                </button>
              </div>
            ))
          )}
        </div>
      )}

      <div ref={scrollRef} className="agent-messages">
        {chat.messages.length === 0 && (
          <div className="agent-empty">
            <p>Migration assistant. Ask:</p>
            <ul>
              <li>status</li>
              <li>proposals</li>
              <li>blockers</li>
              <li>schemas</li>
            </ul>
            <p>Say: "onboard &lt;table&gt;"</p>
            <p>Changes need your approval.</p>
          </div>
        )}
        {chat.messages.map((message, index) => {
          const last = index === chat.messages.length - 1;
          return (
            <ChatMessage
              key={index}
              message={message}
              streaming={last && chat.streaming}
              onConfirm={last && chat.pendingConfirm
                ? (call) => void chat.confirmTool(call, context)
                : undefined}
              onCancel={chat.cancelTool}
            />
          );
        })}
        {chat.error && <div className="alert alert-danger">{chat.error}</div>}
      </div>

      <div className="agent-composer">
        {menuOpen && (
          <CommandMenu
            commands={menuCommands}
            activeIndex={menuIndex}
            onSelect={applyCommand}
            onHover={setMenuIndex}
          />
        )}
        <input
          ref={inputRef}
          className="input"
          aria-label="Message the assistant"
          placeholder={chat.streaming ? "Thinking…" : "Ask the assistant… (/ for commands)"}
          value={draft}
          disabled={chat.streaming}
          onChange={(e) => onDraftChange(e.target.value)}
          onKeyDown={onComposerKeyDown}
        />
        <button type="button" className="btn btn-primary btn-sm"
                aria-label="Send" disabled={chat.streaming || !draft.trim()}
                onClick={submit}>
          <SendHorizonal size={14} />
        </button>
      </div>
    </aside>
  );
}
