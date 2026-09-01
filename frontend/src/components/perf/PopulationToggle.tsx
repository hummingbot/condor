import { Activity, History, Server, Shapes } from "lucide-react";
import type { ComponentType } from "react";

import type { GroupBy, Population } from "@/lib/perf-tree";

/** One segment of a two-way control: an icon, a word, and which one is on. */
function Segment<T extends string>({
  value,
  current,
  label,
  icon: Icon,
  onSelect,
  title,
}: {
  value: T;
  current: T;
  label: string;
  icon: ComponentType<{ className?: string }>;
  onSelect: (value: T) => void;
  title: string;
}) {
  const active = value === current;
  return (
    <button
      type="button"
      onClick={() => onSelect(value)}
      aria-pressed={active}
      title={title}
      className={`flex flex-1 items-center justify-center gap-1 rounded px-2 py-1 text-[10px] font-medium transition-colors ${
        active
          ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
          : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
      }`}
    >
      <Icon className="h-3 w-3 shrink-0" />
      {label}
    </button>
  );
}

/**
 * Which population the browser is reporting on (FEAT-086).
 *
 * The two sides are the *same report* over different records — the same strip,
 * the same chart, the same rows — so this is a switch rather than two tabs with
 * layouts of their own. Nothing is measured one way while it is live and
 * another way once it has finished.
 */
export function PopulationToggle({
  population,
  onChange,
}: {
  population: Population;
  onChange: (next: Population) => void;
}) {
  return (
    <div className="flex gap-0.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-0.5">
      <Segment
        value="running"
        current={population}
        label="Running"
        icon={Activity}
        onSelect={onChange}
        title="Live controllers and the executors working under them"
      />
      <Segment
        value="terminated"
        current={population}
        label="Terminated"
        icon={History}
        onSelect={onChange}
        title="Executors that have closed, and the bot runs that have finished"
      />
    </div>
  );
}

/**
 * What the level between the fleet and its controllers groups on.
 *
 * Only that level changes: controller and executor nodes keep the same
 * identity in both trees, so switching keeps the reader on whatever they had
 * selected (see `resolveScope`).
 */
export function GroupByToggle({
  groupBy,
  onChange,
}: {
  groupBy: GroupBy;
  onChange: (next: GroupBy) => void;
}) {
  return (
    <div className="flex gap-0.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-0.5">
      <Segment
        value="bot"
        current={groupBy}
        label="By bot"
        icon={Server}
        onSelect={onChange}
        title="Group controllers under the bot running them"
      />
      <Segment
        value="type"
        current={groupBy}
        label="By type"
        icon={Shapes}
        onSelect={onChange}
        title="Group by what each thing is: a controller's class, an executor's type"
      />
    </div>
  );
}
