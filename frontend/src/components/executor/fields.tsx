import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
  Crosshair,
} from "lucide-react";

// ── Generic dispatch type ──

export type FieldDispatch = (action: { type: "SET_FIELD"; field: string; value: unknown }) => void;

// ── PriceField ──

export function PriceField({
  label,
  value,
  field,
  activePickField,
  dispatch,
  valid,
  hint,
}: {
  label: string;
  value: number;
  field: string;
  activePickField: string | null;
  dispatch: FieldDispatch;
  valid: boolean;
  hint?: string;
}) {
  const isActive = activePickField === field;
  const inputRef = useRef<HTMLInputElement>(null);
  const [localValue, setLocalValue] = useState(value === 0 ? "" : String(value));

  useEffect(() => {
    if (document.activeElement !== inputRef.current) {
      setLocalValue(value === 0 ? "" : String(value));
    }
  }, [value]);

  return (
    <div>
      <label className="mb-1 flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
        {label}
        {value > 0 && (
          valid
            ? <Check className="h-3 w-3 text-[var(--color-green)]" />
            : <AlertTriangle className="h-3 w-3 text-[var(--color-red)]" />
        )}
      </label>
      <div className="flex gap-1">
        <input
          ref={inputRef}
          type="number"
          step="any"
          value={localValue}
          onChange={(e) => {
            setLocalValue(e.target.value);
            const num = parseFloat(e.target.value);
            dispatch({ type: "SET_FIELD", field, value: isNaN(num) ? 0 : num });
          }}
          onBlur={() => setLocalValue(value === 0 ? "" : String(value))}
          placeholder="0.00"
          className={`flex-1 rounded border bg-[var(--color-bg)] px-2.5 py-1.5 font-mono text-xs text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]/40 focus:outline-none ${
            isActive
              ? "border-[var(--color-primary)] ring-1 ring-[var(--color-primary)]"
              : "border-[var(--color-border)] focus:border-[var(--color-primary)]"
          }`}
        />
        <button
          onClick={() =>
            dispatch({
              type: "SET_FIELD",
              field: "activePickField",
              value: isActive ? null : field,
            })
          }
          className={`flex items-center rounded border px-2 transition-colors ${
            isActive
              ? "border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-primary)]"
              : "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
          }`}
          title="Pick from chart"
        >
          <Crosshair className="h-3.5 w-3.5" />
        </button>
      </div>
      {hint && <p className="mt-0.5 text-[10px] text-[var(--color-text-muted)]">{hint}</p>}
    </div>
  );
}

// ── NumberField ──

export function NumberField({
  label,
  value,
  field,
  dispatch,
  step = 1,
  min,
  suffix,
  isPercent = false,
}: {
  label: string;
  value: number;
  field: string;
  dispatch: FieldDispatch;
  step?: number;
  min?: number;
  suffix?: string;
  isPercent?: boolean;
}) {
  const displayValue = isPercent ? value * 100 : value;
  const inputRef = useRef<HTMLInputElement>(null);
  const [localValue, setLocalValue] = useState(displayValue === 0 ? "" : String(displayValue));

  useEffect(() => {
    if (document.activeElement !== inputRef.current) {
      setLocalValue(displayValue === 0 ? "" : String(displayValue));
    }
  }, [displayValue]);

  return (
    <div>
      <label className="mb-1 block text-xs text-[var(--color-text-muted)]">{label}</label>
      <div className="flex items-center gap-1">
        <input
          ref={inputRef}
          type="number"
          step={isPercent ? step * 100 : step}
          min={min !== undefined ? (isPercent ? min * 100 : min) : undefined}
          value={localValue}
          onChange={(e) => {
            setLocalValue(e.target.value);
            const raw = parseFloat(e.target.value);
            dispatch({ type: "SET_FIELD", field, value: isPercent ? (isNaN(raw) ? 0 : raw / 100) : (isNaN(raw) ? 0 : raw) });
          }}
          onBlur={() => setLocalValue(displayValue === 0 ? "" : String(displayValue))}
          placeholder="0"
          className="flex-1 rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-1.5 font-mono text-xs text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]/40 focus:border-[var(--color-primary)] focus:outline-none"
        />
        {suffix && (
          <span className="text-[10px] text-[var(--color-text-muted)]">{suffix}</span>
        )}
      </div>
    </div>
  );
}

// ── SelectField ──

export function SelectField({
  label,
  value,
  field,
  dispatch,
  options,
}: {
  label: string;
  value: number | string;
  field: string;
  dispatch: FieldDispatch;
  options: { value: number | string; label: string }[];
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const selected = options.find((o) => o.value === value) ?? options[0];

  return (
    <div ref={containerRef} className="relative">
      <label className="mb-1 block text-xs text-[var(--color-text-muted)]">{label}</label>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1.5 text-xs text-[var(--color-text)] transition-colors hover:bg-[var(--color-surface-hover)]"
      >
        <span>{selected?.label}</span>
        <ChevronDown className={`h-3.5 w-3.5 text-[var(--color-text-muted)] transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="absolute z-50 mt-1 max-h-48 w-full overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] shadow-lg">
          {options.map((opt) => {
            const isActive = opt.value === value;
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => {
                  const asNum = Number(opt.value);
                  dispatch({ type: "SET_FIELD", field, value: isNaN(asNum) ? opt.value : asNum });
                  setOpen(false);
                }}
                className={`flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs transition-colors ${
                  isActive
                    ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                    : "text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
                }`}
              >
                {isActive && <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-primary)]" />}
                <span className={isActive ? "" : "ml-3.5"}>{opt.label}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── ToggleField ──

export function ToggleField({
  label,
  value,
  field,
  dispatch,
}: {
  label: string;
  value: boolean;
  field: string;
  dispatch: FieldDispatch;
}) {
  return (
    <div className="flex items-center gap-2">
      <button
        onClick={() => dispatch({ type: "SET_FIELD", field, value: !value })}
        className={`relative h-5 w-9 rounded-full transition-colors ${
          value ? "bg-[var(--color-primary)]" : "bg-[var(--color-border)]"
        }`}
      >
        <span
          className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${
            value ? "left-[18px]" : "left-0.5"
          }`}
        />
      </button>
      <span className="text-xs text-[var(--color-text)]">{label}</span>
    </div>
  );
}

// ── SectionHeader ──

export function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
      {children}
    </span>
  );
}

// ── Collapsible Advanced Section ──

export function AdvancedSection({
  open,
  onToggle,
  children,
}: {
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div>
      <button
        onClick={onToggle}
        className="flex w-full items-center justify-between rounded px-1 py-1.5 text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
      >
        Advanced
        {open ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
      </button>
      {open && <div className="mt-2 space-y-2.5">{children}</div>}
    </div>
  );
}

// ── Validation Display ──

export function ValidationMessages({ errors, warnings }: { errors: string[]; warnings?: string[] }) {
  if (!errors.length && !(warnings?.length)) return null;
  return (
    <div className="space-y-1">
      {errors.map((err, i) => (
        <p key={i} className="flex items-center gap-1 text-[10px] text-[var(--color-red)]">
          <AlertTriangle className="h-3 w-3 shrink-0" />
          {err}
        </p>
      ))}
      {warnings?.map((warn, i) => (
        <p key={i} className="flex items-center gap-1 text-[10px] text-amber-400">
          <AlertTriangle className="h-3 w-3 shrink-0" />
          {warn}
        </p>
      ))}
    </div>
  );
}

// ── AmountField (with base/quote toggle) ──

export function AmountField({
  value,
  field,
  dispatch,
  currentPrice,
  step = 0.001,
  min = 0,
  pair,
}: {
  value: number;
  field: string;
  dispatch: FieldDispatch;
  currentPrice: number | null;
  step?: number;
  min?: number;
  pair?: string;
}) {
  const [inQuote, setInQuote] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const baseAsset = pair?.split("-")[0] ?? "base";
  const quoteAsset = pair?.split("-")[1] ?? "quote";

  // When inQuote mode, show quote value; dispatch always stores base amount
  const displayValue = inQuote && currentPrice && currentPrice > 0 ? value * currentPrice : value;
  const [localValue, setLocalValue] = useState(displayValue === 0 ? "" : String(displayValue));

  useEffect(() => {
    if (document.activeElement !== inputRef.current) {
      setLocalValue(displayValue === 0 ? "" : String(Number(displayValue.toPrecision(8))));
    }
  }, [displayValue]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setLocalValue(e.target.value);
    const raw = parseFloat(e.target.value);
    if (isNaN(raw)) {
      dispatch({ type: "SET_FIELD", field, value: 0 });
    } else if (inQuote && currentPrice && currentPrice > 0) {
      dispatch({ type: "SET_FIELD", field, value: raw / currentPrice });
    } else {
      dispatch({ type: "SET_FIELD", field, value: raw });
    }
  };

  const toggleUnit = () => {
    setInQuote(!inQuote);
    // Recalc display
    const newDisplay = !inQuote && currentPrice && currentPrice > 0 ? value * currentPrice : value;
    setLocalValue(newDisplay === 0 ? "" : String(Number(newDisplay.toPrecision(8))));
  };

  const hint = inQuote && currentPrice && currentPrice > 0 && value > 0
    ? `≈ ${value.toPrecision(6)} ${baseAsset}`
    : !inQuote && currentPrice && currentPrice > 0 && value > 0
    ? `≈ ${(value * currentPrice).toPrecision(6)} ${quoteAsset}`
    : undefined;

  return (
    <div>
      <label className="mb-1 block text-xs text-[var(--color-text-muted)]">
        Amount ({inQuote ? quoteAsset : baseAsset})
      </label>
      <div className="flex items-center gap-1">
        <input
          ref={inputRef}
          type="number"
          step={inQuote && currentPrice ? step * currentPrice : step}
          min={min}
          value={localValue}
          onChange={handleChange}
          onBlur={() => setLocalValue(displayValue === 0 ? "" : String(Number(displayValue.toPrecision(8))))}
          placeholder="0"
          className="flex-1 rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-1.5 font-mono text-xs text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]/40 focus:border-[var(--color-primary)] focus:outline-none"
        />
        <button
          onClick={toggleUnit}
          className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1.5 text-[10px] font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)]"
          title={`Switch to ${inQuote ? baseAsset : quoteAsset}`}
        >
          {inQuote ? quoteAsset : baseAsset}
        </button>
      </div>
      {hint && <p className="mt-0.5 text-[10px] text-[var(--color-text-muted)]">{hint}</p>}
    </div>
  );
}

// ── LeverageField ──

export function LeverageField({
  value,
  field,
  dispatch,
  isSpot = false,
}: {
  value: number;
  field: string;
  dispatch: FieldDispatch;
  isSpot?: boolean;
}) {
  if (isSpot) {
    return (
      <div>
        <label className="mb-1 block text-xs text-[var(--color-text-muted)]">Leverage</label>
        <div className="flex items-center gap-1">
          <input
            type="text"
            value="1"
            disabled
            className="flex-1 rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-1.5 font-mono text-xs text-[var(--color-text-muted)] opacity-60"
          />
          <span className="text-[10px] text-[var(--color-text-muted)]">x (spot)</span>
        </div>
      </div>
    );
  }
  return <NumberField label="Leverage" value={value} field={field} dispatch={dispatch} step={1} min={1} suffix="x" />;
}

// ── Shared constants ──

export const ORDER_TYPE_OPTIONS = [
  { value: 1, label: "Market" },
  { value: 2, label: "Limit" },
  { value: 3, label: "Limit Maker" },
];

export const SIDE_OPTIONS = [
  { value: 1, label: "LONG", color: "var(--color-green)" },
  { value: 2, label: "SHORT", color: "var(--color-red)" },
];

export function SideSelector({ side, dispatch }: { side: 1 | 2; dispatch: FieldDispatch }) {
  return (
    <div>
      <SectionHeader>Direction</SectionHeader>
      <div className="mt-1.5 flex gap-1">
        {SIDE_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            onClick={() => dispatch({ type: "SET_FIELD", field: "side", value: opt.value })}
            className={`flex-1 rounded py-2 text-xs font-bold transition-colors ${
              side === opt.value
                ? `bg-[${opt.color}] text-white`
                : "bg-[var(--color-surface)] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
            }`}
            style={side === opt.value ? { backgroundColor: opt.color } : undefined}
          >
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  );
}
