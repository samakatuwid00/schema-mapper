import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { History, Maximize2, Minimize2, Plus, SendHorizonal, Trash2, X } from "lucide-react";
import {
  bulkDeleteConversations,
  deleteConversation,
  listConversations,
  type AutonomyTier,
  type ConversationSummary,
} from "../api/client";
import type { PinnedJobContext } from "../api/types";
import { useAgentChat } from "../hooks/useAgentChat";
import { errMsg } from "../utils";
import { filterCommands, PLACEHOLDER_RE, type AgentCommand } from "../agentCommands";
import AssistantAvatar from "./AssistantAvatar";
import ChatMessage from "./ChatMessage";
import CommandMenu from "./CommandMenu";

export interface AgentSidebarProps {
  open: boolean;
  onClose: () => void;
  /** A failed job pinned from "Repair with Assistant" (job-orchestration §2). */
  pinnedJob?: PinnedJobContext | null;
  onClearPinnedJob?: () => void;
}

/** The pasted-job-message shape the backend's deploy-job-repair router
 * recognizes (`_looks_like_pasted_deploy_job`), so pinning a job and sending
 * the prefilled draft resolves the same repair context as pasting it by hand.
 * Space-joined (not newline-joined): the composer is a single-line `<input>`,
 * which silently drops literal newlines from its displayed value. */
function repairPromptFor(job: PinnedJobContext): string {
  const parts = [`#${job.job_id} ${job.job_type} failed`];
  if (job.error_message) parts.push(job.error_message.replace(/\s+/g, " ").trim());
  return parts.join(" ");
}

/** Labeled autonomy choices (design D5): friendlier labels over the same
 * backend enum values — `auto_all` is never offered here. */
const AUTONOMY_OPTIONS: { value: AutonomyTier; label: string; help: string }[] = [
  { value: "propose_only", label: "Ask first",
    help: "Read-only answers run immediately. Any change always asks for your approval first." },
  { value: "auto_safe", label: "Auto safe",
    help: "Safe, high-confidence read-only actions may run automatically. Destructive actions still require your approval." },
];

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
export default function AgentSidebar({ open, onClose, pinnedJob,
                                      onClearPinnedJob }: AgentSidebarProps) {
  const chat = useAgentChat();
  const location = useLocation();
  const [draft, setDraft] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<ConversationSummary[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyQuery, setHistoryQuery] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
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

  // Prefill (never clobber an in-progress draft) when a job gets pinned.
  useEffect(() => {
    if (pinnedJob) setDraft((prev) => prev || repairPromptFor(pinnedJob));
  }, [pinnedJob]);

  useEffect(() => {
    if (!fullscreen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setFullscreen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [fullscreen]);

  const refreshHistory = (query = historyQuery) => {
    listConversations(query || undefined)
      .then(setHistory)
      .catch((exc: unknown) => setHistoryError(errMsg(exc)));
  };

  useEffect(() => {
    if (open && historyOpen) refreshHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, historyOpen]);

  // Debounce the search-as-you-type refetch (design D4: API-backed search).
  useEffect(() => {
    if (!(open && historyOpen)) return;
    const timer = window.setTimeout(() => refreshHistory(historyQuery), 250);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [historyQuery]);

  const toggleSelected = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const bulkDeleteSelected = () => {
    const ids = [...selectedIds];
    if (ids.length === 0) return;
    if (!window.confirm(
      `Delete ${ids.length} selected conversation${ids.length === 1 ? "" : "s"}?`))
      return;
    void bulkDeleteConversations(ids).then(() => {
      if (chat.conversationId && ids.includes(chat.conversationId)) chat.reset();
      setSelectedIds(new Set());
      refreshHistory();
    }).catch((exc: unknown) => setHistoryError(errMsg(exc)));
  };

  const baseContext = pageContextFor(location.pathname);
  const context = pinnedJob
    ? { ...baseContext,
        context: { ...(baseContext.context as Record<string, unknown> | undefined),
                  job_id: pinnedJob.job_id, proposal_id: pinnedJob.proposal_id } }
    : baseContext;

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
      aria-hidden={!open}
      className={`agent-panel${open ? " agent-panel--open" : ""}${fullscreen ? " agent-panel--fullscreen" : ""}`}
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

      <div className="field" style={{ padding: "0 16px" }}>
        <span className="field-label dim">Autonomy</span>
        <div className="segmented-control" role="radiogroup" aria-label="Autonomy tier">
          {AUTONOMY_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={chat.tier === option.value}
              title={option.help}
              className={`segmented-control-option${chat.tier === option.value ? " active" : ""}`}
              onClick={() => chat.setTier(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <span className="dim agent-autonomy-help">
          {AUTONOMY_OPTIONS.find((option) => option.value === chat.tier)?.help}{" "}
          (<code className="mono">{chat.tier}</code>)
        </span>
      </div>

      {historyOpen && (
        <div className="agent-history">
          <input
            className="input input-sm"
            aria-label="Search conversation history"
            placeholder="Search history…"
            value={historyQuery}
            onChange={(e) => setHistoryQuery(e.target.value)}
          />
          {historyError && <div className="form-error">{historyError}</div>}
          {selectedIds.size > 0 && (
            <div className="agent-history-bulkbar">
              <span className="dim">{selectedIds.size} selected</span>
              <button type="button" className="btn btn-danger-outline btn-xs"
                      onClick={bulkDeleteSelected}>
                <Trash2 size={12} aria-hidden="true" /> Delete selected
              </button>
            </div>
          )}
          {history.length === 0 ? (
            <span className="dim">
              {historyQuery ? "No conversations match your search." : "No previous conversations."}
            </span>
          ) : (
            history.map((conversation) => (
              <div key={conversation.id} className="agent-history-row">
                <input
                  type="checkbox"
                  aria-label={`Select ${conversation.title || conversation.id}`}
                  checked={selectedIds.has(conversation.id)}
                  onChange={() => toggleSelected(conversation.id)}
                />
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
                              .then(() => {
                                if (chat.conversationId === conversation.id) chat.reset();
                                refreshHistory();
                              });
                          }
                        }}>
                  <Trash2 size={13} />
                </button>
              </div>
            ))
          )}
        </div>
      )}

      {pinnedJob && (
        <div className="agent-pinned-job">
          <div className="agent-pinned-job-top">
            <span className="mono">#{pinnedJob.job_id} {pinnedJob.job_type}</span>
            <span className="dim">{pinnedJob.status}</span>
            <button type="button" className="btn btn-ghost btn-xs"
                    aria-label="Unpin job" onClick={onClearPinnedJob}>
              <X size={12} />
            </button>
          </div>
          {pinnedJob.error_message && (
            <div className="agent-pinned-job-error mono">{pinnedJob.error_message}</div>
          )}
          {pinnedJob.proposal_id && (
            <Link className="btn btn-ghost btn-xs" to={`/mappings/${pinnedJob.proposal_id}`}>
              Open proposal {pinnedJob.proposal_id}
            </Link>
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
              onRunAction={(call) => void chat.confirmTool(call, context)}
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
