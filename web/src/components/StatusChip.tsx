const COLOR_BY_STATUS: Record<string, string> = {
  // queue / event lifecycle
  pending: "amber",
  queued: "amber",
  processing: "blue",
  running: "blue",
  delivered: "green",
  succeeded: "green",
  deployed: "green",
  success: "green",
  ok: "green",
  applied: "green",
  retry: "orange",
  quarantined: "purple",
  dead_letter: "red",
  failed: "red",
  error: "red",
  breaking: "red",
  paused: "gray",
  disabled: "gray",
  cancelled: "gray",
  resolved: "cyan",
  reviewed: "cyan",
  accepted: "green",
  rejected: "red",
  discovered: "gray",
  proposed: "blue",
  enabled: "green",
};

export interface StatusChipProps {
  status: string | null | undefined;
  /** Optional label override; defaults to the status text. */
  label?: string;
  title?: string;
}

export default function StatusChip({ status, label, title }: StatusChipProps) {
  const key = (status ?? "unknown").toLowerCase();
  const color = COLOR_BY_STATUS[key] ?? "gray";
  return (
    <span className={`chip chip-${color}`} title={title} data-status={key}>
      {label ?? (status ?? "unknown")}
    </span>
  );
}
