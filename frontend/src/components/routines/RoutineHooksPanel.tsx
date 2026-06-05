import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, Check, ChevronDown, Loader2, Mail, Plus, Send, X } from "lucide-react";
import { useEffect, useState } from "react";

import { type RoutineHooks, api } from "@/lib/api";

interface Props {
  routineName: string;
  /** When false, render the form directly without the collapsible header. */
  collapsible?: boolean;
}

const EMPTY: RoutineHooks = {
  email: { enabled: false, recipients: [] },
  telegram: { enabled: false, chat_ids: [] },
  trigger: "success",
};

function TagsInput({
  values,
  onChange,
  placeholder,
  hint,
  validate,
}: {
  values: string[];
  onChange: (v: string[]) => void;
  placeholder: string;
  hint?: string;
  validate?: (v: string) => boolean;
}) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState(false);

  const commit = () => {
    const v = draft.trim().replace(/,$/, "");
    if (!v) return;
    if (validate && !validate(v)) {
      setError(true);
      return;
    }
    if (!values.includes(v)) onChange([...values, v]);
    setDraft("");
    setError(false);
  };

  return (
    <div className="space-y-1">
      <div
        className={`flex flex-wrap items-center gap-1.5 rounded border bg-[var(--color-surface)] px-2 py-1.5 ${
          error ? "border-[var(--color-red)]" : "border-[var(--color-border)]"
        }`}
      >
        {values.map((v) => (
          <span
            key={v}
            className="flex items-center gap-1 rounded bg-[var(--color-surface-hover)] px-2 py-0.5 text-xs text-[var(--color-text)]"
          >
            {v}
            <button
              type="button"
              onClick={() => onChange(values.filter((x) => x !== v))}
              className="text-[var(--color-text-muted)] hover:text-[var(--color-red)]"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
        <input
          type="text"
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value);
            if (error) setError(false);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              commit();
            } else if (e.key === "Backspace" && !draft && values.length) {
              onChange(values.slice(0, -1));
            }
          }}
          onBlur={commit}
          placeholder={values.length === 0 ? placeholder : "Add another…"}
          className="min-w-[140px] flex-1 bg-transparent px-1 py-0.5 text-sm text-[var(--color-text)] focus:outline-none"
        />
        <button
          type="button"
          onClick={commit}
          disabled={!draft.trim()}
          className="flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[10px] font-semibold text-[var(--color-primary)] hover:bg-[var(--color-surface-hover)] disabled:opacity-40"
          title="Add"
        >
          <Plus className="h-3 w-3" />
          Add
        </button>
      </div>
      {hint && (
        <p className="text-[10px] text-[var(--color-text-muted)]">{hint}</p>
      )}
    </div>
  );
}

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded px-3 py-1 text-xs font-medium transition-colors ${
        on
          ? "bg-[var(--color-primary)]/20 text-[var(--color-primary)]"
          : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
      }`}
    >
      {on ? "ON" : "OFF"}
    </button>
  );
}

function HooksForm({ routineName }: { routineName: string }) {
  const qc = useQueryClient();
  const [hooks, setHooks] = useState<RoutineHooks>(EMPTY);

  const { data: status } = useQuery({
    queryKey: ["hooks-status"],
    queryFn: () => api.getHooksStatus(),
    staleTime: 60_000,
  });

  const { data: saved } = useQuery({
    queryKey: ["routine-hooks", routineName],
    queryFn: () => api.getRoutineHooks(routineName),
  });

  useEffect(() => {
    if (saved) setHooks(saved);
  }, [saved, routineName]);

  const saveMutation = useMutation({
    mutationFn: () => api.saveRoutineHooks(routineName, hooks),
    onSuccess: (data) => {
      setHooks(data);
      qc.invalidateQueries({ queryKey: ["routine-hooks", routineName] });
    },
  });

  const smtpOff = status?.smtp_configured === false;

  return (
    <div className="space-y-4">
      <p className="text-[11px] text-[var(--color-text-muted)]">
        Send the report to these destinations after the routine runs.
      </p>

      {/* Email */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-2">
          <Mail className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
          <span className="text-xs font-medium text-[var(--color-text)]">Email</span>
          <div className="ml-auto">
            <Toggle
              on={hooks.email.enabled}
              onClick={() =>
                !smtpOff &&
                setHooks((h) => ({ ...h, email: { ...h.email, enabled: !h.email.enabled } }))
              }
            />
          </div>
        </div>
        {smtpOff ? (
          <p className="text-[11px] text-[var(--color-text-muted)]">
            SMTP not configured. Set SMTP_HOST / SMTP_USER in the server .env to enable email.
          </p>
        ) : (
          <TagsInput
            values={hooks.email.recipients}
            onChange={(v) => setHooks((h) => ({ ...h, email: { ...h.email, recipients: v } }))}
            placeholder="alice@example.com"
            hint="Add multiple recipients — press Enter, comma, or Add after each one."
            validate={(v) => v.includes("@") && (v.split("@")[1]?.includes(".") ?? false)}
          />
        )}
      </div>

      {/* Telegram */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-2">
          <Send className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
          <span className="text-xs font-medium text-[var(--color-text)]">Telegram</span>
          <div className="ml-auto">
            <Toggle
              on={hooks.telegram.enabled}
              onClick={() =>
                setHooks((h) => ({
                  ...h,
                  telegram: { ...h.telegram, enabled: !h.telegram.enabled },
                }))
              }
            />
          </div>
        </div>
        <TagsInput
          values={hooks.telegram.chat_ids}
          onChange={(v) => setHooks((h) => ({ ...h, telegram: { ...h.telegram, chat_ids: v } }))}
          placeholder="-1001234567890 (group) or user id"
          hint="Add multiple chats — press Enter, comma, or Add after each id."
          validate={(v) => /^-?\d+$/.test(v)}
        />
      </div>

      {/* Trigger */}
      <div className="space-y-1.5">
        <span className="text-xs font-medium text-[var(--color-text)]">Send when</span>
        <div className="relative">
          <select
            value={hooks.trigger}
            onChange={(e) =>
              setHooks((h) => ({ ...h, trigger: e.target.value as RoutineHooks["trigger"] }))
            }
            className="w-full appearance-none rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 pr-8 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
          >
            <option value="success">On success only</option>
            <option value="always">Always (success or error)</option>
            <option value="failure">On error only</option>
          </select>
          <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-muted)]" />
        </div>
      </div>

      {/* Save */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={saveMutation.isPending}
          onClick={() => saveMutation.mutate()}
          className="flex items-center gap-1.5 rounded bg-[var(--color-primary)] px-4 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
        >
          {saveMutation.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : saveMutation.isSuccess ? (
            <Check className="h-3.5 w-3.5" />
          ) : null}
          Save
        </button>
        {saveMutation.isError && (
          <span className="text-xs text-[var(--color-red)]">
            {(saveMutation.error as Error).message}
          </span>
        )}
      </div>
    </div>
  );
}

export function RoutineHooksPanel({ routineName, collapsible = true }: Props) {
  const [open, setOpen] = useState(false);

  if (!collapsible) {
    return <HooksForm routineName={routineName} />;
  }

  return (
    <div className="rounded-lg border border-[var(--color-border)]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <Bell className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
        <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
          Notifications
        </span>
        <ChevronDown
          className={`ml-auto h-4 w-4 text-[var(--color-text-muted)] transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div className="border-t border-[var(--color-border)] px-3 py-3">
          <HooksForm routineName={routineName} />
        </div>
      )}
    </div>
  );
}
