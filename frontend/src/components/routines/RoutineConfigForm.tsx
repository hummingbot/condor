import { useQuery } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import { useEffect, useState } from "react";

import type { RoutineFieldInfo } from "@/lib/api";
import { api } from "@/lib/api";
import { humanizeFieldName } from "@/lib/routineFields";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { useServer } from "@/hooks/useServer";

interface Props {
  fields: Record<string, RoutineFieldInfo>;
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
}

function SelectField({
  field,
  value,
  onChange,
}: {
  field: RoutineFieldInfo;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const { server } = useServer();
  const { data, isLoading } = useQuery({
    queryKey: ["routine-field-options", field.options_from, server],
    queryFn: () => api.getRoutineFieldOptions(field.options_from!, server!),
    enabled: !!field.options_from && !!server,
    staleTime: 30_000,
  });

  const options = data?.options ?? [];

  // Auto-select first option ONLY when no value is set. A non-empty persisted
  // value is preserved even if it's absent from the currently loaded options
  // (e.g. server/pair temporarily unavailable, server-scoped options).
  useEffect(() => {
    if (options.length === 0) return;
    if (value != null && value !== "") return;
    onChange(options[0]);
    // onChange is an unstable inline closure from the parent; depending on it
    // would re-run this effect every parent render. The auto-select trigger
    // only depends on options/value, so we intentionally omit it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options, value]);

  return (
    <div className="relative">
      <select
        value={String(value ?? field.default ?? "")}
        onChange={(e) => onChange(e.target.value)}
        className="w-full appearance-none rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 pr-8 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
      >
        {isLoading && <option value="">Loading...</option>}
        {!isLoading && options.length === 0 && (
          <option value="">No options available</option>
        )}
        {options.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-muted)]" />
    </div>
  );
}

function NumberField({
  fieldType,
  value,
  onChange,
}: {
  fieldType: string;
  value: unknown;
  onChange: (v: number) => void;
}) {
  const [draft, setDraft] = useState(String(value ?? ""));

  // Sync from parent when value changes externally
  useEffect(() => {
    setDraft(String(value ?? ""));
  }, [value]);

  return (
    <input
      type="number"
      step={fieldType === "float" ? "any" : "1"}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        const v = fieldType === "int" ? parseInt(draft) : parseFloat(draft);
        if (!isNaN(v)) onChange(v);
        else setDraft(String(value ?? ""));
      }}
      className="w-full rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
    />
  );
}

/**
 * The form a routine's `Config` turns into.
 *
 * Labelled by the field's name, with its description as help text underneath
 * (UX review): the description used to *be* the label, so a single field could
 * carry a whole sentence above its input, centred and squeezed into a third of
 * the width. One left-aligned column now, and a field with a fixed set of
 * values is a segmented control rather than a box to type the right word into.
 */
export function RoutineConfigForm({ fields, values, onChange }: Props) {
  return (
    <div className="flex flex-col gap-4 text-left">
      {Object.entries(fields).map(([key, field]) => {
        // `get_fields` falls back to the name when there is no description,
        // which would only repeat the label.
        const help = field.description && field.description !== key ? field.description : "";
        const current = values[key] ?? field.default ?? "";
        return (
          <div key={key}>
            <label className="mb-1 block text-xs font-medium text-[var(--color-text)]">
              {humanizeFieldName(key)}
            </label>
            {field.choices && field.choices.length > 0 ? (
              <SegmentedControl
                size="sm"
                aria-label={humanizeFieldName(key)}
                value={String(current)}
                onChange={(v) => {
                  const picked = field.choices!.find((c) => String(c) === v);
                  onChange(key, picked ?? v);
                }}
                options={field.choices.map((c) => ({ value: String(c), label: String(c) }))}
              />
            ) : field.widget === "select" && field.options_from ? (
              <SelectField
                field={field}
                value={values[key]}
                onChange={(v) => onChange(key, v)}
              />
            ) : field.type === "bool" ? (
              <button
                type="button"
                role="switch"
                aria-checked={!!values[key]}
                onClick={() => onChange(key, !values[key])}
                className={`rounded px-3 py-1.5 text-xs font-medium transition-colors ${
                  values[key]
                    ? "bg-[var(--color-primary-fill)]/20 text-[var(--color-primary)]"
                    : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
                }`}
              >
                {values[key] ? "ON" : "OFF"}
              </button>
            ) : field.type === "int" || field.type === "float" ? (
              <NumberField
                fieldType={field.type}
                value={current}
                onChange={(v) => onChange(key, v)}
              />
            ) : (
              <input
                type="text"
                value={String(current)}
                onChange={(e) => onChange(key, e.target.value)}
                className="w-full rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
              />
            )}
            {help && (
              <div className="mt-1 space-y-0.5 text-[11px] leading-snug text-[var(--color-text-muted)]">
                {/* "a = … | b = …" is how several routines document their
                    modes; one line each reads far better than one run-on. */}
                {help.split(" | ").map((line, i) => (
                  <p key={i}>{line}</p>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
