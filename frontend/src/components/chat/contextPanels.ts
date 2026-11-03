/**
 * What this conversation is doing: which panes are open, and how they reach
 * the rail.
 *
 * Split from the column that draws them for the same reason `accountPanels` is:
 * the two halves of the feature live in different places. The panes are a
 * column of their own (`ContextDock`), and the words that open them belong to
 * the one strip shared with everything else on the right edge
 * (`WorkspaceRail`).
 *
 * The dock used to render its own 40 px strip when it was collapsed, right
 * beside the workspace rail's — two strips built from the same button, drawn to
 * look alike on purpose, divided by a border that meant nothing to anyone
 * reading it. "The one thing worse than two rails is two rails that do not look
 * alike" was solving the wrong half of the problem. There is one rail now, so
 * the state behind its entries has to be reachable by the page that composes
 * it, which is what this hook is for.
 *
 * ## Panes, not a column
 *
 * The old state was a single boolean — is the column there — plus two section
 * disclosures inside it, which meant the two rail tiles could only ever open
 * *the dock*, while the two account tiles an inch above them opened a named
 * panel each. Five tiles in one strip that answer a click in two different ways
 * is the whole of what made the edge feel arbitrary. So this holds the same
 * shape `useAccountPanels` does: a list of open panes, a toggle per pane, and
 * no column at all when the list is empty. Both docks now read alike from the
 * rail, from the section headers and from storage.
 *
 * The queries come with it: they gate on whether anything is open, and that is
 * now known here.
 */
import { useQuery } from "@tanstack/react-query";
import { Radio, Zap } from "lucide-react";
import { useEffect, useState } from "react";

import { conversationInstances } from "@/components/chat/DockRoutines";
import type { RailItem } from "@/components/chat/WorkspaceRail";
import {
  api,
  type Delegation,
  type RoutineInfo,
  type RoutineInstance,
} from "@/lib/api";
import { DOCK_PANES_KEY } from "@/lib/sessionState";

/** Where the dock stops being a column and starts overlaying (Tailwind `xl`). */
const WIDE = "(min-width: 1280px)";

/** The two questions this dock answers about the conversation on screen. */
export type ContextPaneId = "tasks" | "routines";

const PANES: ContextPaneId[] = ["tasks", "routines"];

/**
 * What is open, for a window that has not said.
 *
 * Both panes on a wide window and neither on a narrow one, because below `xl`
 * the column overlays the transcript rather than sitting beside it: a reader
 * who has never touched the dock must not arrive to a panel parked on top of
 * the conversation.
 */
function fallback(): ContextPaneId[] {
  return window.matchMedia(WIDE).matches ? [...PANES] : [];
}

function readOpen(): ContextPaneId[] {
  try {
    const raw = localStorage.getItem(DOCK_PANES_KEY);
    // Nothing recorded means nothing recorded *about this rail*. The boolean
    // that used to hold the column's state was read here for one release, so a
    // reader who had collapsed the old dock would find it collapsed; it is not
    // read any more, because the two are not the same fact. Collapsing a column
    // whose only control was a chevron on its own header said "give me the
    // width back" — and it stuck, for months, because there was no other way to
    // ask for it back. The rail's two tiles are that other way, and inheriting
    // the old answer meant a workspace that opened on nothing and looked, to
    // the reader, like the panes had simply stopped defaulting to open.
    if (raw === null) return fallback();
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return fallback();
    return PANES.filter((id) => parsed.includes(id));
  } catch {
    // Unreadable storage is a browser that has never opened a pane, not an
    // error to surface: the rail is how they come back either way.
    return fallback();
  }
}

export type ContextPanels = {
  /** The panes on screen, in the order the column draws them. */
  shown: ContextPaneId[];
  toggle: (id: ContextPaneId) => void;
  closeAll: () => void;
  instances: RoutineInstance[];
  routines: RoutineInfo[];
  railItems: RailItem[];
};

export function useContextPanels({
  delegations,
  conversationId,
  agentSlug,
  libraryOpen,
}: {
  /** The shared `["delegations"]` result — this adds no poll of its own. */
  delegations: Delegation[];
  conversationId: string;
  agentSlug: string;
  /**
   * The library pane can be up while the column is not, and it reads the same
   * two lists — so it keeps their polls alive on its own.
   */
  libraryOpen: boolean;
}): ContextPanels {
  const [shown, setShown] = useState<ContextPaneId[]>(readOpen);

  const write = (next: ContextPaneId[]) => {
    setShown(next);
    localStorage.setItem(DOCK_PANES_KEY, JSON.stringify(next));
  };

  const toggle = (id: ContextPaneId) =>
    setShown((prev) => {
      const next = prev.includes(id)
        ? prev.filter((p) => p !== id)
        : PANES.filter((p) => p === id || prev.includes(p));
      localStorage.setItem(DOCK_PANES_KEY, JSON.stringify(next));
      return next;
    });

  const closeAll = () => write([]);

  // Crossing the breakpoint re-derives the default for a window that has never
  // said: a narrow one must not wake up with an overlay on the transcript, and
  // a wide one that was only narrow should not stay empty for it.
  useEffect(() => {
    const mq = window.matchMedia(WIDE);
    const onChange = () => {
      if (localStorage.getItem(DOCK_PANES_KEY) === null) setShown(fallback());
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const anyOpen = shown.length > 0 || libraryOpen;

  const { data: instances = [] } = useQuery({
    queryKey: ["routine-instances"],
    queryFn: api.getRoutineInstances,
    // Polled whenever anything in the dock is up, not only while the Routines
    // pane is: a closed Routines still has to be able to say "one is running".
    enabled: anyOpen,
    refetchInterval: 5000,
  });

  // The library itself, for the picker. Shares react-query's cache with the
  // report browser, so the pane it opens costs no second fetch.
  const { data: routines = [] } = useQuery({
    queryKey: ["routines"],
    queryFn: api.getRoutines,
    enabled: anyOpen,
  });

  const mineRunning = delegations.filter(
    (d) => d.conversation_id === conversationId && d.status === "running",
  ).length;
  // Counted the same way the list is built, so the badge and the rows agree.
  const routinesRunning = conversationInstances(
    instances,
    agentSlug,
    conversationId,
  ).filter((i) => i.status === "running").length;

  /**
   * The two tiles.
   *
   * The counts are on them whether or not the pane is open — that is the entire
   * job of a rail you are meant to watch while you type, and it is why the
   * delegation list is polled by the page rather than by the dock.
   */
  const railItems: RailItem[] = [
    {
      id: "tasks",
      label: "Tasks",
      Icon: Radio,
      hint: "Work handed to other agents from this conversation",
      active: shown.includes("tasks"),
      count: mineRunning,
      onToggle: () => toggle("tasks"),
    },
    {
      id: "routines",
      label: "Routines",
      Icon: Zap,
      hint: "Scripts this agent runs, on demand or on a schedule",
      active: shown.includes("routines"),
      count: routinesRunning,
      onToggle: () => toggle("routines"),
    },
  ];

  return { shown, toggle, closeAll, instances, routines, railItems };
}
