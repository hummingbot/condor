import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  BookOpen,
  Brain,
  Check,
  ChevronDown,
  ChevronRight,
  Loader2,
  Pencil,
  Plus,
  Repeat,
  Save,
  Server,
  Sparkles,
  Trash2,
  Wrench,
  X,
  Zap,
} from "lucide-react";
import { useCallback, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { MarkdownEditor } from "@/components/agent/AgentOverviewTab";
import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
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
  | { kind: "skill"; card: SkillCard }
  | { kind: "memory"; card: MemoryCard };

/** What `getAgentMemory` returns — a name and the body, nothing else. */
type MemoryBody = { name: string; body: string };

/** A tab a host can append (the agent page adds Delegations this way). */
export type KnowledgeTab = {
  id: string;
  label: string;
  icon: React.ReactNode;
  count?: number;
  render: () => React.ReactNode;
};

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
  slots,
  extraTabs = [],
}: {
  slug: string;
  slots: {
    /** The page's strategies grid — it creates and deletes, so it owns that tab. */
    strategies: React.ReactNode;
    /** Anything the page wants beside the routine catalog (e.g. Reports). */
    routinesAction?: React.ReactNode;
  };
  extraTabs?: KnowledgeTab[];
}) {
  const [tab, setTab] = useState<string>("brain");
  const [reading, setReading] = useState<Reading | null>(null);
  const [creating, setCreating] = useState<"skill" | "memory" | null>(null);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState<Reading | null>(null);

  const queryClient = useQueryClient();

  const { data: brain, isLoading, error } = useQuery({
    queryKey: ["agent-brain", slug],
    queryFn: () => api.getAgentBrain(slug),
    // A brain changes when someone edits it, not while you read it. Fresh on
    // open is what matters; a poll here would walk the skill and memory stores
    // on disk every few seconds for a panel nobody is looking at.
    staleTime: 30_000,
  });

  // One dirty flag for whichever editor is mounted — only ever one is.
  const [dirty, setDirty] = useState(false);
  const markDirty = useCallback((d: boolean) => setDirty(d), []);

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

  const counts = {
    skills: brain?.skills.length ?? 0,
    memories: brain?.memories.length ?? 0,
    tools: brain?.tools.length ?? 0,
    strategies: brain?.strategies.length ?? 0,
    routines: brain?.routines.length ?? 0,
  };

  const tabs: { id: string; label: string; icon: React.ReactNode; count?: number }[] = [
    { id: "brain", label: "Brain", icon: <Brain className="h-3.5 w-3.5" /> },
    {
      id: "skills",
      label: "Skills",
      icon: <BookOpen className="h-3.5 w-3.5" />,
      count: counts.skills,
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
      // Unrestricted is not zero, and a "0" next to it would say the opposite
      // of what it means — so that case carries no count at all.
      count: brain && !brain.tools_unrestricted ? counts.tools : undefined,
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
    },
    ...extraTabs.map(({ id, label, icon, count }) => ({ id, label, icon, count })),
  ];

  return (
    <div>
      {/* Tabs stay put while a drill-down is open: leaving a playbook is one
          click on the section you came from, not a hunt for a back button. */}
      <div className="-mt-1 mb-4 flex flex-wrap items-center gap-1 border-b border-[var(--color-border)] pb-2">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => {
              setTab(t.id);
              setReading(null);
              leaveEditor();
            }}
            className={`flex items-center gap-1.5 rounded px-2.5 py-1.5 text-xs transition-colors ${
              tab === t.id
                ? "bg-[var(--color-surface-hover)] font-medium text-[var(--color-text)]"
                : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            }`}
          >
            {t.icon}
            {t.label}
            {t.count !== undefined && t.count > 0 && (
              <span className="text-[10px] text-[var(--color-text-muted)]">
                {t.count}
              </span>
            )}
          </button>
        ))}
      </div>

      {isLoading && (
        <div className="flex items-center gap-2 py-8 text-xs text-[var(--color-text-muted)]">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Reading the brain…
        </div>
      )}
      {error && !brain && (
        <p className="py-8 text-xs text-[var(--color-red)]">
          {error instanceof Error ? error.message : "Could not read this agent."}
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
            {tab === "brain" && (
              <BrainTab
                brain={brain}
                slug={slug}
                editing={editing}
                onEdit={() => setEditing(true)}
                onDone={leaveEditor}
                onDirtyChange={markDirty}
              />
            )}
            {tab === "skills" && (
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
              />
            )}
            {tab === "memories" && (
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
            {tab === "tools" && <ToolsTab brain={brain} />}
            {tab === "strategies" && slots.strategies}
            {tab === "routines" && (
              <RoutinesTab brain={brain} action={slots.routinesAction} />
            )}
            {extraTabs.find((t) => t.id === tab)?.render()}
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
            <strong className="text-[var(--color-text)]">{deleting.card.name}</strong>?
            It drops out of every future conversation.
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
}: {
  children: React.ReactNode;
  title?: string;
  tone?: "muted" | "accent" | "warn";
}) {
  const tones = {
    muted: "border-[var(--color-border)] text-[var(--color-text-muted)]",
    accent: "border-[var(--color-primary)]/40 text-[var(--color-primary)]",
    warn: "border-amber-500/40 text-amber-400",
  };
  return (
    <span
      title={title}
      className={`shrink-0 rounded border bg-[var(--color-surface)] px-1.5 py-px text-[10px] ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

/**
 * A list row: a title line, badges, prose, and — when the thing is writable —
 * edit and delete beside it.
 *
 * The row's own click opens it for reading, so the actions are siblings of that
 * button rather than nested inside it.
 */
function Row({
  title,
  badges,
  subtitle,
  onClick,
  onEdit,
  onDelete,
  editTitle,
  deleteTitle,
}: {
  title: string;
  badges?: React.ReactNode;
  subtitle?: string;
  onClick?: () => void;
  onEdit?: () => void;
  onDelete?: () => void;
  editTitle?: string;
  deleteTitle?: string;
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
        <button onClick={onClick} className="min-w-0 flex-1 text-left">
          {inner}
        </button>
      ) : (
        <div className="min-w-0 flex-1">{inner}</div>
      )}
      {(onEdit || onDelete) && (
        <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
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
function EditButton({ onClick, label = "Edit" }: { onClick: () => void; label?: string }) {
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
      {hint && <p className="mt-0.5 text-[10px] text-[var(--color-text-muted)]">{hint}</p>}
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

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-1.5">
        <Chip title="Agent slug">{brain.slug}</Chip>
        {brain.agent_key ? (
          <Chip tone="accent" title="The model this agent answers on, everywhere it runs">
            {brain.agent_key}
          </Chip>
        ) : (
          <Chip title="No model pinned — it answers on the chat's default">
            chat default model
          </Chip>
        )}
        {brain.server_name && (
          <Chip title="Pinned Hummingbot API server">
            <Server className="mr-0.5 inline h-2.5 w-2.5" />
            {brain.server_name}
          </Chip>
        )}
        {!brain.server_required && <Chip title="Runs without a trading server">serverless</Chip>}
      </div>

      {brain.when_to_consult && (
        <div className="mb-4 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2">
          <p className="mb-0.5 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
            When Condor routes to it
          </p>
          <p className="text-xs text-[var(--color-text)]">{brain.when_to_consult}</p>
        </div>
      )}

      <div className="mb-1 flex items-center justify-between gap-3">
        <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
          AGENT.md — identity &amp; domain knowledge
        </p>
        <EditButton onClick={onEdit} />
      </div>
      {brain.agent_md ? (
        <div className="chat-markdown text-xs text-[var(--color-text)]">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{brain.agent_md}</ReactMarkdown>
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
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{proposal.body}</ReactMarkdown>
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
}: {
  brain: AgentBrain;
  slug: string;
  onOpen: (skill: SkillCard) => void;
  onEdit: (skill: SkillCard) => void;
  onDelete: (skill: SkillCard) => void;
  onCreate: () => void;
  onRuled: () => void;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-start justify-between gap-3">
        <p className="text-[11px] text-[var(--color-text-muted)]">
          Playbooks the agent reads before hand-rolling a known flow. Click one to
          read it exactly as the agent does.
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
                {s.shared && (
                  <Chip title={s.inherited ? "From the shared library — read-only here" : "In the shared library"}>
                    shared
                  </Chip>
                )}
                {s.references_routine && (
                  <Chip
                    tone={s.routine_ok ? "accent" : "warn"}
                    title={
                      s.routine_ok
                        ? "Runs this routine instead of improvising the flow"
                        : "This playbook points at a routine that no longer exists"
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
            editTitle="Edit this playbook"
            deleteTitle="Delete this playbook"
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

function ToolsTab({ brain }: { brain: AgentBrain }) {
  if (brain.tools_unrestricted) {
    return (
      <div>
        <p className="text-xs text-[var(--color-text)]">
          Unrestricted — every tool discovered on its MCP servers.
        </p>
        <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">
          No allowlist in AGENT.md. Naming tools there narrows what this agent
          may call, on both consult and its loops.
        </p>
      </div>
    );
  }
  return (
    <div>
      <p className="mb-2 text-[11px] text-[var(--color-text-muted)]">
        The allowlist from AGENT.md — the only tools this agent may call. Edit it
        in the Brain tab, where it is written.
      </p>
      <div className="flex flex-wrap gap-1.5">
        {brain.tools.map((t) => (
          <span
            key={t}
            className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 font-mono text-[11px] text-[var(--color-text)]"
          >
            {t}
          </span>
        ))}
      </div>
    </div>
  );
}

function RoutinesTab({
  brain,
  action,
}: {
  brain: AgentBrain;
  action?: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-start justify-between gap-3">
        <p className="text-[11px] text-[var(--color-text-muted)]">
          Scripts this agent can run on demand or on a schedule. Its own library
          first, then the shared one every agent reads.
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
                {r.continuous && (
                  <Chip title="Runs in a loop until stopped">♾️ continuous</Chip>
                )}
                {r.source === "global" && brain.slug !== "condor" && (
                  <Chip title="From the shared library every agent reads">shared</Chip>
                )}
              </>
            }
            subtitle={r.description}
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

  const touch = <T,>(set: (v: T) => void) => (v: T) => {
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

  const touch = <T,>(set: (v: T) => void) => (v: T) => {
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
        <BodyArea value={body} onChange={touch(setBody)} placeholder="The fact itself." />
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
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.body}</ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  );
}
