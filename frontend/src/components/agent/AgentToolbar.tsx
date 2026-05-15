import { Brain, FileText, FlaskConical, ScrollText } from "lucide-react";

export type PanelId = "strategy" | "learnings" | "routines" | "experiments";

const BUTTONS: { id: PanelId; icon: typeof FileText; label: string }[] = [
  { id: "strategy", icon: FileText, label: "Strategy" },
  { id: "learnings", icon: Brain, label: "Learnings" },
  { id: "routines", icon: ScrollText, label: "Reports" },
  { id: "experiments", icon: FlaskConical, label: "Dry-Run" },
];

interface AgentToolbarProps {
  activePanel: PanelId | null;
  onToggle: (id: PanelId) => void;
}

export function AgentToolbar({ activePanel, onToggle }: AgentToolbarProps) {
  return (
    <div className="flex items-center gap-px rounded-md border border-[var(--color-border)] bg-[var(--color-border)] overflow-hidden">
      {BUTTONS.map(({ id, icon: Icon, label }) => {
        const isActive = activePanel === id;
        return (
          <button
            key={id}
            onClick={() => onToggle(id)}
            className={`flex items-center gap-1.5 px-3 py-2 text-[11px] font-semibold uppercase tracking-wide transition-colors ${
              isActive
                ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                : "bg-[var(--color-surface)] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            }`}
          >
            <Icon className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">{label}</span>
          </button>
        );
      })}
    </div>
  );
}
