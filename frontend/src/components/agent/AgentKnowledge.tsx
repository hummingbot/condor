import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  BookOpen,
  Brain,
  Check,
  ChevronDown,
  ChevronRight,
  History,
  Loader2,
  Pencil,
  Plus,
  Repeat,
  Save,
  Sparkles,
  Trash2,
  Wand2,
  Wrench,
  X,
  Zap,
} from "lucide-react";
import { useCallback, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { ActivityFeed } from "@/components/agent/ActivityFeed";
import { MarkdownEditor } from "@/components/agent/AgentOverviewTab";
import { AgentStrategies } from "@/components/agent/AgentStrategies";
import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import type {
  KnowledgeLayout,
  KnowledgeTabId,
} from "@/components/agent/knowledgeTabs";
import {
  api,
  type AgentBrain,
  type MemoryCard,
  type SkillBody,
  type SkillCard,
  type SkillProposal,
} from "@/lib/api";
import { formatRoutineName } from "@/lib/routineUtils";

/** The taxonomy `MemoryStore` validates against — anything else falls back to `fact`. */
const MEMORY_TYPES = ["preference", "fact", "feedback", "reference"] as const;

/** What the reader drilled into, if anything — a playbook or a memory. */
type Reading =
  { kind: "skill"; card: SkillCard } | { kind: "memory"; card: MemoryCard };

/** What `getAgentMemory` returns — a name and the body, nothing else. */
type MemoryBody = { name: string; body: string };

/**
 * Everything an agent is, in one editable surface.
 *
 * What the model is handed at the top of every turn — its AGENT.md, the
 * playbooks and memories that reach it as an index, its tool allowlist — plus
 * the two catalogs it can act through, its routines and its strategies. Reading
 * the panel and reading the prompt should leave you with the same picture.
 *
 * It is also where you *change* that picture. Editing used to live somewhere
 * else entirely: this panel was read-only and the one AGENT.md editor sat behind
 * a header button on the agent page, with skills and memories not editable from
 * the web at all. So the panel that answered "what does it know" could never
 * answer "and let me fix that".
 *
 * This is the body of `/agents/{slug}` and the chat's Knowledge link goes
 * straight there, so there is one address for both questions.
 *
 * Bodies are fetched one at a time. The list endpoint carries metadata only, so
 * opening the panel on an agent with forty playbooks costs a few kilobytes, and
 * the one you clicked costs its own request.
 */
export function AgentKnowledge({
  slug,
  layout = "tabs",
  tab,
  onTabChange,
  routinesAction,
  onOpenRoutine,
  onAskAgent,
  onDirtyChange,
}: {
  slug: string;
  /** How the sections are offered — see {@link KnowledgeLayout}. */
  layout?: KnowledgeLayout;
  /**
   * Which section is open, when the host wants a say. The agent page reads it
   * off `?tab=` so a link can land on Skills; the chat panel holds it in state.
   * Left out, the component keeps its own — which is what the page did before
   * anything outside it needed to know (FEAT-081).
   */
  tab?: KnowledgeTabId;
  onTabChange?: (tab: KnowledgeTabId) => void;
  /** Anything the host wants beside the routine catalog (e.g. Reports). */
  routinesAction?: React.ReactNode;
  /**
   * Where a routine row goes. Given, the host takes over — the chat's pane
   * hands it to the routine library it already houses (FEAT-077) rather than
   * this panel growing a second one. Absent, a row is a plain list entry.
   */
  onOpenRoutine?: (routineName: string) => void;
  /**
   * Put a request to this agent, in its own words (FEAT-092).
   *
   * The panel decides *what to say* — one sentence per kind of row, naming the
   * item you clicked — and the host decides *how to say it*, because the two
   * hosts have different powers: the chat sends into a fresh session, the agent
   * page can only navigate to one. Absent, the row simply does not offer it,
   * so a host that passes nothing sees the panel it had.
   */
  onAskAgent?: (text: string) => void;
  /**
   * An editor here has unsaved text. Reported outward because a host that can
   * be closed in one click — the chat's pane — owes the reader a question
   * before dropping it; the page, which you have to navigate away from, does
   * not and passes nothing.
   */
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const [ownTab, setOwnTab] = useState<KnowledgeTabId>("brain");
  const activeTab = tab ?? ownTab;
  const setTab = useCallback(
    (next: KnowledgeTabId) => {
      setOwnTab(next);
      onTabChange?.(next);
    },
    [onTabChange],
  );
  const [reading, setReading] = useState<Reading | null>(null);
  const [creating, setCreating] = useState<"skill" | "memory" | null>(null);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState<Reading | null>(null);

  const queryClient = useQueryClient();

  const {
    data: brain,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["agent-brain", slug],
    queryFn: () => api.getAgentBrain(slug),
    // A brain changes when someone edits it, not while you read it. Fresh on
    // open is what matters; a poll here would walk the skill and memory stores
    // on disk every few seconds for a panel nobody is looking at.
    staleTime: 30_000,
  });

  // One dirty flag for whichever editor is mounted — only ever one is.
  const [dirty, setDirty] = useState(false);
  const markDirty = useCallback(
    (d: boolean) => {
      setDirty(d);
      onDirtyChange?.(d);
    },
    [onDirtyChange],
  );

  /** Leaving an editor drops its unsaved state along with the form. */
  const leaveEditor = useCallback(() => {
    setEditing(false);
    setCreating(null);
    markDirty(false);
  }, [markDirty]);

  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["agent-brain", slug] });
    // The agent page polls a different key for the same AGENT.md.
    queryClient.invalidateQueries({ queryKey: ["agent", slug] });
  }, [queryClient, slug]);

  const deleteMut = useMutation({
    mutationFn: () =>
      deleting!.kind === "skill"
        ? api.deleteAgentSkill(slug, deleting!.card.slug)
        : api.deleteAgentMemory(slug, deleting!.card.name),
    onSuccess: () => {
      refresh();
      setDeleting(null);
      setReading(null);
      leaveEditor();
    },
  });

  /**
   * Switch one playbook, routine or tool off for this agent, or back on
   * (FEAT-090, FEAT-091).
   *
   * Optimistic on the brain cache: the switch is the whole feedback and a
   * round-trip of dead switch is worse than a flicker on the rare failure.
   * The cached row is patched rather than the query invalidated, so the tab
   * does not re-fetch and re-sort under the cursor mid-curation; settling
   * invalidates once, which is when the server's answer matters.
   */
  const muteMut = useMutation({
    mutationFn: (v: {
      kind: "skill" | "routine" | "tool";
      name: string;
      muted: boolean;
    }) => api.setAgentMute(slug, v),
    onMutate: async (v) => {
      await queryClient.cancelQueries({ queryKey: ["agent-brain", slug] });
      const previous = queryClient.getQueryData<AgentBrain>([
        "agent-brain",
        slug,
      ]);
      queryClient.setQueryData<AgentBrain>(["agent-brain", slug], (old) =>
        old
          ? {
              ...old,
              skills:
                v.kind === "skill"
                  ? old.skills.map((x) =>
                      x.slug === v.name ? { ...x, muted: v.muted } : x,
                    )
                  : old.skills,
              routines:
                v.kind === "routine"
                  ? old.routines.map((x) =>
                      x.name === v.name ? { ...x, muted: v.muted } : x,
                    )
                  : old.routines,
              tools:
                v.kind === "tool"
                  ? old.tools.map((x) =>
                      x.name === v.name ? { ...x, muted: v.muted } : x,
                    )
                  : old.tools,
            }
          : old,
      );
      return { previous };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.previous)
        queryClient.setQueryData(["agent-brain", slug], ctx.previous);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["agent-brain", slug] });
    },
  });

  // A count is what the *agent* gets, because that is the number the tab is
  // asked for. When something is muted the panel lists more rows than that, so
  // the count says "7/9" — otherwise the tab would contradict its own list.
  const counts = {
    skills: brain?.skills.filter((s) => !s.muted).length ?? 0,
    memories: brain?.memories.length ?? 0,
    tools: brain?.tools.filter((t) => !t.muted).length ?? 0,
    strategies: brain?.strategies.length ?? 0,
    routines: brain?.routines.filter((r) => !r.muted).length ?? 0,
  };
  const totals = {
    skills: brain?.skills.length ?? 0,
    routines: brain?.routines.length ?? 0,
    tools: brain?.tools.length ?? 0,
  };
  const withMuted = (live: number, total: number) =>
    total > live
      ? { countLabel: `${live}/${total}`, countTitle: `${live} of ${total}` }
      : {};

  const tabs: {
    id: KnowledgeTabId;
    label: string;
    icon: React.ReactNode;
    count?: number;
    countLabel?: string;
    countTitle?: string;
  }[] = [
    { id: "brain", label: "Brain", icon: <Brain className="h-3.5 w-3.5" /> },
    {
      id: "skills",
      label: "Skills",
      icon: <BookOpen className="h-3.5 w-3.5" />,
      count: counts.skills,
      ...withMuted(counts.skills, totals.skills),
    },
    {
      id: "memories",
      label: "Memories",
      icon: <Sparkles className="h-3.5 w-3.5" />,
      count: counts.memories,
    },
    {
      id: "tools",
      label: "Tools",
      icon: <Wrench className="h-3.5 w-3.5" />,
      // The mounted surface, so it counts on every agent now — an allowlist is
      // a different statement and no longer decides whether there is a number.
      count: counts.tools,
      ...withMuted(counts.tools, totals.tools),
    },
    {
      id: "strategies",
      label: "Strategies",
      icon: <Repeat className="h-3.5 w-3.5" />,
      count: counts.strategies,
    },
    {
      id: "routines",
      label: "Routines",
      icon: <Zap className="h-3.5 w-3.5" />,
      count: counts.routines,
      ...withMuted(counts.routines, totals.routines),
    },
    {
      id: "activity",
      label: "Activity",
      // Everything this agent actually did — the tasks handed to it in the
      // background and the consults it answered — this run's and every earlier
      // one, read back from disk (FEAT-058).
      icon: <History className="h-3.5 w-3.5" />,
    },
  ];

  const rail = layout === "rail";

  /** Every section change also leaves whatever drill-down or editor was open. */
  const openTab = (id: KnowledgeTabId) => {
    setTab(id);
    setReading(null);
    leaveEditor();
  };

  const nav = rail ? (
    // A column down the *right* edge, because eight tabs wrap to three rows in
    // a 400px pane — and because in the chat this rail sits against the dock,
    // where everything else you click to open something already is. Same tabs,
    // same counts, same order, each still saying its name: an icon alone made
    // the reader learn seven glyphs to find "Tools".
    //
    // The names are set flat rather than turned on their side. Sideways text
    // buys 30px of width and charges the reader for it — a Latin word is
    // recognised by its shape, and rotating it makes you decode it letter by
    // letter — and it charges the column too: `STRATEGIES` on its side is 60px
    // of height per key, so the seven ran the full height of the pane. Upright
    // at 10px they are ~36px each, and the whole rail is a third of the column.
    <div
      role="tablist"
      aria-orientation="vertical"
      aria-label="Sections"
      className="flex w-20 shrink-0 flex-col items-center gap-1 overflow-y-auto border-l border-[var(--color-border)] px-1 py-2"
    >
      {tabs.map((t) => {
        const name =
          t.countTitle !== undefined
            ? `${t.label} (${t.countTitle})`
            : t.count !== undefined && t.count > 0
              ? `${t.label} (${t.count})`
              : t.label;
        const active = activeTab === t.id;
        return (
          /* Each section is its own key rather than a name in a list: a border,
           a ground of its own and real air between it and its neighbours. The
           rail was seven words stacked with a hairline of space, which read as
           one striped column and made the reader parse text to find the thing
           they wanted to click. The selected one is filled, not underlined —
           at this width a 2px mark on the outer edge was the only difference
           between the section you are in and the six you are not. */
          <button
            key={t.id}
            role="tab"
            aria-selected={active}
            aria-label={name}
            onClick={() => openTab(t.id)}
            title={name}
            className={`flex w-full shrink-0 flex-col items-center gap-1 rounded-md border px-1 py-2 transition-colors ${
              active
                ? "border-[var(--color-primary)]/40 bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                : "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text-muted)] hover:border-[var(--color-primary)]/40 hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            }`}
          >
            {t.icon}
            {/* The count rides beside the name on the same line: at 10px the
              longest section still leaves room for it, and a second line would
              cost every key height for the benefit of two of them. */}
            <span
              aria-hidden
              className={`flex items-baseline gap-1 text-[10px] leading-none ${
                active ? "font-semibold" : "font-medium"
              }`}
            >
              <span>{t.label}</span>
              {(t.countLabel || (t.count !== undefined && t.count > 0)) && (
                <span
                  className={
                    active
                      ? "text-[9px] text-[var(--color-primary)]/70"
                      : "text-[9px] text-[var(--color-text-muted)]"
                  }
                >
                  {t.countLabel ?? t.count}
                </span>
              )}
            </span>
          </button>
        );
      })}
    </div>
  ) : (
    /* Tabs stay put while a drill-down is open: leaving a playbook is one
       click on the section you came from, not a hunt for a back button. */
    <div
      role="tablist"
      aria-label="Sections"
      className="-mt-1 mb-4 flex flex-wrap items-center gap-1 border-b border-[var(--color-border)] pb-2"
    >
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={activeTab === t.id}
          onClick={() => openTab(t.id)}
          className={`flex items-center gap-1.5 rounded px-2.5 py-1.5 text-xs transition-colors ${
            activeTab === t.id
              ? "bg-[var(--color-surface-hover)] font-medium text-[var(--color-text)]"
              : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          }`}
        >
          {t.icon}
          {t.label}
          {(t.countLabel || (t.count !== undefined && t.count > 0)) && (
            <span
              title={t.countTitle}
              className="text-[10px] text-[var(--color-text-muted)]"
            >
              {t.countLabel ?? t.count}
            </span>
          )}
        </button>
      ))}
    </div>
  );

  const body = (
    <>
      {isLoading && (
        <div className="flex items-center gap-2 py-8 text-xs text-[var(--color-text-muted)]">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Reading the brain…
        </div>
      )}
      {error && !brain && (
        <p className="py-8 text-xs text-[var(--color-red)]">
          {error instanceof Error
            ? error.message
            : "Could not read this agent."}
        </p>
      )}

      {brain &&
        (creating === "skill" ? (
          <SkillEditor
            slug={slug}
            onDirtyChange={markDirty}
            onDone={() => {
              refresh();
              leaveEditor();
            }}
            onCancel={leaveEditor}
          />
        ) : creating === "memory" ? (
          <MemoryEditor
            slug={slug}
            onDirtyChange={markDirty}
            onDone={() => {
              refresh();
              leaveEditor();
            }}
            onCancel={leaveEditor}
          />
        ) : reading ? (
          <BodyReader
            slug={slug}
            reading={reading}
            editing={editing}
            onEdit={() => setEditing(true)}
            onDirtyChange={markDirty}
            onSaved={() => {
              refresh();
              leaveEditor();
            }}
            onCancelEdit={leaveEditor}
            onBack={() => {
              setReading(null);
              leaveEditor();
            }}
          />
        ) : (
          <>
            {activeTab === "brain" && (
              <BrainTab
                brain={brain}
                slug={slug}
                editing={editing}
                onEdit={() => setEditing(true)}
                onDone={leaveEditor}
                onDirtyChange={markDirty}
              />
            )}
            {activeTab === "skills" && (
              <SkillsTab
                brain={brain}
                slug={slug}
                onRuled={refresh}
                onOpen={(card) => setReading({ kind: "skill", card })}
                onEdit={(card) => {
                  setReading({ kind: "skill", card });
                  setEditing(true);
                }}
                onDelete={(card) => setDeleting({ kind: "skill", card })}
                onCreate={() => setCreating("skill")}
                onMute={(name, muted) =>
                  muteMut.mutate({ kind: "skill", name, muted })
                }
                onAskAgent={onAskAgent}
                onGoToRoutine={(name) => {
                  if (onOpenRoutine) {
                    onOpenRoutine(name);
                  } else {
                    openTab("routines");
                  }
                }}
              />
            )}
            {activeTab === "memories" && (
              <MemoriesTab
                brain={brain}
                onOpen={(card) => setReading({ kind: "memory", card })}
                onEdit={(card) => {
                  setReading({ kind: "memory", card });
                  setEditing(true);
                }}
                onDelete={(card) => setDeleting({ kind: "memory", card })}
                onCreate={() => setCreating("memory")}
              />
            )}
            {activeTab === "tools" && (
              <ToolsTab
                brain={brain}
                onMute={(name, muted) =>
                  muteMut.mutate({ kind: "tool", name, muted })
                }
                onAskAgent={onAskAgent}
              />
            )}
            {activeTab === "strategies" && (
              <AgentStrategies slug={slug} dense={rail} />
            )}
            {activeTab === "routines" && (
              <RoutinesTab
                brain={brain}
                action={routinesAction}
                onOpen={onOpenRoutine}
                onMute={(name, muted) =>
                  muteMut.mutate({ kind: "routine", name, muted })
                }
                onAskAgent={onAskAgent}
              />
            )}
            {activeTab === "activity" && <ActivityFeed agent={slug} />}
          </>
        ))}

      <ConfirmDialog
        open={!!deleting}
        title={deleting?.kind === "memory" ? "Forget this" : "Delete Playbook"}
        isPending={deleteMut.isPending}
        isError={deleteMut.isError}
        errorText={
          deleteMut.error instanceof Error
            ? deleteMut.error.message
            : "Could not delete this."
        }
        onConfirm={() => deleteMut.mutate()}
        onClose={() => setDeleting(null)}
      >
        {deleting?.kind === "memory" ? (
          <>
            Make this agent forget{" "}
            <strong className="text-[var(--color-text)]">
              {deleting.card.name}
            </strong>
            ? It drops out of every future conversation.
          </>
        ) : (
          <>
            Delete{" "}
            <strong className="text-[var(--color-text)]">
              {deleting?.kind === "skill" ? deleting.card.name : ""}
            </strong>
            ? The agent stops reading it. This cannot be undone.
          </>
        )}
      </ConfirmDialog>

      {/* An unsaved editor is worth flagging even where the host cannot guard
          its own close — a tab click drops the text without ceremony. */}
      {dirty && (
        <p className="mt-3 text-[10px] text-amber-400">Unsaved changes.</p>
      )}
    </>
  );

  // The rail is beside its body and both scroll independently, so a long
  // AGENT.md never scrolls the sections out of reach. The strip is above it,
  // where the page's own scroll is the only one.
  return rail ? (
    <div className="flex min-h-0 flex-1 overflow-hidden">
      <div className="min-w-0 flex-1 overflow-y-auto px-3 py-2">{body}</div>
      {nav}
    </div>
  ) : (
    <div>
      {nav}
      {body}
    </div>
  );
}

// ── Shared bits ──

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <p className="py-8 text-center text-xs text-[var(--color-text-muted)]">
      {children}
    </p>
  );
}

function Chip({
  children,
  title,
  tone = "muted",
  onClick,
}: {
  children: React.ReactNode;
  title?: string;
  tone?: "muted" | "accent" | "warn";
  onClick?: () => void;
}) {
  const tones = {
    muted: "border-[var(--color-border)] text-[var(--color-text-muted)]",
    accent: "border-[var(--color-primary)]/40 text-[var(--color-primary)]",
    warn: "border-amber-500/40 text-amber-400",
  };
  const cls = `shrink-0 rounded border bg-[var(--color-surface)] px-1.5 py-px text-[10px] ${tones[tone]}`;
  if (onClick) {
    return (
      <button
        type="button"
        title={title}
        onClick={(e) => {
          e.stopPropagation();
          onClick();
        }}
        className={`${cls} cursor-pointer hover:brightness-125`}
      >
        {children}
      </button>
    );
  }
  return (
    <span title={title} className={cls}>
      {children}
    </span>
  );
}

/**
 * The small on/off switch a row pins left of its edit and delete buttons.
 *
 * `on` is "the agent gets this", not "this is muted": the switch reads the way
 * every switch reads, and the caller inverts once at the seam rather than the
 * eye inverting on every row.
 */
function Switch({
  on,
  onChange,
  title,
}: {
  on: boolean;
  onChange: () => void;
  title: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      title={title}
      aria-label={title}
      onClick={(e) => {
        e.stopPropagation();
        onChange();
      }}
      className={`mt-0.5 flex h-3.5 w-6 shrink-0 items-center rounded-full border p-px transition-colors ${
        on
          ? "border-[var(--color-primary)]/50 bg-[var(--color-primary)]/30"
          : "border-[var(--color-border)] bg-[var(--color-surface-hover)]"
      }`}
    >
      <span
        className={`h-2.5 w-2.5 rounded-full transition-transform ${
          on
            ? "translate-x-[10px] bg-[var(--color-primary)]"
            : "translate-x-0 bg-[var(--color-text-muted)]"
        }`}
      />
    </button>
  );
}

/**
 * A list row: a title line, badges, prose, and — when the thing is writable —
 * edit and delete beside it.
 *
 * The row's own click opens it for reading, so the actions are siblings of that
 * button rather than nested inside it.
 *
 * `toggle` is the mute switch (FEAT-090). Unlike edit and delete it is always
 * visible rather than hover-revealed, because it renders *state* and not an
 * action: a row you cannot see the switch on cannot tell you it is off.
 *
 * `onAsk` is the third action (FEAT-092) and rides in the same hover cluster,
 * left of edit so the destructive one stays rightmost. It hands the row to the
 * agent that owns it instead of to a form: some of these rows have no body to
 * edit at all, and the ones that do are usually better revised by the thing
 * that reads them.
 */
function Row({
  title,
  badges,
  subtitle,
  onClick,
  onAsk,
  onEdit,
  onDelete,
  askTitle,
  editTitle,
  deleteTitle,
  toggle,
  dimmed,
}: {
  title: string;
  badges?: React.ReactNode;
  subtitle?: string;
  onClick?: () => void;
  onAsk?: () => void;
  onEdit?: () => void;
  onDelete?: () => void;
  askTitle?: string;
  editTitle?: string;
  deleteTitle?: string;
  toggle?: { on: boolean; onChange: () => void; title: string };
  /** Render the row's content faded — it is off, not gone. */
  dimmed?: boolean;
}) {
  const inner = (
    <>
      <div className="flex items-center gap-1.5">
        <span className="min-w-0 truncate text-xs font-medium text-[var(--color-text)]">
          {title}
        </span>
        {badges}
      </div>
      {subtitle && (
        <p className="mt-0.5 text-[11px] leading-snug text-[var(--color-text-muted)]">
          {subtitle}
        </p>
      )}
    </>
  );

  return (
    <div className="group flex items-start gap-1 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 transition-colors focus-within:border-[var(--color-primary)]/40 hover:border-[var(--color-primary)]/40">
      {onClick ? (
        <button
          onClick={onClick}
          className={`min-w-0 flex-1 text-left ${dimmed ? "opacity-60" : ""}`}
        >
          {inner}
        </button>
      ) : (
        <div className={`min-w-0 flex-1 ${dimmed ? "opacity-60" : ""}`}>
          {inner}
        </div>
      )}
      {toggle && <Switch {...toggle} />}
      {(onAsk || onEdit || onDelete) && (
        <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
          {onAsk && (
            <button
              onClick={onAsk}
              title={askTitle || "Ask the agent about this"}
              className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-primary)]"
            >
              <Wand2 className="h-3 w-3" />
            </button>
          )}
          {onEdit && (
            <button
              onClick={onEdit}
              title={editTitle || "Edit"}
              className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            >
              <Pencil className="h-3 w-3" />
            </button>
          )}
          {onDelete && (
            <button
              onClick={onDelete}
              title={deleteTitle || "Delete"}
              className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-red)]/10 hover:text-[var(--color-red)]"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** The button that turns a rendered section into an editable one. */
function EditButton({
  onClick,
  label = "Edit",
}: {
  onClick: () => void;
  label?: string;
}) {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-1 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-[11px] text-[var(--color-text-muted)] transition-colors hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
    >
      <Pencil className="h-3 w-3" /> {label}
    </button>
  );
}

/** The `+ New …` button above a library. */
function AddButton({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      className="flex shrink-0 items-center gap-1 rounded border border-dashed border-[var(--color-border)] px-2 py-1 text-[11px] text-[var(--color-text-muted)] transition-colors hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
    >
      <Plus className="h-3 w-3" /> {label}
    </button>
  );
}

/** One labelled single-line input, for a playbook's or memory's metadata. */
function Field({
  label,
  value,
  onChange,
  placeholder,
  hint,
  mono,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  hint?: string;
  mono?: boolean;
}) {
  return (
    <div>
      <label className="mb-1 block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
        {label}
      </label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        spellCheck={false}
        className={`w-full rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1.5 text-xs text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none transition-colors focus:border-[var(--color-primary)] ${
          mono ? "font-mono" : ""
        }`}
      />
      {hint && (
        <p className="mt-0.5 text-[10px] text-[var(--color-text-muted)]">
          {hint}
        </p>
      )}
    </div>
  );
}

/**
 * The chrome every entity editor shares: a title, Save/Cancel, and the error a
 * refused write comes back with.
 *
 * The stores answer a refusal — an inherited playbook, a missing required field
 * — as a 400 carrying their own message, so this surfaces the server's sentence
 * rather than inventing one.
 */
function EditorShell({
  title,
  canSave,
  isPending,
  error,
  onSave,
  onCancel,
  children,
}: {
  title: string;
  canSave: boolean;
  isPending: boolean;
  error: unknown;
  onSave: () => void;
  onCancel: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          {title}
        </h3>
        <div className="flex items-center gap-2">
          <button
            onClick={onCancel}
            className="flex items-center gap-1 rounded px-2 py-1.5 text-[11px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
          >
            <X className="h-3 w-3" /> Cancel
          </button>
          <button
            onClick={onSave}
            disabled={!canSave || isPending}
            className="flex items-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-3 py-1.5 text-xs font-semibold text-white transition-all disabled:opacity-30"
          >
            <Save className="h-3.5 w-3.5" />
            {isPending ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
      {error != null && (
        <div className="rounded-md border border-[var(--color-red)]/40 bg-[var(--color-red)]/10 px-3 py-2 text-xs text-[var(--color-red)]">
          {error instanceof Error ? error.message : "Save failed"}
        </div>
      )}
      {children}
    </div>
  );
}

/** The body textarea shared by the skill and memory editors. */
function BodyArea({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <textarea
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      spellCheck={false}
      className="min-h-[320px] w-full resize-y rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 font-mono text-xs leading-relaxed text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none transition-colors focus:border-[var(--color-primary)]/50"
    />
  );
}

// ── Tabs ──

/**
 * What the panel says to the agent when a row's wand is clicked (FEAT-092).
 *
 * One sentence per kind, naming the item you were looking at — because the
 * point of the action is that you do not have to describe which one you meant.
 * The two kinds with a body also name the tool that would apply the change,
 * which is what turns "chat about it" into "change it". A tool row has no body
 * to edit, so its sentence asks about the tool rather than offering to rewrite
 * it.
 */
const OPENER = {
  skill: (slug: string) =>
    `Revise your \`${slug}\` playbook. Read it with manage_skill(action="read", name="${slug}") first, tell me what you would change and why, then apply it with manage_skill(action="edit").`,
  /**
   * An inherited playbook is the shared library's and the store refuses the
   * write, so the opener names the documented fix — shadow it locally with one
   * of its own under the same name — rather than sending the agent at a wall.
   */
  inheritedSkill: (slug: string) =>
    `Revise your \`${slug}\` playbook. It is inherited from the shared library, so it is read-only for you — read it with manage_skill(action="read", name="${slug}"), tell me what you would change and why, then shadow it locally by creating your own playbook with the same name via manage_skill(action="create").`,
  routine: (name: string) =>
    `Improve the \`${name}\` routine. Read it, tell me what you would change and why, then apply it and test it with manage_routines(action="run").`,
  tool: (name: string) =>
    `Explain how you use the \`${name}\` tool and when you reach for it — and tell me whether you actually need it.`,
};

/**
 * The body of a markdown file with front matter, if it has any.
 *
 * Anchored at the very start and closed by the first lone `---` on its own
 * line, which is YAML's own rule — a horizontal rule further down the document
 * is prose and stays.
 */
function withoutFrontMatter(text: string): string {
  const match = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/.exec(text);
  return (match ? text.slice(match[0].length) : text).trim();
}

function BrainTab({
  brain,
  slug,
  editing,
  onEdit,
  onDone,
  onDirtyChange,
}: {
  brain: AgentBrain;
  slug: string;
  editing: boolean;
  onEdit: () => void;
  onDone: () => void;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const queryClient = useQueryClient();

  if (editing) {
    return (
      <div>
        <button
          onClick={onDone}
          className="mb-3 flex items-center gap-1 text-[11px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
        >
          <ArrowLeft className="h-3 w-3" /> Back to the brain
        </button>
        <MarkdownEditor
          label="Brain"
          sublabel="AGENT.md — identity & domain knowledge"
          content={brain.agent_md}
          // The agent page reads the same file under its own key, so a save
          // here has to reach both or the page keeps showing the old text.
          onSave={(value) =>
            api
              .updateAgentMd(slug, value)
              .then(() =>
                queryClient.invalidateQueries({ queryKey: ["agent", slug] }),
              )
          }
          invalidateKey={["agent-brain", slug]}
          onDirtyChange={onDirtyChange}
        />
      </div>
    );
  }

  // The file's front matter is the agent's record, not its prose: name,
  // description, agent_key, server_name, created_by — every field of it is
  // either said by the header above or set by a control there, and markdown
  // renders the block as one run-on paragraph of `key: value` at the top of
  // the thing you came here to read. The editor still gets the whole file; a
  // reader gets what the model is actually told.
  const prose = withoutFrontMatter(brain.agent_md);

  // No identity chips here — the slug, the model and the server were all said
  // twice on every screen this tab appears on. The agent's page carries them in
  // its own header, three lines above; the chat's panel carries the live pair
  // in its bar, one line above. A row that repeats the line above it is not
  // context, it is the same sentence at a smaller size.
  return (
    <div>
      {brain.when_to_consult && (
        <div className="mb-4 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2">
          <p className="mb-0.5 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
            When Condor routes to it
          </p>
          <p className="text-xs text-[var(--color-text)]">
            {brain.when_to_consult}
          </p>
        </div>
      )}

      <div className="mb-1 flex items-center justify-between gap-3">
        <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
          AGENT.md — identity &amp; domain knowledge
        </p>
        <EditButton onClick={onEdit} />
      </div>
      {prose ? (
        <div className="chat-markdown text-xs text-[var(--color-text)]">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{prose}</ReactMarkdown>
        </div>
      ) : (
        <Empty>This agent has no AGENT.md yet.</Empty>
      )}
    </div>
  );
}

/**
 * The playbook the agent offered, above the library it is not in yet (FEAT-074).
 *
 * The agent worked a procedure out in a conversation and proposed it; it has
 * been written nowhere the agent can read, because a skill is per-agent and
 * reaches every future prompt of everyone using it. So this card is the accept:
 * until somebody clicks it the injected index is exactly what it was.
 *
 * Dashed, like the `+ New playbook` button beside it, because both are the same
 * statement — a playbook that does not exist yet.
 */
function ProposedSkill({
  slug,
  proposal,
  onRuled,
}: {
  slug: string;
  proposal: SkillProposal;
  onRuled: () => void;
}) {
  const [open, setOpen] = useState(false);
  const accept = useMutation({
    mutationFn: () => api.acceptAgentSkillProposal(slug),
    onSuccess: onRuled,
  });
  const discard = useMutation({
    mutationFn: () => api.discardAgentSkillProposal(slug),
    onSuccess: onRuled,
  });
  const busy = accept.isPending || discard.isPending;
  const failure = accept.error || discard.error;

  return (
    <div className="rounded border border-dashed border-[var(--color-primary)]/40 bg-[var(--color-surface)] px-3 py-2">
      <div className="flex items-center gap-1.5 text-[10px] text-[var(--color-primary)]">
        <Sparkles className="h-3 w-3" /> Proposed from a conversation
      </div>
      <p className="mt-1 text-xs font-medium text-[var(--color-text)]">
        {proposal.name}
      </p>
      <p className="mt-0.5 text-[11px] leading-snug text-[var(--color-text-muted)]">
        {proposal.when_to_use || proposal.description}
      </p>

      <button
        onClick={() => setOpen((was) => !was)}
        className="mt-1.5 flex items-center gap-1 text-[11px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
      >
        {open ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
        {open ? "Hide the steps" : "Read the steps"}
      </button>
      {open && (
        <div className="chat-markdown mt-2 text-xs text-[var(--color-text)]">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {proposal.body}
          </ReactMarkdown>
        </div>
      )}

      <div className="mt-2 flex items-center gap-1.5">
        <button
          onClick={() => accept.mutate()}
          disabled={busy}
          className="flex items-center gap-1 rounded border border-[var(--color-primary)]/50 px-2 py-1 text-[11px] text-[var(--color-primary)] transition-colors hover:bg-[var(--color-primary)]/10 disabled:opacity-50"
        >
          {accept.isPending ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Check className="h-3 w-3" />
          )}
          Accept
        </button>
        <button
          onClick={() => discard.mutate()}
          disabled={busy}
          className="flex items-center gap-1 rounded border border-[var(--color-border)] px-2 py-1 text-[11px] text-[var(--color-text-muted)] transition-colors hover:border-[var(--color-red)]/50 hover:text-[var(--color-red)] disabled:opacity-50"
        >
          <X className="h-3 w-3" /> Discard
        </button>
      </div>
      {failure && (
        <p className="mt-1.5 text-[11px] text-[var(--color-red)]">
          {failure instanceof Error ? failure.message : "Could not do that."}
        </p>
      )}
    </div>
  );
}

function SkillsTab({
  brain,
  slug,
  onOpen,
  onEdit,
  onDelete,
  onCreate,
  onRuled,
  onGoToRoutine,
  onMute,
  onAskAgent,
}: {
  brain: AgentBrain;
  slug: string;
  onOpen: (skill: SkillCard) => void;
  onEdit: (skill: SkillCard) => void;
  onDelete: (skill: SkillCard) => void;
  onCreate: () => void;
  onRuled: () => void;
  onGoToRoutine?: (name: string) => void;
  /** Switch a playbook off for this agent, or back on (FEAT-090). */
  onMute?: (slug: string, muted: boolean) => void;
  /** Put the revision to the agent that owns it (FEAT-092). */
  onAskAgent?: (text: string) => void;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-start justify-between gap-3">
        <p className="text-[11px] text-[var(--color-text-muted)]">
          Playbooks the agent reads before hand-rolling a known flow. Click one
          to read it exactly as the agent does. Switch one off and it leaves
          this agent's context — from its next tick or next session.
        </p>
        <AddButton onClick={onCreate} label="New playbook" />
      </div>
      {brain.skill_proposal && (
        <ProposedSkill
          slug={slug}
          proposal={brain.skill_proposal}
          onRuled={onRuled}
        />
      )}
      {brain.skills.length === 0 ? (
        // A pending proposal is already the whole tab: an empty-library notice
        // under its card reads as a contradiction.
        brain.skill_proposal ? null : (
          <Empty>No playbooks in this agent's library.</Empty>
        )
      ) : (
        brain.skills.map((s) => (
          <Row
            key={s.slug}
            title={s.name}
            badges={
              <>
                {s.muted && (
                  <Chip title="Switched off — this agent is never told it exists">
                    muted
                  </Chip>
                )}
                {s.shared && (
                  <Chip
                    title={
                      s.inherited
                        ? "From the shared library — read-only here"
                        : "In the shared library"
                    }
                  >
                    shared
                  </Chip>
                )}
                {s.references_routine && (
                  <Chip
                    tone={s.routine_ok ? "accent" : "warn"}
                    title={
                      s.routine_ok
                        ? "Click to open this routine"
                        : "This playbook points at a routine that no longer exists"
                    }
                    onClick={
                      s.routine_ok && onGoToRoutine
                        ? () => onGoToRoutine(s.references_routine!)
                        : undefined
                    }
                  >
                    → {s.references_routine}
                  </Chip>
                )}
              </>
            }
            subtitle={s.when_to_use || s.description}
            onClick={() => onOpen(s)}
            // An inherited playbook is the shared library's, not this agent's:
            // the store refuses the write, so the buttons are not offered.
            onEdit={s.inherited ? undefined : () => onEdit(s)}
            onDelete={s.inherited ? undefined : () => onDelete(s)}
            // Offered on an inherited playbook too, with a different sentence:
            // the agent cannot edit that one in place, but shadowing it locally
            // is exactly the kind of thing the conversation is for.
            onAsk={
              onAskAgent &&
              (() =>
                onAskAgent(
                  s.inherited
                    ? OPENER.inheritedSkill(s.slug)
                    : OPENER.skill(s.slug),
                ))
            }
            askTitle={
              s.inherited
                ? "Ask the agent to specialize this shared playbook"
                : "Ask the agent to revise this playbook"
            }
            editTitle="Edit this playbook"
            deleteTitle="Delete this playbook"
            dimmed={s.muted}
            // Offered on an inherited playbook too: a mute is per-agent, so
            // switching a shared one off here leaves every other agent's alone.
            toggle={
              onMute && {
                on: !s.muted,
                onChange: () => onMute(s.slug, !s.muted),
                title: s.muted
                  ? "Off for this agent — switch it back on"
                  : "On — switch it off for this agent",
              }
            }
          />
        ))
      )}
    </div>
  );
}

function MemoriesTab({
  brain,
  onOpen,
  onEdit,
  onDelete,
  onCreate,
}: {
  brain: AgentBrain;
  onOpen: (memory: MemoryCard) => void;
  onEdit: (memory: MemoryCard) => void;
  onDelete: (memory: MemoryCard) => void;
  onCreate: () => void;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-start justify-between gap-3">
        <p className="text-[11px] text-[var(--color-text-muted)]">
          What this agent remembers about you — yours alone, and separate from
          every other agent's.
        </p>
        <AddButton onClick={onCreate} label="New memory" />
      </div>
      {brain.memories.length === 0 ? (
        <Empty>Nothing remembered in this domain yet.</Empty>
      ) : (
        brain.memories.map((m) => (
          <Row
            key={m.name}
            title={m.name}
            badges={<Chip title="Memory type">{m.type}</Chip>}
            subtitle={m.description}
            onClick={() => onOpen(m)}
            onEdit={() => onEdit(m)}
            onDelete={() => onDelete(m)}
            editTitle="Edit this memory"
            deleteTitle="Forget this"
          />
        ))
      )}
    </div>
  );
}

/** The two MCP subprocesses a seat mounts, in the order the panel groups them. */
const TOOL_SERVERS: { id: string; label: string }[] = [
  { id: "condor", label: "Condor" },
  { id: "hummingbot", label: "Hummingbot" },
];

/**
 * Every tool this agent's seat actually mounts, each with a switch (FEAT-091).
 *
 * Not the AGENT.md allowlist. That list only binds pydantic-ai model keys — an
 * ACP bridge runs unrestricted — so a tab that only echoed it was telling most
 * agents something untrue about what they can reach. The switch is the honest
 * control: a muted tool is never registered on the subprocess, so the model is
 * never told it exists, on every backend alike.
 */
function ToolsTab({
  brain,
  onMute,
  onAskAgent,
}: {
  brain: AgentBrain;
  /** Switch a tool off for this agent, or back on. */
  onMute?: (name: string, muted: boolean) => void;
  /** Ask the agent about this tool — it has no body to edit (FEAT-092). */
  onAskAgent?: (text: string) => void;
}) {
  const grouped = TOOL_SERVERS.map((server) => ({
    ...server,
    tools: brain.tools.filter((t) => t.server === server.id),
  })).filter((group) => group.tools.length > 0);

  return (
    <div className="space-y-1.5">
      <p className="text-[11px] text-[var(--color-text-muted)]">
        Every tool this agent's seat mounts. Switch one off and the next session
        never registers it — the model is not told it exists. Changes apply to
        the next session this agent starts.
      </p>
      <p className="text-[11px] text-[var(--color-text-muted)]">
        {brain.tools_unrestricted
          ? "AGENT.md names no allowlist, so nothing narrows this further. Naming tools there also narrows what the agent may call — edit it in the Brain tab, where it is written."
          : "AGENT.md also names an allowlist, marked below. Edit it in the Brain tab, where it is written."}
      </p>
      {brain.tools.length === 0 ? (
        <Empty>No tools mounted on this agent's seat.</Empty>
      ) : (
        grouped.map((group) => (
          <div key={group.id} className="space-y-1.5 pt-1">
            <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
              {group.label}
            </p>
            {group.tools.map((t) => (
              <Row
                key={`${t.server}:${t.name}`}
                title={t.name}
                badges={
                  <>
                    {t.muted && (
                      <Chip title="Switched off — this agent is never told it exists">
                        muted
                      </Chip>
                    )}
                    {t.allowlisted && (
                      <Chip title="Named in the AGENT.md tool allowlist">
                        allowlisted
                      </Chip>
                    )}
                  </>
                }
                subtitle={t.description}
                dimmed={t.muted}
                onAsk={onAskAgent && (() => onAskAgent(OPENER.tool(t.name)))}
                askTitle="Ask the agent how it uses this tool"
                toggle={
                  onMute && {
                    on: !t.muted,
                    onChange: () => onMute(t.name, !t.muted),
                    title: t.muted
                      ? "Off for this agent — switch it back on"
                      : "On — switch it off for this agent",
                  }
                }
              />
            ))}
          </div>
        ))
      )}
    </div>
  );
}

function RoutinesTab({
  brain,
  action,
  onOpen,
  onMute,
  onAskAgent,
}: {
  brain: AgentBrain;
  action?: React.ReactNode;
  /** Hand this routine to the host's own library, if it has one (FEAT-081). */
  onOpen?: (routineName: string) => void;
  /** Switch a routine off for this agent, or back on (FEAT-090). */
  onMute?: (name: string, muted: boolean) => void;
  /** Put the improvement to the agent that runs it (FEAT-092). */
  onAskAgent?: (text: string) => void;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-start justify-between gap-3">
        <p className="text-[11px] text-[var(--color-text-muted)]">
          Scripts this agent can run on demand or on a schedule. Its own library
          first, then the shared one every agent reads. Switching one off takes
          it out of this agent from its next tick or session — /routines still
          lists and runs it.
        </p>
        {action}
      </div>
      {brain.routines.length === 0 ? (
        <Empty>No routines this agent can run.</Empty>
      ) : (
        brain.routines.map((r) => (
          <Row
            key={r.name}
            title={formatRoutineName(r.name)}
            badges={
              <>
                {r.muted && (
                  <Chip title="Switched off — this agent cannot run it">
                    muted
                  </Chip>
                )}
                {r.continuous && (
                  <Chip title="Runs in a loop until stopped">
                    ♾️ continuous
                  </Chip>
                )}
                {r.source === "global" && brain.slug !== "condor" && (
                  <Chip title="From the shared library every agent reads">
                    shared
                  </Chip>
                )}
              </>
            }
            subtitle={r.description}
            onClick={onOpen && (() => onOpen(r.name))}
            dimmed={r.muted}
            // The routine's real name, not the title-cased one the row shows:
            // this sentence is read by the agent, which addresses it by the
            // name `manage_routines` takes.
            onAsk={onAskAgent && (() => onAskAgent(OPENER.routine(r.name)))}
            askTitle="Ask the agent to improve this routine"
            toggle={
              onMute && {
                on: !r.muted,
                onChange: () => onMute(r.name, !r.muted),
                title: r.muted
                  ? "Off for this agent — switch it back on"
                  : "On — switch it off for this agent",
              }
            }
          />
        ))
      )}
    </div>
  );
}

// ── Editors ──

/**
 * A playbook, written.
 *
 * One form for both doors: with no `existing` it creates in the agent's own
 * library, with one it patches that playbook in place. The `shared` flag is
 * absent by design — publishing to every assistant is Condor's own decision,
 * not a checkbox on a panel.
 */
function SkillEditor({
  slug,
  existing,
  onDirtyChange,
  onDone,
  onCancel,
}: {
  slug: string;
  existing?: SkillBody;
  onDirtyChange: (dirty: boolean) => void;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(existing?.name ?? "");
  const [description, setDescription] = useState(existing?.description ?? "");
  const [whenToUse, setWhenToUse] = useState(existing?.when_to_use ?? "");
  const [routine, setRoutine] = useState(existing?.references_routine ?? "");
  const [body, setBody] = useState(existing?.body ?? "");

  const touch =
    <T,>(set: (v: T) => void) =>
    (v: T) => {
      set(v);
      onDirtyChange(true);
    };

  const save = useMutation({
    // The two calls answer with different shapes and neither is read — the
    // panel refetches the catalog instead.
    mutationFn: (): Promise<unknown> =>
      existing
        ? api.updateAgentSkill(slug, existing.slug, {
            description,
            when_to_use: whenToUse,
            body,
            // Only sent when it actually moved: "" is a request to unlink, and
            // sending it unchanged on every save would be one too.
            ...(routine !== (existing.references_routine ?? "")
              ? { references_routine: routine }
              : {}),
          })
        : api.createAgentSkill(slug, {
            name,
            description,
            when_to_use: whenToUse,
            body,
            references_routine: routine,
          }),
    onSuccess: onDone,
  });

  // The store requires all four on create; an edit may touch one field.
  const canSave = existing
    ? true
    : !!(name.trim() && description.trim() && whenToUse.trim() && body.trim());

  return (
    <EditorShell
      title={existing ? `Editing ${existing.name}` : "New playbook"}
      canSave={canSave}
      isPending={save.isPending}
      error={save.error}
      onSave={() => save.mutate()}
      onCancel={onCancel}
    >
      {!existing && (
        <Field
          label="Name"
          value={name}
          onChange={touch(setName)}
          placeholder="e.g. Rebalance an LP position"
          hint="Slugified into the library — this becomes the playbook's id."
          mono
        />
      )}
      <Field
        label="Description"
        value={description}
        onChange={touch(setDescription)}
        placeholder="What this playbook does, in one line"
      />
      <Field
        label="When to use"
        value={whenToUse}
        onChange={touch(setWhenToUse)}
        placeholder="The situation that should make the agent reach for it"
        hint="This line is what the agent sees in its index — it decides whether the playbook gets read at all."
      />
      <Field
        label="Runs routine"
        value={routine}
        onChange={touch(setRoutine)}
        placeholder="optional — a routine name"
        hint="Named here, the agent runs that routine instead of improvising the flow. Empty unlinks it."
        mono
      />
      <div>
        <label className="mb-1 block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
          Body
        </label>
        <BodyArea
          value={body}
          onChange={touch(setBody)}
          placeholder="The steps, in markdown — written for the agent to follow."
        />
      </div>
    </EditorShell>
  );
}

/**
 * A memory, written by hand.
 *
 * Scoped to you and this agent, the same as the ones it writes itself: what you
 * put here reaches its prompt exactly the way `manage_memory` would have.
 */
function MemoryEditor({
  slug,
  existing,
  body: initialBody,
  onDirtyChange,
  onDone,
  onCancel,
}: {
  slug: string;
  existing?: MemoryCard;
  body?: string;
  onDirtyChange: (dirty: boolean) => void;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(existing?.name ?? "");
  const [description, setDescription] = useState(existing?.description ?? "");
  const [type, setType] = useState(existing?.type ?? "fact");
  const [body, setBody] = useState(initialBody ?? "");

  const touch =
    <T,>(set: (v: T) => void) =>
    (v: T) => {
      set(v);
      onDirtyChange(true);
    };

  const save = useMutation({
    mutationFn: () =>
      api.saveAgentMemory(slug, existing?.name || name, {
        content: body,
        description,
        type,
      }),
    onSuccess: onDone,
  });

  const canSave = !!(
    (existing?.name || name.trim()) &&
    description.trim() &&
    body.trim()
  );

  return (
    <EditorShell
      title={existing ? `Editing ${existing.name}` : "New memory"}
      canSave={canSave}
      isPending={save.isPending}
      error={save.error}
      onSave={() => save.mutate()}
      onCancel={onCancel}
    >
      {!existing && (
        <Field
          label="Name"
          value={name}
          onChange={touch(setName)}
          placeholder="e.g. favourite-pair"
          hint="Slugified — writing an existing name overwrites that memory."
          mono
        />
      )}
      <Field
        label="Description"
        value={description}
        onChange={touch(setDescription)}
        placeholder="One line — this is what the agent scans to decide it is relevant"
      />
      <div>
        <label className="mb-1 block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
          Type
        </label>
        <select
          value={type}
          onChange={(e) => touch(setType)(e.target.value)}
          className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1.5 text-xs text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)]"
        >
          {MEMORY_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="mb-1 block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
          Body
        </label>
        <BodyArea
          value={body}
          onChange={touch(setBody)}
          placeholder="The fact itself."
        />
      </div>
    </EditorShell>
  );
}

// ── Drill-down ──

/**
 * One playbook or memory — read in full, or edited in place.
 *
 * Read mode shows the same text the agent gets back from `manage_skill` /
 * `manage_memory`. Edit mode is the same body plus the metadata that decides
 * whether the agent ever reaches for it, which is why the two live at one
 * address rather than in separate places.
 */
function BodyReader({
  slug,
  reading,
  editing,
  onEdit,
  onDirtyChange,
  onSaved,
  onCancelEdit,
  onBack,
}: {
  slug: string;
  reading: Reading;
  editing: boolean;
  onEdit: () => void;
  onDirtyChange: (dirty: boolean) => void;
  onSaved: () => void;
  onCancelEdit: () => void;
  onBack: () => void;
}) {
  const name = reading.kind === "skill" ? reading.card.slug : reading.card.name;

  const { data, isLoading, error } = useQuery<MemoryBody | SkillBody>({
    queryKey: ["agent-brain-body", slug, reading.kind, name],
    queryFn: () =>
      reading.kind === "skill"
        ? api.getAgentSkill(slug, name)
        : api.getAgentMemory(slug, name),
    staleTime: 30_000,
  });

  // The kind is in the query key, so the shape follows it — a memory's payload
  // can never be sitting under a skill's key.
  const skill =
    reading.kind === "skill" ? (data as SkillBody | undefined) : undefined;

  const back = (
    <button
      onClick={onBack}
      className="mb-3 flex items-center gap-1 text-[11px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
    >
      <ArrowLeft className="h-3 w-3" />
      Back to {reading.kind === "skill" ? "skills" : "memories"}
    </button>
  );

  // An editor needs the body, so editing waits for the same fetch reading does.
  if (editing) {
    if (isLoading || !data)
      return (
        <div>
          {back}
          <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading…
          </div>
        </div>
      );
    return (
      <div>
        {back}
        {reading.kind === "skill" ? (
          <SkillEditor
            slug={slug}
            existing={data as SkillBody}
            onDirtyChange={onDirtyChange}
            onDone={onSaved}
            onCancel={onCancelEdit}
          />
        ) : (
          <MemoryEditor
            slug={slug}
            existing={reading.card}
            body={data.body}
            onDirtyChange={onDirtyChange}
            onDone={onSaved}
            onCancel={onCancelEdit}
          />
        )}
      </div>
    );
  }

  const writable = reading.kind === "memory" || !reading.card.inherited;

  return (
    <div>
      {back}

      <div className="flex items-start justify-between gap-3">
        <h3 className="text-sm font-semibold text-[var(--color-text)]">
          {reading.card.name}
        </h3>
        {writable && <EditButton onClick={onEdit} />}
      </div>
      {skill?.when_to_use && (
        <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">
          {skill.when_to_use}
        </p>
      )}
      {reading.kind === "skill" && reading.card.inherited && (
        <p className="mt-1 text-[11px] text-amber-400">
          From the shared library — read-only here. Create a playbook with the
          same name to specialize it.
        </p>
      )}
      {skill?.files && skill.files.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {skill.files.map((f) => (
            <Chip key={f} title="Companion file the agent can pull on demand">
              {f}
            </Chip>
          ))}
        </div>
      )}

      <div className="mt-3">
        {isLoading && (
          <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading…
          </div>
        )}
        {error && (
          <p className="text-xs text-[var(--color-red)]">
            {error instanceof Error ? error.message : "Could not read this."}
          </p>
        )}
        {data && (
          <div className="chat-markdown text-xs text-[var(--color-text)]">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {data.body}
            </ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  );
}
