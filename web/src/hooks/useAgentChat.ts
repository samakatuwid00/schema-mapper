import { useCallback, useRef, useState } from "react";
import {
  getConversation,
  streamAgentChat,
  type AgentMessage,
  type AgentStreamEvent,
  type AgentToolCall,
  type AutonomyTier,
} from "../api/client";
import { errMsg } from "../utils";

export interface UseAgentChat {
  messages: AgentMessage[];
  conversationId: string | null;
  tier: AutonomyTier;
  streaming: boolean;
  error: string | null;
  pendingConfirm: AgentToolCall | null;
  send: (message: string, context?: Record<string, unknown>) => Promise<void>;
  confirmTool: (call: AgentToolCall, context?: Record<string, unknown>) => Promise<void>;
  cancelTool: () => void;
  setTier: (tier: AutonomyTier) => void;
  loadConversation: (id: string) => Promise<void>;
  reset: () => void;
}

/**
 * Chat state + SSE streaming for the agent sidebar: sends messages with the
 * caller-supplied page context, renders token events progressively, surfaces
 * confirmation prompts, and supports resuming persisted conversations.
 */
export function useAgentChat(): UseAgentChat {
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [tier, setTier] = useState<AutonomyTier>("propose_only");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingConfirm, setPendingConfirm] = useState<AgentToolCall | null>(null);
  // conversation id inside one stream (the state update is async)
  const liveConversation = useRef<string | null>(null);

  const run = useCallback(
    async (payload: {
      message: string;
      context?: Record<string, unknown>;
      confirm?: { tool: string; params: Record<string, unknown> } | null;
    }) => {
      setError(null);
      setStreaming(true);
      setPendingConfirm(null);
      setMessages((previous) => [
        ...previous,
        { role: "user", content: payload.message },
        { role: "assistant", content: "" },
      ]);

      const applyToDraft = (patch: (draft: AgentMessage) => AgentMessage) => {
        setMessages((previous) => {
          const next = [...previous];
          next[next.length - 1] = patch(next[next.length - 1]);
          return next;
        });
      };

      const onEvent = (event: AgentStreamEvent) => {
        if (event.event === "conversation") {
          const id = String(event.data.conversation_id ?? "");
          liveConversation.current = id;
          setConversationId(id);
        } else if (event.event === "token") {
          applyToDraft((draft) => ({
            ...draft,
            content: draft.content + String(event.data.text ?? ""),
          }));
        } else if (event.event === "tool_call") {
          const call = event.data as unknown as AgentToolCall;
          applyToDraft((draft) => ({
            ...draft,
            tool_calls: [...(draft.tool_calls ?? []), call],
          }));
          if (call.requires_confirmation) setPendingConfirm(call);
        } else if (event.event === "tool_result") {
          applyToDraft((draft) => ({
            ...draft,
            tool_results: [...(draft.tool_results ?? []), event.data.result],
          }));
        } else if (event.event === "error") {
          setError(String(event.data.detail ?? "agent error"));
        } else if (event.event === "done") {
          applyToDraft((draft) => ({
            ...draft,
            content: String(event.data.content ?? draft.content),
          }));
        }
      };

      try {
        await streamAgentChat(
          {
            message: payload.message,
            conversation_id: liveConversation.current,
            context: payload.context,
            autonomy_tier: tier,
            confirm: payload.confirm ?? null,
          },
          onEvent,
        );
      } catch (exc: unknown) {
        setError(errMsg(exc));
      } finally {
        setStreaming(false);
      }
    },
    [tier],
  );

  const send = useCallback(
    (message: string, context?: Record<string, unknown>) =>
      run({ message, context }),
    [run],
  );

  const confirmTool = useCallback(
    (call: AgentToolCall, context?: Record<string, unknown>) =>
      run({
        message: `Confirmed: run ${call.tool}.`,
        context,
        confirm: { tool: call.tool, params: call.params },
      }),
    [run],
  );

  const cancelTool = useCallback(() => setPendingConfirm(null), []);

  const loadConversation = useCallback(async (id: string) => {
    const detail = await getConversation(id);
    liveConversation.current = detail.id;
    setConversationId(detail.id);
    setTier(detail.autonomy_tier);
    setMessages(detail.messages);
    setPendingConfirm(null);
    setError(null);
  }, []);

  const reset = useCallback(() => {
    liveConversation.current = null;
    setConversationId(null);
    setMessages([]);
    setPendingConfirm(null);
    setError(null);
  }, []);

  return { messages, conversationId, tier, streaming, error, pendingConfirm,
           send, confirmTool, cancelTool, setTier, loadConversation, reset };
}
