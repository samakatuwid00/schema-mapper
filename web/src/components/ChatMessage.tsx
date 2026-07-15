import { Check, Cog, User, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Link } from "react-router-dom";
import type { AgentMessage, AgentToolCall } from "../api/client";
import AssistantAvatar from "./AssistantAvatar";

export interface ChatMessageProps {
  message: AgentMessage;
  streaming?: boolean;
  /** Present only while this message's confirmation is still actionable. */
  onConfirm?: (call: AgentToolCall) => void;
  onCancel?: () => void;
  /** Runs a structured `gated_repair` action chip (design D3). */
  onRunAction?: (call: AgentToolCall) => void;
}

interface RepairAction {
  type: "open_proposal" | "gated_repair" | string;
  proposal_id?: number;
  tool?: string;
  params?: Record<string, unknown>;
  label?: string;
}

/** Structured repair-action chips a tool result may carry (design D3):
 * scans every `tool_results` entry for an `actions` array. */
function repairActionsFrom(results?: unknown[]): RepairAction[] {
  const actions: RepairAction[] = [];
  for (const result of results ?? []) {
    const list = (result as { actions?: unknown } | null)?.actions;
    if (Array.isArray(list)) actions.push(...(list as RepairAction[]));
  }
  return actions;
}

/**
 * One chat message: avatar, text, compact tool-call indicators, and inline
 * Approve/Cancel controls when the agent proposed a gated action. Assistant
 * text renders as sanitized markdown once settled; the live streaming tail
 * stays plain text so partial markdown never flickers or breaks.
 */
export default function ChatMessage({ message, streaming = false,
                                      onConfirm, onCancel,
                                      onRunAction }: ChatMessageProps) {
  const isUser = message.role === "user";
  const pending = (message.tool_calls ?? []).find(
    (call) => call.requires_confirmation);
  const renderMarkdown = !isUser && !streaming;
  const repairActions = renderMarkdown
    ? repairActionsFrom(message.tool_results) : [];

  return (
    <div
      data-role={message.role}
      className={`agent-msg ${isUser ? "agent-msg--user" : "agent-msg--assistant"}`}
    >
      <span aria-hidden="true" style={{ marginTop: 2 }}>
        {isUser ? <User size={14} className="dim" /> : <AssistantAvatar active={streaming} />}
      </span>
      <div className="agent-msg-body">
        {(message.tool_calls ?? []).map((call, index) => (
          <div key={index} className="mono agent-tool-chip" data-testid="tool-chip">
            <Cog size={12} aria-hidden="true" />
            {call.tool}
            {call.requires_confirmation
              ? " (needs approval)"
              : call.executed === false
                ? " (failed)"
                : streaming
                  ? " (running…)"
                  : " (done)"}
          </div>
        ))}

        {renderMarkdown ? (
          <div className="agent-markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
          </div>
        ) : (
          <div className="agent-msg-plain">
            {message.content}
            {streaming && !isUser ? <span className="dim">▌</span> : null}
          </div>
        )}

        {repairActions.length > 0 && (
          <div className="agent-action-chips">
            {repairActions.map((action, index) =>
              action.type === "open_proposal" && action.proposal_id ? (
                <Link key={index} className="btn btn-ghost btn-xs agent-action-chip"
                      to={`/mappings/${action.proposal_id}`}>
                  Open proposal {action.proposal_id}
                </Link>
              ) : action.type === "gated_repair" && action.tool ? (
                <button key={index} type="button"
                        className="btn btn-primary btn-xs agent-action-chip"
                        onClick={() => onRunAction?.({
                          tool: action.tool!, params: action.params ?? {},
                          requires_confirmation: true,
                        })}>
                  <Check size={12} aria-hidden="true" />
                  {action.label ?? `Run ${action.tool}`}
                </button>
              ) : null,
            )}
          </div>
        )}

        {pending && onConfirm && (
          <div className="row-actions" style={{ marginTop: "0.5rem" }}>
            <button type="button" className="btn btn-primary btn-sm"
                    onClick={() => onConfirm(pending)}>
              <Check size={13} aria-hidden="true" /> Approve
            </button>
            <button type="button" className="btn btn-sm"
                    onClick={() => onCancel?.()}>
              <X size={13} aria-hidden="true" /> Cancel
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
