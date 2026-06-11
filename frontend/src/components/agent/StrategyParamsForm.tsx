import { ChevronDown } from "lucide-react";
import { useEffect, useState } from "react";

import type { RoutineFieldInfo } from "@/lib/api";

function durationToTicks(hours: number, frequencySec: number): number {
  const freq = Math.max(1, frequencySec || 60);
  return Math.max(1, Math.round((hours * 3600) / freq));
}

interface Props {
  fields: Record<string, RoutineFieldInfo>;
  groups: string[];
  values: Record<string, unknown>;
  frequencySec: number;
  onChange: (key: string, value: unknown) => void;
}

function NumberField({
  fieldType,
  value,
  step,
  onChange,
}: {
  fieldType: string;
  value: unknown;
  step?: string;
  onChange: (v: number) => void;
}) {
  const [draft, setDraft] = useState(String(value ?? ""));

  useEffect(() => {
    setDraft(String(value ?? ""));
  }, [value]);

  return (
    <input
      type="number"
      step={step ?? (fieldType === "float" ? "any" : "1")}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        const v = fieldType === "int" ? parseInt(draft, 10) : parseFloat(draft);
        if (!isNaN(v)) onChange(v);
        else setDraft(String(value ?? ""));
      }}
      className="w-full rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
    />
  );
}

function FieldInput({
  field,
  value,
  onChange,
}: {
  field: RoutineFieldInfo;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  if (field.type === "bool") {
    return (
      <button
        type="button"
        onClick={() => onChange(!value)}
        className={`rounded px-3 py-1.5 text-xs font-medium transition-colors ${
          value
            ? "bg-[var(--color-primary)]/20 text-[var(--color-primary)]"
            : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
        }`}
      >
        {value ? "ON" : "OFF"}
      </button>
    );
  }

  if (field.type === "int" || field.type === "float") {
    return (
      <NumberField
        fieldType={field.type}
        value={value ?? ""}
        step={field.duration ? "0.25" : undefined}
        onChange={onChange}
      />
    );
  }

  return (
    <input
      type="text"
      value={value !== undefined && value !== null ? String(value) : ""}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
    />
  );
}

function DurationHint({
  hours,
  frequencySec,
  effectiveTickKey,
}: {
  hours: number;
  frequencySec: number;
  effectiveTickKey?: string;
}) {
  const freq = Math.max(1, frequencySec || 60);
  const ticks = durationToTicks(hours, freq);
  const tickIntervalHours = (freq / 3600).toFixed(2);
  const tickLabel = effectiveTickKey ? ` (${effectiveTickKey}=${ticks})` : "";

  return (
    <p className="mt-1 text-[10px] text-[var(--color-text-muted)]">
      ≈ {ticks} agent tick{ticks === 1 ? "" : "s"} at {freq}s frequency (~{tickIntervalHours}h per
      tick){tickLabel}
    </p>
  );
}

export function StrategyParamsForm({
  fields,
  groups,
  values,
  frequencySec,
  onChange,
}: Props) {
  const [expanded, setExpanded] = useState(true);

  const fieldsByGroup = groups.length
    ? groups.map((group) => ({
        group,
        entries: Object.entries(fields).filter(([, field]) => field.group === group),
      }))
    : [{ group: "Strategy", entries: Object.entries(fields) }];

  if (Object.keys(fields).length === 0) {
    return null;
  }

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]/50">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-3 text-left"
      >
        <div>
          <span className="text-sm font-semibold text-[var(--color-text)]">Strategy Parameters</span>
          <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
            Set values here and save via Session Defaults. Time-based fields use hours; tick counts update
            with tick frequency.
          </p>
        </div>
        <ChevronDown
          className={`h-4 w-4 shrink-0 text-[var(--color-text-muted)] transition-transform ${
            expanded ? "rotate-180" : ""
          }`}
        />
      </button>

      {expanded && (
        <div className="space-y-5 border-t border-[var(--color-border)] px-4 py-4">
          {fieldsByGroup.map(({ group, entries }) =>
            entries.length === 0 ? null : (
              <div key={group}>
                <h4 className="mb-3 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
                  {group}
                </h4>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {entries.map(([key, field]) => {
                    const raw = values[key];
                    const numeric =
                      typeof raw === "number" ? raw : parseFloat(String(raw ?? ""));
                    return (
                      <div key={key}>
                        <label className="mb-1 block text-xs font-medium text-[var(--color-text-muted)]">
                          {field.description || key}
                          {field.duration ? " (hours)" : ""}
                        </label>
                        <FieldInput
                          field={field}
                          value={values[key]}
                          onChange={(v) => onChange(key, v)}
                        />
                        {field.duration && !isNaN(numeric) && numeric > 0 && (
                          <DurationHint
                            hours={numeric}
                            frequencySec={frequencySec}
                            effectiveTickKey={field.effective_tick_key}
                          />
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            ),
          )}
        </div>
      )}
    </div>
  );
}
