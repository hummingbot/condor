import type { RoutineFieldInfo } from "@/lib/api";

interface Props {
  fields: Record<string, RoutineFieldInfo>;
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
}

export function RoutineConfigForm({ fields, values, onChange }: Props) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {Object.entries(fields).map(([key, field]) => (
        <div key={key}>
          <label className="mb-1 block text-xs font-medium text-[var(--color-text-muted)]">
            {field.description || key}
          </label>
          {field.type === "bool" ? (
            <button
              type="button"
              onClick={() => onChange(key, !values[key])}
              className={`rounded px-3 py-1.5 text-xs font-medium transition-colors ${
                values[key]
                  ? "bg-[var(--color-primary)]/20 text-[var(--color-primary)]"
                  : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
              }`}
            >
              {values[key] ? "ON" : "OFF"}
            </button>
          ) : field.type === "int" || field.type === "float" ? (
            <input
              type="number"
              step={field.type === "float" ? "any" : "1"}
              value={String(values[key] ?? field.default ?? "")}
              onChange={(e) => {
                const v = field.type === "int" ? parseInt(e.target.value) : parseFloat(e.target.value);
                if (!isNaN(v)) onChange(key, v);
              }}
              className="w-full rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
            />
          ) : (
            <input
              type="text"
              value={String(values[key] ?? field.default ?? "")}
              onChange={(e) => onChange(key, e.target.value)}
              className="w-full rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
            />
          )}
        </div>
      ))}
    </div>
  );
}
