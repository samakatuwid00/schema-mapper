import { Bot, Check, Cog, User, X } from "lucide-react";
import type { AgentMessage, AgentToolCall } from "../api/client";

export interface ChatMessageProps {
  message: AgentMessage;
  streaming?: boolean;
  /** Present only while this message's confirmation is still actionable. */
  onConfirm?: (call: AgentToolCall) => void;
  onCancel?: () => void;
}

/**
 * One chat message: role icon, text, compact tool-call indicators, and inline
 * Approve/Cancel controls when the agent proposed a gated action.
 */
export default function ChatMessage({ message, streaming = false,
                                      onConfirm, onCancel }: ChatMessageProps) {
  const isUser = message.role === "user";
  const pending = (message.tool_calls ?? []).find(
    (call) => call.requires_confirmation);

  return (
    <div
      data-role={message.role}
      style={{ display: "flex", gap: "0.5rem", alignItems: "flex-start",
               margin: "0.5rem 0" }}
    >
      <span className="dim" aria-hidden="true" style={{ marginTop: 2 }}>
        {isUser ? <User size={14} /> : <Bot size={14} />}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        {(message.tool_calls ?? []).map((call, index) => (
          <div key={index} className="mono dim" data-testid="tool-chip"
               style={{ fontSize: "0.75rem", display: "flex", gap: "0.3rem",
                        alignItems: "center" }}>
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

        <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
          {message.content}
          {streaming && !isUser ? <span className="dim">▌</span> : null}
        </div>

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
