import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import { useAuthedImage } from "@/hooks/useAuthedImage";
import type { RoutineInstance } from "@/lib/api";

interface Props {
  instance: RoutineInstance;
}

interface KpiSection {
  type: "kpi";
  label: string;
  value: string;
  delta?: string | null;
  trend?: string;
}

/** The routine's chart, off a bearer-guarded route. The fetching lives in the
 *  hook; what belongs here is the chart's own shape — a reserved 2:1 box while
 *  the render is in flight, so the content below does not shift. */
function AuthImage({ src, alt, className }: { src: string; alt: string; className?: string }) {
  const image = useAuthedImage(src);

  if (image.status === "loading") {
    return (
      <div
        className={`aspect-[2/1] animate-pulse bg-[var(--color-border)]/30 ${className ?? ""}`}
      />
    );
  }
  // Failed fetch: collapse, only after the request settles
  if (!image.src) return null;
  return (
    <img
      src={image.src}
      alt={alt}
      className={`aspect-[2/1] object-contain ${className ?? ""}`}
    />
  );
}

function KpiBar({ kpis }: { kpis: KpiSection[] }) {
  return (
    <div className="flex gap-3 flex-wrap mb-3">
      {kpis.map((k, i) => (
        <div
          key={i}
          className="flex-1 min-w-[120px] rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3"
        >
          <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
            {k.label}
          </div>
          <div className="text-lg font-bold text-[var(--color-text)] mt-0.5">
            {k.value}
          </div>
          {k.delta && (
            <div
              className={`text-xs mt-0.5 ${
                k.trend === "up"
                  ? "text-[var(--color-green)]"
                  : k.trend === "down"
                    ? "text-[var(--color-red)]"
                    : "text-[var(--color-text-muted)]"
              }`}
            >
              {k.delta}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export function RoutineResultView({ instance }: Props) {
  const [rawOpen, setRawOpen] = useState(false);

  if (!instance.has_result && !instance.result_text) {
    return null;
  }

  // Extract KPI sections
  const kpis = (instance.sections ?? []).filter(
    (s) => s.type === "kpi"
  ) as unknown as KpiSection[];

  const hasRichContent = instance.has_chart || (instance.table_data && instance.table_data.length > 0) || kpis.length > 0;

  return (
    <div className="space-y-3">
      {kpis.length > 0 && <KpiBar kpis={kpis} />}

      {instance.has_chart && (
        <div className="overflow-hidden rounded-lg border border-[var(--color-border)]">
          <AuthImage
            src={`/api/v1/routines/instances/${instance.instance_id}/image`}
            alt="Chart"
            className="w-full"
          />
        </div>
      )}

      {instance.table_data && instance.table_data.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-[var(--color-border)]">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-[var(--color-border)] bg-[var(--color-surface-hover)]">
                {(instance.table_columns || Object.keys(instance.table_data[0])).map((col) => (
                  <th key={col} className="px-3 py-2 text-left font-medium text-[var(--color-text-muted)] uppercase tracking-wider">
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {instance.table_data.map((row, i) => (
                <tr key={i} className="border-b border-[var(--color-border)]/50 last:border-0">
                  {(instance.table_columns || Object.keys(row)).map((col) => {
                    const val = row[col];
                    const isNum = typeof val === "number";
                    const numColor = isNum && val > 0
                      ? "text-[var(--color-green)]"
                      : isNum && val < 0
                        ? "text-[var(--color-red)]"
                        : "";
                    return (
                      <td key={col} className={`px-3 py-1.5 font-mono ${numColor}`}>
                        {isNum ? (val as number).toFixed(val % 1 === 0 ? 0 : 2) : String(val ?? "")}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {instance.result_text && (
        hasRichContent ? (
          <div className="rounded-lg border border-[var(--color-border)]">
            <button
              onClick={() => setRawOpen(!rawOpen)}
              className="flex w-full items-center gap-1.5 px-3 py-2 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
            >
              {rawOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
              Raw Output
            </button>
            {rawOpen && (
              <pre className="max-h-60 overflow-auto border-t border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-xs text-[var(--color-text)] font-mono whitespace-pre-wrap">
                {instance.result_text}
              </pre>
            )}
          </div>
        ) : (
          <pre className="max-h-80 overflow-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-xs text-[var(--color-text)] font-mono whitespace-pre-wrap">
            {instance.result_text}
          </pre>
        )
      )}
    </div>
  );
}
