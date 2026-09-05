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
  Repeat,
  Sparkles,
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
import { LoopBanner } from "@/components/agent/LoopBanner";
import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import type { KnowledgeTabId } from "@/components/agent/knowledgeTabs";
import {
  BodyReader,
  type Reading,
} from "@/components/agent/knowledge/BodyReader";
import {
  AddButton,
  Chip,
  EditButton,
  Empty,
  Row,
} from "@/components/agent/knowledge/KnowledgeChrome";
import { MemoryEditor } from "@/components/agent/knowledge/MemoryEditor";
import { SkillEditor } from "@/components/agent/knowledge/SkillEditor";
import {
  api,
  type AgentBrain,
  type MemoryCard,
  type SkillCard,
  type SkillProposal,
} from "@/lib/api";
import { formatRoutineName } from "@/lib/routineUtils";

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
  dense = false,
  tab,
  onTabChange,
  routinesAction,
  onOpenRoutine,
  onOpenStrategy,
  onAskAgent,
  onDirtyChange,
}: {
  slug: string;
  /**
   * Whether the host is a column rather than a page.
   *
   * All this decides is whether the strategy cards lay out as a grid: the
   * grid's breakpoints are the *viewport's*, so in the chat's 400–700px pane a
   * wide window put three cards side by side in a column that fits one. It used
   * to ride on a `layout` prop that said the same thing by accident; FEAT-117
   * split it out so the width is stated rather than inferred, and it outlived
   * that prop (FEAT-119) — "this is 400px wide" was always the useful half.
   */
  dense?: boolean;
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
   * Where a strategy card goes. Same bargain as `onOpenRoutine`: given, the
   * host opens it in its own surface — the chat's pane shows the same
   * workbench the page does — and absent, the card navigates to the page.
   */
  onOpenStrategy?: (strategySlug: string) => void;
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

  /**
   * The loops, for the banner above the sections.
   *
   * The same `["agent", slug]` key `AgentStrategies` reads, so the two share
   * one set of records through react-query and the banner costs no second
   * fetch when the Strategies section is open. Same conditional cadence for
   * the same reason (PERF-305): only a live loop can move a countdown, and an
   * idle agent should not buy 5s of Hummingbot round-trips to be told so.
   */
  const { data: agentDetail } = useQuery({
    queryKey: ["agent", slug],
    queryFn: () => api.getAgent(slug),
    enabled: !!slug,
    refetchInterval: (q) =>
      q.state.data?.strategies.some((s) => s.status === "running") ? 5000 : false,
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
    // A body lives under its own key, and keys match element by element — so
    // "agent-brain" never reaches "agent-brain-body". Without this the reader
    // stays mounted across a save and renders the text from before it, and
    // Edit re-opens on that stale text and overwrites the save. The prefix
    // covers every kind and name; a delete has already unmounted its reader
    // and a create has no body query open, so both only mark cache stale.
    queryClient.invalidateQueries({ queryKey: ["agent-brain-body", slug] });
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

  /** Every section change also leaves whatever drill-down or editor was open. */
  const openTab = (id: KnowledgeTabId) => {
    setTab(id);
    setReading(null);
    leaveEditor();
  };

  // A column down the *right* edge, because eight tabs wrap to three rows in a
  // 400px pane — and because in the chat this rail sits against the dock, where
  // everything else you click to open something already is. Same tabs, same
  // counts, same order, each still saying its name: an icon alone made the
  // reader learn seven glyphs to find "Tools".
  //
  // The names are set flat rather than turned on their side. Sideways text buys
  // 30px of width and charges the reader for it — a Latin word is recognised by
  // its shape, and rotating it makes you decode it letter by letter — and it
  // charges the column too: `STRATEGIES` on its side is 60px of height per key,
  // so the seven ran the full height of the pane. Upright at 10px they are
  // ~36px each, and the whole rail is a third of the column.
  const nav = (
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
              <AgentStrategies
                slug={slug}
                dense={dense}
                onOpenStrategy={onOpenStrategy}
              />
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
  // AGENT.md never scrolls the sections out of reach.
  //
  // There used to be a second arrangement — the bodies with no chrome at all,
  // for a host that drew its own navigation. The agent page was that host and
  // its spine was that navigation, and both went with FEAT-119; the rail is the
  // only way these sections are offered now, so it is not a prop any more.
  //
  // The banner spans the rail as well as the body, and sits outside the
  // scroller: "there is a loop running" must not be a fact you scroll past.
  // Only where a host can receive the click — a host that passes no
  // `onOpenStrategy` has nowhere to open the workbench, and a strip that says
  // something is running but cannot take you to it is worse than none.
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      {onOpenStrategy && (
        <LoopBanner
          strategies={agentDetail?.strategies ?? []}
          onOpenStrategy={onOpenStrategy}
        />
      )}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <div className="min-w-0 flex-1 overflow-y-auto px-3 py-2">{body}</div>
        {nav}
      </div>
    </div>
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
          this agent's context — from its next tick, or from your next message
          in a chat that is already open.
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
        Every tool this agent's seat mounts. Switch one off and the model is no
        longer told it exists — from the next tick, or from your next message in
        an open chat.
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
          it out of this agent from its next tick, or from your next message in
          a chat that is already open — /routines still lists and runs it.
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
