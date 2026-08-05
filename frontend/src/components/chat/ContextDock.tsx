import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, PanelRightClose, Radio, Zap } from "lucide-react";
import { useEffect, useState } from "react";

import { DockRoutines } from "@/components/chat/DockRoutines";
import { DockTasks } from "@/components/chat/DockTasks";
import { api, type Delegation } from "@/lib/api";

const OPEN_KEY = "condor.dock.open";
/** Where the dock stops being a column and starts overlaying (Tailwind `xl`). */
const WIDE = "(min-width: 1280px)";

/** The dock's default: a column when there is room for one, an icon rail when not. */
function defaultOpen(): boolean {
  return (
    window.matchMedia(WIDE).matches && localStorage.getItem(OPEN_KEY) !== "false"
  );
}

/**
 * What this conversation is doing.
 *
 * The workspace's third column: the rail is who you talk to, the middle is the
 * conversation, and this is the work it set in motion — the delegations it
 * started and the routines the agent on the other end owns. Both were only
 * reachable by leaving the conversation before.
 *
 * Below `xl` it stops being a column and overlays the transcript instead, the
 * mirror of what the rail does below `md`.
 */
export function ContextDock({
  delegations,
  conversationId,
  agentSlug,
  onOpenFleet,
}: {
  /** The shared `["delegations"]` result — the dock adds no poll of its own. */
  delegations: Delegation[];
  conversationId: string;
  agentSlug: string;
  onOpenFleet: () => void;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [tasksOpen, setTasksOpen] = useState(true);
  // Collapsed by default so the chat tab fetches routines only once someone
  // asks for them — this page never polled that endpoint before.
  const [routinesOpen, setRoutinesOpen] = useState(false);

  useEffect(() => {
    localStorage.setItem(OPEN_KEY, String(open));
  }, [open]);

  // Crossing the breakpoint re-derives the default: a narrow window must not
  // wake up with an overlay parked on top of the transcript.
  useEffect(() => {
    const mq = window.matchMedia(WIDE);
    const onChange = () => setOpen(defaultOpen());
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const { data: instances = [] } = useQuery({
    queryKey: ["routine-instances"],
    queryFn: api.getRoutineInstances,
    enabled: open && routinesOpen,
    refetchInterval: 5000,
  });

  const mineRunning = delegations.filter(
    (d) => d.conversation_id === conversationId && d.status === "running",
  ).length;

  if (!open) {
    return (
      <aside className="flex w-10 shrink-0 flex-col items-center gap-3 border-l border-[var(--color-border)] bg-[var(--color-bg)] py-2">
        <button
          onClick={() => setOpen(true)}
          className="flex flex-col items-center gap-0.5 rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          title="Show what this conversation is doing"
        >
          <Radio
            className={`h-4 w-4 ${mineRunning > 0 ? "text-emerald-400" : ""}`}
          />
          {mineRunning > 0 && (
            <span className="text-[9px] font-bold text-emerald-400">
              {mineRunning}
            </span>
          )}
        </button>
        <button
          onClick={() => {
            setOpen(true);
            setRoutinesOpen(true);
          }}
          className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          title="Show routines"
        >
          <Zap className="h-4 w-4" />
        </button>
      </aside>
    );
  }

  return (
    <aside className="absolute inset-y-0 right-0 z-30 flex w-[300px] shrink-0 flex-col overflow-y-auto border-l border-[var(--color-border)] bg-[var(--color-bg)] xl:relative xl:z-auto">
      <div className="flex shrink-0 items-center gap-2 border-b border-[var(--color-border)] px-3 py-2">
        <span className="min-w-0 flex-1 truncate text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
          This conversation
        </span>
        <button
          onClick={() => setOpen(false)}
          className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          title="Collapse"
        >
          <PanelRightClose className="h-3.5 w-3.5" />
        </button>
      </div>

      <DockSection
        icon={
          <Radio
            className={`h-3 w-3 shrink-0 ${mineRunning > 0 ? "text-emerald-400" : ""}`}
          />
        }
        label="Tasks"
        count={mineRunning || undefined}
        open={tasksOpen}
        onToggle={() => setTasksOpen((v) => !v)}
      >
        <DockTasks
          delegations={delegations}
          conversationId={conversationId}
          onOpenFleet={onOpenFleet}
        />
      </DockSection>

      <DockSection
        icon={<Zap className="h-3 w-3 shrink-0" />}
        label="Routines"
        open={routinesOpen}
        onToggle={() => setRoutinesOpen((v) => !v)}
      >
        <DockRoutines instances={instances} agentSlug={agentSlug} />
      </DockSection>
    </aside>
  );
}

function DockSection({
  icon,
  label,
  count,
  open,
  onToggle,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  count?: number;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="border-b border-[var(--color-border)]">
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-1.5 px-3 py-1.5 text-left text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
      >
        {open ? (
          <ChevronDown className="h-3 w-3 shrink-0" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0" />
        )}
        {icon}
        <span className="min-w-0 flex-1 truncate">{label}</span>
        {count !== undefined && (
          <span className="shrink-0 text-emerald-400">{count}</span>
        )}
      </button>
      {open && <div className="pb-1">{children}</div>}
    </div>
  );
}
