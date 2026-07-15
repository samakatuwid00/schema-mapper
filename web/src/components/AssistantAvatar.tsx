export interface AssistantAvatarProps {
  active?: boolean;
}

/** Small animated presence: idle breathing pulse, active "thinking" spin. Motion-safe. */
export default function AssistantAvatar({ active = false }: AssistantAvatarProps) {
  return (
    <span
      className={`agent-avatar ${active ? "agent-avatar--active" : "agent-avatar--idle"}`}
      role="img"
      aria-label={active ? "Assistant is thinking" : "Assistant is idle"}
    >
      <span className="agent-avatar-ring" aria-hidden="true" />
      <span className="agent-avatar-core" aria-hidden="true" />
    </span>
  );
}
