import { LayoutGrid, MessageSquare } from "lucide-react";

export type AgentsTab = "chat" | "fleet";

const OPTIONS = [
  { key: "chat", label: "Chat", icon: MessageSquare },
  { key: "fleet", label: "Fleet", icon: LayoutGrid },
] as const;

/**
 * Chat ↔ Fleet, small enough to live inside chrome that already exists.
 *
 * `/agents` used to spend a whole 62 px row on this toggle; it now rides in the
 * chat rail's live strip and in the fleet header, so neither tab pays for it.
 * Purely presentational — the host owns the `?tab=` param.
 */
export function AgentsTabSwitch({
  tab,
  onChange,
}: {
  tab: AgentsTab;
  onChange: (tab: AgentsTab) => void;
}) {
  return (
    <div className="flex shrink-0 items-center gap-0.5 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] p-0.5">
      {OPTIONS.map(({ key, label, icon: Icon }) => (
        <button
          key={key}
          onClick={() => onChange(key)}
          title={label}
          aria-label={label}
          aria-pressed={tab === key}
          className={`flex h-5 w-6 items-center justify-center rounded transition-colors ${
            tab === key
              ? "bg-[var(--color-bg)] text-[var(--color-text)] shadow-sm"
              : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          }`}
        >
          <Icon className="h-3 w-3" />
        </button>
      ))}
    </div>
  );
}
