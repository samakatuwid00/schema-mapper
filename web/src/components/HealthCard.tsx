import type { ReactNode } from "react";
import { Link } from "react-router-dom";

export interface HealthCardProps {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "amber" | "blue" | "green" | "orange" | "purple" | "red" | "gray" | "cyan";
  /** If set, the whole card becomes a link. */
  to?: string;
}

export default function HealthCard({ label, value, sub, tone = "cyan", to }: HealthCardProps) {
  const body = (
    <div className={`health-card health-${tone}`}>
      <div className="health-label">{label}</div>
      <div className="health-value">{value}</div>
      {sub !== undefined && <div className="health-sub">{sub}</div>}
    </div>
  );
  if (to) {
    return (
      <Link to={to} className="health-card-link">
        {body}
      </Link>
    );
  }
  return body;
}
