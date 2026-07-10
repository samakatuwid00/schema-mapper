/**
 * Status chip with a disciplined color language: every semantic color carries
 * exactly ONE meaning. The important fix vs. the old map is that
 * `accepted` / `resolved` / `reviewed` read as informational (cyan), NOT as
 * "data is flowing" (green) — a reviewed proposal has not delivered anything.
 *
 * Colors are keyed to the `--st-*` semantic tokens in styles.css:
 *   flowing (green)  data delivered / healthy / terminal success
 *   info    (cyan)   reviewed / accepted / resolved — acknowledged, not flowing
 *   waiting (amber)  queued, not started
 *   active  (blue)   in progress
 *   retry   (orange) will be attempted again
 *   blocked (purple) held back for a human
 *   failed  (red)    gave up / rejected / breaking
 *   idle    (gray)   paused / disabled / unknown
 */
type Semantic =
  | "flowing"
  | "info"
  | "waiting"
  | "active"
  | "retry"
  | "blocked"
  | "failed"
  | "idle";

const SEMANTIC_BY_STATUS: Record<string, Semantic> = {
  // flowing — good / terminal success (genuinely all "delivered/healthy")
  delivered: "flowing",
  succeeded: "flowing",
  deployed: "flowing",
  applied: "flowing",
  enabled: "flowing",
  ok: "flowing",
  success: "flowing",

  // info — acknowledged / reviewed, but nothing has flowed
  accepted: "info",
  resolved: "info",
  reviewed: "info",

  // waiting — queued, not started
  pending: "waiting",
  queued: "waiting",

  // active — in progress
  processing: "active",
  running: "active",
  proposed: "active",
  deploying: "active",

  // retry — will be attempted again
  retry: "retry",

  // blocked — held back for a human
  quarantined: "blocked",

  // failed — gave up / rejected / breaking
  dead_letter: "failed",
  failed: "failed",
  error: "failed",
  breaking: "failed",
  rejected: "failed",

  // idle — paused / disabled / unknown
  paused: "idle",
  disabled: "idle",
  cancelled: "idle",
  discovered: "idle",
  unknown: "idle",
};

export interface StatusChipProps {
  status: string | null | undefined;
  /** Optional label override; defaults to the status text. */
  label?: string;
  /** Tooltip text; defaults to the raw status for screen inspection. */
  title?: string;
  /** Render a small leading dot in addition to the text label. */
  dot?: boolean;
}

export default function StatusChip({ status, label, title, dot }: StatusChipProps) {
  const key = (status ?? "unknown").toLowerCase();
  const semantic = SEMANTIC_BY_STATUS[key] ?? "idle";
  const text = label ?? status ?? "unknown";
  return (
    <span
      className={`chip chip-${semantic}`}
      title={title ?? key}
      data-status={key}
    >
      {dot ? <span className="chip-dot" aria-hidden="true" /> : null}
      {text}
    </span>
  );
}
