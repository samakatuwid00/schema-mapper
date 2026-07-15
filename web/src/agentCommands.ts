/**
 * Slash-command palette for the assistant composer. Typing "/" in the box
 * opens a suggestion list so operators pick a command instead of guessing the
 * phrasing. Each `template` is inserted verbatim into the composer; angle-bracket
 * placeholders (`<job_id>`) are auto-selected so the user types the value next.
 *
 * Every template maps to a phrasing the intent classifier already recognizes
 * (src/agent/conversation.py `_INTENT_PATTERNS`) — keep them in sync.
 */
export interface AgentCommand {
  /** Text inserted into the composer. */
  template: string;
  /** Terse, caveman-style description of what it does. */
  hint: string;
  /** Extra fuzzy-match terms (space separated). */
  keywords?: string;
}

export const AGENT_COMMANDS: AgentCommand[] = [
  { template: "--help", hint: "command list", keywords: "help ?" },
  { template: "check status", hint: "integration health, queue, outbox", keywords: "status pending" },
  { template: "show schema", hint: "source + target tables", keywords: "tables structure" },
  { template: "summarize proposal <proposal_id>", hint: "accepted mappings + review ids", keywords: "summary review" },
  { template: "explain blocker for proposal <proposal_id>", hint: "why deploy stuck", keywords: "block stuck" },
  { template: "deploy guidance for proposal <proposal_id>", hint: "ready to ship?", keywords: "deploy ship" },
  { template: "onboard <source_table>", hint: "create proposal. deploys nothing", keywords: "add table map" },
  { template: "inspect job <job_id>", hint: "worker status, failures, repair handles", keywords: "job worker queue" },
  { template: "plan_refresh_failure_repair for job <job_id>", hint: "build repair checklist", keywords: "refresh fix failed" },
  { template: "diagnose_entity_delivery <entity>", hint: "deployed status + target row counts", keywords: "no data paused empty" },
  { template: "explain deploy error <error text>", hint: "parse missing required mappings", keywords: "deploy error failed" },
  { template: "diagnose duplicate key for <entity>: <error text>", hint: "safe to repair?", keywords: "duplicate unique exists" },
  { template: "repair duplicate key for <entity>", hint: "gated crosswalk repair", keywords: "fix duplicate crosswalk" },
  { template: "add mapping <source_column> to <target_table>.<target_column> proposal <proposal_id>", hint: "manual mapping", keywords: "map column" },
  { template: "reopen mapping review <review_id>", hint: "bad mapping back to Review Queue", keywords: "reopen review send back" },
  { template: "reject mapping review <review_id>", hint: "reject one bad mapping row", keywords: "reject review remove" },
  { template: "list drift reports", hint: "schema drift", keywords: "drift" },
  { template: "resolve drift", hint: "re-map + re-deliver (gated)", keywords: "fix drift apply" },
  { template: "schema swap", hint: "preview target swap", keywords: "swap target" },
  { template: "where are we", hint: "workflow status", keywords: "step progress" },
];

/** Placeholder token (`<job_id>`) matcher — first hit is auto-selected on insert. */
export const PLACEHOLDER_RE = /<[^>]+>/;

/** Case-insensitive substring filter across template, hint, and keywords. */
export function filterCommands(query: string): AgentCommand[] {
  const q = query.trim().toLowerCase();
  if (!q) return AGENT_COMMANDS;
  return AGENT_COMMANDS.filter((cmd) =>
    `${cmd.template} ${cmd.hint} ${cmd.keywords ?? ""}`.toLowerCase().includes(q),
  );
}
