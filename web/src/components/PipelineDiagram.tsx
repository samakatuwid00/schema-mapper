/**
 * The signature Overview visual: source -> sync queue -> staging, with pulses
 * whose motion tracks real throughput and whose color reflects queue health.
 * Pure SVG/CSS; honors prefers-reduced-motion via the global guard in styles.css.
 */
import { Database, HardDrive, Inbox } from "lucide-react";

interface PipelineDiagramProps {
  pending: number;
  retry: number;
  blocked: number; // quarantined + dead_letter
  delivered: number;
  workerRunning: boolean;
  queueProcessing: boolean;
  deliveryProcessing: boolean;
}

type Health = "processing" | "flowing" | "waiting" | "blocked" | "idle";

function health(pending: number, retry: number, blocked: number, processing: boolean): Health {
  if (processing) return "processing";
  if (blocked > 0) return "blocked";
  if (retry > 0 || pending > 25) return "waiting";
  if (pending > 0) return "flowing";
  return "idle";
}

const TONE: Record<Health, string> = {
  processing: "var(--st-active)",
  flowing: "var(--st-flowing)",
  waiting: "var(--st-waiting)",
  blocked: "var(--st-blocked)",
  idle: "var(--st-idle)",
};

function Segment({ tone, active }: { tone: string; active: boolean }) {
  return (
    <div className="pipe-segment" aria-hidden="true">
      <span className="pipe-rail" style={{ background: `color-mix(in srgb, ${tone} 30%, transparent)` }} />
      {active && (
        <>
          <span className="pipe-pulse" style={{ background: tone }} />
          <span className="pipe-pulse pipe-pulse-2" style={{ background: tone }} />
        </>
      )}
    </div>
  );
}

function Node({
  icon,
  title,
  sub,
  tone,
  active = false,
}: {
  icon: React.ReactNode;
  title: string;
  sub: string;
  tone?: string;
  active?: boolean;
}) {
  return (
    <div className={`pipe-node${active ? " pipe-node-active" : ""}`} style={tone ? { borderColor: tone } : undefined}>
      <div className="pipe-node-icon" style={tone ? { color: tone } : undefined}>
        {icon}
      </div>
      <div className="pipe-node-title mono">{title}</div>
      <div className="pipe-node-sub dim">{sub}</div>
    </div>
  );
}

export default function PipelineDiagram({
  pending,
  retry,
  blocked,
  delivered,
  workerRunning,
  queueProcessing,
  deliveryProcessing,
}: PipelineDiagramProps) {
  const inFlight = pending + retry;
  const workerProcessing = workerRunning && inFlight > 0;
  const rightActive = deliveryProcessing || workerProcessing;
  const leftActive = queueProcessing || rightActive;
  const processing = leftActive || rightActive;
  const state = health(pending, retry, blocked, processing);
  const tone = TONE[state];
  const stateLabel = state === "processing"
    ? rightActive ? "delivering" : "preparing data"
    : state === "flowing"
      ? "flowing"
      : state === "waiting"
        ? "backing up"
        : state;

  return (
    <section className="panel pipeline-panel">
      <div className="panel-header">
        <h3 className="panel-title">Delivery pipeline</h3>
        <span className={`chip chip-${state === "processing" ? "active" : state === "flowing" ? "flowing" : state === "idle" ? "idle" : state === "blocked" ? "blocked" : "waiting"}`}>
          <span className="chip-dot" />
          {stateLabel}
        </span>
      </div>
      <div className="pipeline" role="img" aria-label={`Delivery pipeline is ${stateLabel}. ${inFlight} rows in the queue, ${blocked} blocked.`}>
        <Node
          icon={<Database size={22} />}
          title="IRIMSV"
          sub={queueProcessing ? "reading source" : "source"}
          tone={queueProcessing ? tone : undefined}
          active={queueProcessing}
        />
        <Segment tone={tone} active={leftActive} />
        <Node
          icon={<Inbox size={22} />}
          title="Sync queue"
          sub={`${inFlight} waiting${rightActive ? " · processing" : ""}${blocked ? ` · ${blocked} blocked` : ""}`}
          tone={inFlight > 0 || blocked > 0 ? tone : undefined}
          active={processing}
        />
        <Segment tone={rightActive ? "var(--st-active)" : tone} active={rightActive} />
        <Node
          icon={<HardDrive size={22} />}
          title="LRMIS"
          sub={`${delivered} delivered${rightActive ? " · receiving" : ""}`}
          tone={rightActive ? "var(--st-active)" : undefined}
          active={rightActive}
        />
      </div>
      {!workerRunning && inFlight > 0 && (
        <p className="dim pipeline-hint">
          Rows are queued but the delivery worker is stopped — start it in Sync Queue.
        </p>
      )}
    </section>
  );
}
