import { useMemo } from "react";

interface Props {
  /** PnL values over time (already sorted chronologically) */
  values: number[];
  width?: number;
  height?: number;
}

export function PnlSparkline({ values, width = 80, height = 24 }: Props) {
  const path = useMemo(() => {
    if (values.length < 2) return null;

    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;
    const padding = 1;
    const innerH = height - padding * 2;
    const stepX = (width - 2) / (values.length - 1);

    const points = values.map((v, i) => {
      const x = 1 + i * stepX;
      const y = padding + innerH - ((v - min) / range) * innerH;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });

    return `M${points.join("L")}`;
  }, [values, width, height]);

  if (!path) return <div style={{ width, height }} />;

  const lastVal = values[values.length - 1];
  const color = lastVal >= 0 ? "var(--color-green)" : "var(--color-red)";

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
      <path d={path} fill="none" stroke={color} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
