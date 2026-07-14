import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { History, Plus, SendHorizonal, Trash2, X } from "lucide-react";
import {
  deleteConversation,
  listConversations,
  type AutonomyTier,
  type ConversationSummary,
} from "../api/client";
import { useAgentChat } from "../hooks/useAgentChat";
import { errMsg } from "../utils";
import ChatMessage from "./ChatMessage";

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
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && typeof el.scrollTo === "function") {
      el.scrollTo({ top: el.scrollHeight });
    }
  }, [chat.messages]);

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
    void chat.send(message, context);
  };

  return (
    <aside
      aria-label="Migration assistant"
      style={{ position: "fixed", top: 0, right: 0, bottom: 0, width: "22rem",
               display: "flex", flexDirection: "column", zIndex: 40,
               background: "var(--panel-bg, #16181d)",
               borderLeft: "1px solid var(--border, #333)" }}
    >
      <div className="panel-header"
           style={{ padding: "0.6rem 0.8rem", display: "flex",
                    alignItems: "center", gap: "0.4rem" }}>
        <strong style={{ flex: 1 }}>Migration assistant</strong>
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
                aria-label="Close assistant" onClick={onClose}>
          <X size={14} />
        </button>
      </div>

      <label className="field" style={{ padding: "0 0.8rem" }}>
        <span className="field-label dim">Autonomy</span>
        <select className="input" aria-label="Autonomy tier" value={chat.tier}
                onChange={(e) => chat.setTier(e.target.value as AutonomyTier)}>
          <option value="propose_only">Propose only (default)</option>
          <option value="auto_safe">Auto-apply safe reads</option>
        </select>
      </label>

      {historyOpen && (
        <div style={{ padding: "0.4rem 0.8rem", overflowY: "auto",
                      maxHeight: "10rem",
                      borderBottom: "1px solid var(--border, #333)" }}>
          {historyError && <div className="form-error">{historyError}</div>}
          {history.length === 0 ? (
            <span className="dim">No previous conversations.</span>
          ) : (
            history.map((conversation) => (
              <div key={conversation.id}
                   style={{ display: "flex", gap: "0.3rem",
                            alignItems: "center" }}>
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

      <div ref={scrollRef}
           style={{ flex: 1, overflowY: "auto", padding: "0.5rem 0.8rem" }}>
        {chat.messages.length === 0 && (
          <p className="dim" style={{ fontSize: "0.85rem" }}>
            I'm the migration assistant — ask about status, proposals, blockers,
            schemas, or say "onboard &lt;table&gt;". Actions that change anything
            always ask for your approval.
          </p>
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

      <div style={{ display: "flex", gap: "0.4rem", padding: "0.6rem 0.8rem",
                    borderTop: "1px solid var(--border, #333)" }}>
        <input
          className="input"
          style={{ flex: 1 }}
          aria-label="Message the assistant"
          placeholder={chat.streaming ? "Thinking…" : "Ask the assistant…"}
          value={draft}
          disabled={chat.streaming}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
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
