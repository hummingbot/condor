import {
  BookOpen,
  Brain,
  FileText,
  Gauge,
  History,
  Layers,
  Repeat,
  Sparkles,
  Wrench,
  Zap,
} from "lucide-react";

import type { WorkspaceViewId } from "@/components/agent/workspace/views";

/**
 * The workspace's one navigation: what the agent is *doing*, and what it *is*.
 *
 * This replaces `AgentKnowledge`'s horizontal tab strip — the chrome the agent
 * page used to draw around the seven sections — and puts the loop's own views
 * above it in the same column. Before it there were three tab strips on the way
 * to one agent (the page's sections, the workbench's bands, the Lab's rail) and
 * no strip anywhere that could get you from a run back to a memory, because
 * they were different pages.
 *
 * Two groups, because the reader's two questions are different questions.
 * **Doing** is the loop: what it decided this hour, the runs behind that, the
 * money and the fleet it put into the world. **Being** is the seven sections an
 * agent is made of, and they change on the timescale of a person editing them.
 * Mixing them into one flat list is what made "open the agent" land on an
 * `AGENT.md` dump while a loop was running three feet away.
 *
 * It never unmounts: selecting an entry sets `?view=` and the body swaps under
 * a header, a loop bar and a tick spine that stay put.
 */
interface SpineEntry {
  id: WorkspaceViewId;
  label: string;
  icon: React.ReactNode;
  /** Why this section exists, for the reader hovering it. */
  hint: string;
}

const ICON = "h-3.5 w-3.5";

/** The loop. Ordered by how soon the answer goes stale. */
const DOING_ENTRIES: SpineEntry[] = [
  {
    id: "now",
    label: "Now",
    icon: <Zap className={ICON} />,
    hint: "What it just decided, what needs attention, what it deployed",
  },
  {
    id: "runs",
    label: "Runs",
    icon: <Repeat className={ICON} />,
    hint: "Every run of every strategy, tick by tick",
  },
  {
    id: "playbook",
    label: "Playbook",
    icon: <FileText className={ICON} />,
    hint: "Operate the strategy in scope — start, stop, playbook, learnings",
  },
  {
    id: "money",
    label: "Money",
    icon: <Gauge className={ICON} />,
    hint: "What the strategy in scope has made and traded",
  },
  {
    id: "fleet",
    label: "Fleet",
    icon: <Layers className={ICON} />,
    hint: "The bots, controllers and executors this run put into the world",
  },
];

/** The seven sections, in `KNOWLEDGE_TABS` order — the taxonomy's own. */
const BEING_ENTRIES: SpineEntry[] = [
  {
    id: "brain",
    label: "Brain",
    icon: <Brain className={ICON} />,
    hint: "AGENT.md — what the model is handed at the top of every turn",
  },
  {
    id: "skills",
    label: "Skills",
    icon: <BookOpen className={ICON} />,
    hint: "The playbooks it reads",
  },
  {
    id: "memories",
    label: "Memories",
    icon: <Sparkles className={ICON} />,
    hint: "What it remembers about you",
  },
  {
    id: "tools",
    label: "Tools",
    icon: <Wrench className={ICON} />,
    hint: "The tools its seat mounts",
  },
  {
    id: "strategies",
    label: "Strategies",
    icon: <Repeat className={ICON} />,
    hint: "Every playbook it can loop",
  },
  {
    id: "routines",
    label: "Routines",
    icon: <Zap className={ICON} />,
    hint: "The scripts it can run, on demand or on a schedule",
  },
  {
    id: "activity",
    label: "Activity",
    icon: <History className={ICON} />,
    hint: "Tasks handed to it and consults it answered",
  },
];

export function WorkspaceSpine({
  current,
  onSelect,
  alertCount = 0,
}: {
  /** The lit entry — `spineSectionFor(view)`, not the raw view. */
  current: WorkspaceViewId;
  onSelect: (view: WorkspaceViewId) => void;
  /** How many things Now would raise, so the reader sees it from anywhere. */
  alertCount?: number;
}) {
  return (
    <nav
      aria-label="Workspace sections"
      className="flex w-36 shrink-0 flex-col gap-3 overflow-y-auto border-r border-[var(--color-border)] px-2 py-3"
    >
      <Group
        label="Doing"
        entries={DOING_ENTRIES}
        current={current}
        onSelect={onSelect}
        alertCount={alertCount}
      />
      <Group
        label="Being"
        entries={BEING_ENTRIES}
        current={current}
        onSelect={onSelect}
        alertCount={0}
      />
    </nav>
  );
}

function Group({
  label,
  entries,
  current,
  onSelect,
  alertCount,
}: {
  label: string;
  entries: SpineEntry[];
  current: WorkspaceViewId;
  onSelect: (view: WorkspaceViewId) => void;
  alertCount: number;
}) {
  return (
    <div>
      <div className="px-2 pb-1 text-[9px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]/70">
        {label}
      </div>
      <div className="flex flex-col gap-0.5">
        {entries.map((entry) => {
          const active = entry.id === current;
          return (
            <button
              key={entry.id}
              type="button"
              data-spine-entry={entry.id}
              aria-current={active ? "page" : undefined}
              onClick={() => onSelect(entry.id)}
              title={entry.hint}
              className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-colors ${
                active
                  ? "bg-[var(--color-primary)]/10 font-medium text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              }`}
            >
              {entry.icon}
              <span className="min-w-0 flex-1 truncate">{entry.label}</span>
              {/* The count rides on Now wherever the reader is, because an
                  alert you have to open a section to discover is not one. */}
              {entry.id === "now" && alertCount > 0 && (
                <span
                  data-spine-alerts
                  className="rounded-full bg-amber-500/20 px-1.5 text-[10px] font-bold text-amber-400"
                >
                  {alertCount}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
