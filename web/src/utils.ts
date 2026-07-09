/** Extract a human-readable message from an unknown error (ApiError, Error, anything). */
export function errMsg(err: unknown): string {
  if (err && typeof err === "object") {
    const e = err as { detail?: unknown; message?: unknown };
    if (typeof e.detail === "string" && e.detail) return e.detail;
    if (typeof e.message === "string" && e.message) return e.message;
  }
  return String(err);
}

export function errStatus(err: unknown): number | undefined {
  if (err && typeof err === "object" && "status" in err) {
    const s = (err as { status: unknown }).status;
    if (typeof s === "number") return s;
  }
  return undefined;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** Rough "how long ago" formatter: "42s", "3m", "2h 05m", "3d 4h". */
export function fmtAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  let sec = Math.max(0, Math.floor((Date.now() - then) / 1000));
  const days = Math.floor(sec / 86400);
  sec -= days * 86400;
  const hours = Math.floor(sec / 3600);
  sec -= hours * 3600;
  const mins = Math.floor(sec / 60);
  sec -= mins * 60;
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${String(mins).padStart(2, "0")}m`;
  if (mins > 0) return `${mins}m`;
  return `${sec}s`;
}

/** Truncate a schema fingerprint for display. */
export function shortFp(fp: string | null | undefined, length = 14): string {
  if (!fp) return "—";
  return fp.length <= length ? fp : `${fp.slice(0, length)}…`;
}

export function prettyJson(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
