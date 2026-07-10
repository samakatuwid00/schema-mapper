/**
 * Zero-dependency inline SVG sparkline. Hand-rolled to keep web/ free of a
 * charting runtime at this data volume.
 */
interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
  stroke?: string;
  fill?: string;
  ariaLabel?: string;
}

export default function Sparkline({
  data,
  width = 120,
  height = 32,
  stroke = "var(--accent)",
  fill = "color-mix(in srgb, var(--accent) 18%, transparent)",
  ariaLabel,
}: SparklineProps) {
  if (data.length === 0) {
    return <svg className="sparkline" width={width} height={height} aria-hidden="true" />;
  }
  const max = Math.max(...data, 1);
  const min = Math.min(...data, 0);
  const span = max - min || 1;
  const stepX = data.length > 1 ? width / (data.length - 1) : width;
  const pad = 2;
  const usable = height - pad * 2;

  const points = data.map((v, i) => {
    const x = i * stepX;
    const y = pad + usable - ((v - min) / span) * usable;
    return [x, y] as const;
  });

  const line = points.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${line} L${width},${height} L0,${height} Z`;
  const [lastX, lastY] = points[points.length - 1];

  return (
    <svg
      className="sparkline"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={ariaLabel ?? `trend, latest ${data[data.length - 1]}`}
    >
      <path d={area} fill={fill} stroke="none" />
      <path d={line} fill="none" stroke={stroke} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={lastX} cy={lastY} r="2" fill={stroke} />
    </svg>
  );
}
