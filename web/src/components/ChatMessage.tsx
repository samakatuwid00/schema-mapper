import { Check, Cog, User, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { AgentMessage, AgentToolCall } from "../api/client";
import AssistantAvatar from "./AssistantAvatar";

export interface ChatMessageProps {
  message: AgentMessage;
  streaming?: boolean;
  /** Present only while this message's confirmation is still actionable. */
  onConfirm?: (call: AgentToolCall) => void;
  onCancel?: () => void;
}

/**
 * One chat message: avatar, text, compact tool-call indicators, and inline
 * Approve/Cancel controls when the agent proposed a gated action. Assistant
 * text renders as sanitized markdown once settled; the live streaming tail
 * stays plain text so partial markdown never flickers or breaks.
 */
export default function ChatMessage({ message, streaming = false,
                                      onConfirm, onCancel }: ChatMessageProps) {
  const isUser = message.role === "user";
  const pending = (message.tool_calls ?? []).find(
    (call) => call.requires_confirmation);
  const renderMarkdown = !isUser && !streaming;

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
