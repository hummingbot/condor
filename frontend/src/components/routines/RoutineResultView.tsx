import { useEffect, useState } from "react";
import type { RoutineInstance } from "@/lib/api";

interface Props {
  instance: RoutineInstance;
}

function AuthImage({ src, alt, className }: { src: string; alt: string; className?: string }) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);

  useEffect(() => {
    let revoke: string | null = null;
    const token = localStorage.getItem("condor_token");
    fetch(src, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((res) => (res.ok ? res.blob() : Promise.reject()))
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        revoke = url;
        setBlobUrl(url);
      })
      .catch(() => setBlobUrl(null));
    return () => {
      if (revoke) URL.revokeObjectURL(revoke);
    };
  }, [src]);

  if (!blobUrl) return null;
  return <img src={blobUrl} alt={alt} className={className} />;
}

export function RoutineResultView({ instance }: Props) {
  if (!instance.has_result && !instance.result_text) {
    return null;
  }

  return (
    <div className="space-y-3">
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
        <pre className="max-h-80 overflow-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-xs text-[var(--color-text)] font-mono whitespace-pre-wrap">
          {instance.result_text}
        </pre>
      )}
    </div>
  );
}
