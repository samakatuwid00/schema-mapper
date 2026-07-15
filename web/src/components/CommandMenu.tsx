import type { AgentCommand } from "../agentCommands";

export interface CommandMenuProps {
  commands: AgentCommand[];
  activeIndex: number;
  onSelect: (command: AgentCommand) => void;
  onHover: (index: number) => void;
}

/**
 * Slash-command suggestion list shown above the composer. Purely presentational:
 * keyboard navigation (arrows / enter / escape) lives in AgentSidebar so it can
 * share the input's key handler. Uses onMouseDown so a click fires before the
 * input loses focus.
 */
export default function CommandMenu({ commands, activeIndex, onSelect,
                                     onHover }: CommandMenuProps) {
  if (commands.length === 0) return null;
  return (
    <ul className="agent-cmd-menu" role="listbox" aria-label="Command suggestions">
      {commands.map((command, index) => (
        <li
          key={command.template}
          role="option"
          aria-selected={index === activeIndex}
          className={`agent-cmd-item${index === activeIndex ? " active" : ""}`}
          onMouseDown={(event) => {
            event.preventDefault();
            onSelect(command);
          }}
          onMouseEnter={() => onHover(index)}
        >
          <span className="agent-cmd-template mono">{command.template}</span>
          <span className="agent-cmd-hint dim">{command.hint}</span>
        </li>
      ))}
    </ul>
  );
}
