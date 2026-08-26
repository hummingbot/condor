import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowUpRight,
  BookOpen,
  Brain,
  Loader2,
  Repeat,
  Server,
  Sparkles,
  Wrench,
  Zap,
} from "lucide-react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import { Link } from "react-router-dom";
import remarkGfm from "remark-gfm";

import { WorkspaceSheet } from "@/components/chat/WorkspaceSheet";
import { api, type AgentBrain, type SkillBody } from "@/lib/api";
import { formatRoutineName } from "@/lib/routineUtils";

type TabId = "brain" | "skills" | "memories" | "tools" | "strategies" | "routines";

/** What the reader drilled into, if anything — a playbook or a memory. */
type Reading = { kind: "skill" | "memory"; name: string; title: string };

/** What `getAgentMemory` returns — a name and the body, nothing else. */
type MemoryBody = { name: string; body: string };

/**
 * Who you are talking to, in full.
 *
 * A conversation shows an agent's name and nothing else, so the question it
 * leaves unanswered is the obvious one: what does this thing actually know, and
 * what can it do? Everything here is what the model itself is handed at the top
 * of every turn — its AGENT.md, the playbooks and memories that reach it as an
 * index, its tool allowlist — plus the two catalogs it can act through, its
 * routines and its strategies. Reading the panel and reading the prompt should
 * leave you with the same picture.
 *
 * Read-only on purpose. Editing a brain is a page-sized job with an unsaved-edit
 * guard behind it, and that page already exists at `/agents/{slug}` — this is
 * the glance you take without leaving the conversation, and every row that has
 * a fuller home links to it.
 *
 * Bodies are fetched one at a time. The list endpoint carries metadata only, so
 * opening the panel on an agent with forty playbooks costs a few kilobytes, and
 * the one you clicked costs its own request.
 */
export function AgentBrainSheet({
  slug,
  fallbackName,
  onClose,
}: {
  slug: string;
  /** Named before the fetch lands, so the sheet opens with a title. */
  fallbackName?: string;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<TabId>("brain");
  const [reading, setReading] = useState<Reading | null>(null);

  const { data: brain, isLoading, error } = useQuery({
    queryKey: ["agent-brain", slug],
    queryFn: () => api.getAgentBrain(slug),
    // A brain changes when someone edits it, not while you read it. Fresh on
    // open is what matters; a poll here would walk the skill and memory stores
    // on disk every few seconds for a panel nobody is looking at.
    staleTime: 30_000,
  });

  const counts = {
    skills: brain?.skills.length ?? 0,
    memories: brain?.memories.length ?? 0,
    tools: brain?.tools.length ?? 0,
    strategies: brain?.strategies.length ?? 0,
    routines: brain?.routines.length ?? 0,
  };

  const tabs: { id: TabId; label: string; icon: React.ReactNode; count?: number }[] = [
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
  ];

  return (
    <WorkspaceSheet
      title={brain?.name || fallbackName || slug}
      subtitle={brain?.description || "What this agent knows and can do"}
      onClose={onClose}
    >
      {/* Tabs stay put while a drill-down is open: leaving a playbook is one
          click on the section you came from, not a hunt for a back button. */}
      <div className="-mt-1 mb-4 flex flex-wrap items-center gap-1 border-b border-[var(--color-border)] pb-2">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => {
              setTab(t.id);
              setReading(null);
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
        <Link
          to={`/agents/${encodeURIComponent(slug)}`}
          onClick={onClose}
          className="ml-auto flex items-center gap-1 px-2 text-[11px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-primary)]"
          title="Edit this agent, its brain and its strategies"
        >
          Manage
          <ArrowUpRight className="h-3 w-3" />
        </Link>
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
        (reading ? (
          <BodyReader
            slug={slug}
            reading={reading}
            onBack={() => setReading(null)}
          />
        ) : (
          <>
            {tab === "brain" && <BrainTab brain={brain} />}
            {tab === "skills" && (
              <SkillsTab
                brain={brain}
                onOpen={(s) =>
                  setReading({ kind: "skill", name: s.slug, title: s.name })
                }
              />
            )}
            {tab === "memories" && (
              <MemoriesTab
                brain={brain}
                onOpen={(m) =>
                  setReading({ kind: "memory", name: m.name, title: m.name })
                }
              />
            )}
            {tab === "tools" && <ToolsTab brain={brain} />}
            {tab === "strategies" && (
              <StrategiesTab brain={brain} onNavigate={onClose} />
            )}
            {tab === "routines" && <RoutinesTab brain={brain} />}
          </>
        ))}
    </WorkspaceSheet>
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

/** A clickable list row: a title line, badges, and a line of prose under it. */
function Row({
  title,
  badges,
  subtitle,
  onClick,
}: {
  title: string;
  badges?: React.ReactNode;
  subtitle?: string;
  onClick?: () => void;
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

  if (!onClick) {
    return (
      <div className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2">
        {inner}
      </div>
    );
  }
  return (
    <button
      onClick={onClick}
      className="w-full rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-left transition-colors hover:border-[var(--color-primary)]/40"
    >
      {inner}
    </button>
  );
}

// ── Tabs ──

function BrainTab({ brain }: { brain: AgentBrain }) {
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

      <p className="mb-1 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
        AGENT.md — identity &amp; domain knowledge
      </p>
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

function SkillsTab({
  brain,
  onOpen,
}: {
  brain: AgentBrain;
  onOpen: (skill: AgentBrain["skills"][number]) => void;
}) {
  if (brain.skills.length === 0)
    return <Empty>No playbooks in this agent's library.</Empty>;

  return (
    <div className="space-y-1.5">
      <p className="text-[11px] text-[var(--color-text-muted)]">
        Playbooks the agent reads before hand-rolling a known flow. Click one to
        read it exactly as the agent does.
      </p>
      {brain.skills.map((s) => (
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
        />
      ))}
    </div>
  );
}

function MemoriesTab({
  brain,
  onOpen,
}: {
  brain: AgentBrain;
  onOpen: (memory: AgentBrain["memories"][number]) => void;
}) {
  if (brain.memories.length === 0)
    return <Empty>Nothing remembered in this domain yet.</Empty>;

  return (
    <div className="space-y-1.5">
      <p className="text-[11px] text-[var(--color-text-muted)]">
        What this agent remembers about you — yours alone, and separate from
        every other agent's.
      </p>
      {brain.memories.map((m) => (
        <Row
          key={m.name}
          title={m.name}
          badges={<Chip title="Memory type">{m.type}</Chip>}
          subtitle={m.description}
          onClick={() => onOpen(m)}
        />
      ))}
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
        The allowlist from AGENT.md — the only tools this agent may call.
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

function StrategiesTab({
  brain,
  onNavigate,
}: {
  brain: AgentBrain;
  /** Leaving the sheet for a page means closing the sheet. */
  onNavigate: () => void;
}) {
  if (brain.strategies.length === 0)
    return (
      <Empty>
        No strategies yet — this agent can still be chatted with, consulted and
        delegated to.
      </Empty>
    );

  return (
    <div className="space-y-1.5">
      <p className="text-[11px] text-[var(--color-text-muted)]">
        Playbooks this agent loops on. Open one for its config, sessions and PnL.
      </p>
      {brain.strategies.map((s) => (
        <Link
          key={s.slug}
          to={`/agents/${encodeURIComponent(brain.slug)}/strategies/${encodeURIComponent(s.slug)}`}
          onClick={onNavigate}
          className="flex items-center gap-2 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 transition-colors hover:border-[var(--color-primary)]/40"
        >
          <span
            className={`h-1.5 w-1.5 shrink-0 rounded-full ${
              s.status === "running"
                ? "bg-emerald-400"
                : s.status === "paused"
                  ? "bg-amber-400"
                  : "bg-[var(--color-text-muted)]/40"
            }`}
            title={s.status}
          />
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-medium text-[var(--color-text)]">
              {s.name}
            </p>
            {s.description && (
              <p className="truncate text-[11px] text-[var(--color-text-muted)]">
                {s.description}
              </p>
            )}
          </div>
          <ArrowUpRight className="h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
        </Link>
      ))}
    </div>
  );
}

function RoutinesTab({ brain }: { brain: AgentBrain }) {
  if (brain.routines.length === 0)
    return <Empty>No routines this agent can run.</Empty>;

  return (
    <div className="space-y-1.5">
      <p className="text-[11px] text-[var(--color-text-muted)]">
        Scripts this agent can run on demand or on a schedule. Its own library
        first, then the shared one every agent reads.
      </p>
      {brain.routines.map((r) => (
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
      ))}
    </div>
  );
}

// ── Drill-down ──

/**
 * One playbook or memory, read in full — the same text the agent gets back from
 * `manage_skill` / `manage_memory`.
 */
function BodyReader({
  slug,
  reading,
  onBack,
}: {
  slug: string;
  reading: Reading;
  onBack: () => void;
}) {
  const { data, isLoading, error } = useQuery<MemoryBody | SkillBody>({
    queryKey: ["agent-brain-body", slug, reading.kind, reading.name],
    queryFn: () =>
      reading.kind === "skill"
        ? api.getAgentSkill(slug, reading.name)
        : api.getAgentMemory(slug, reading.name),
    staleTime: 30_000,
  });

  // The kind is in the query key, so the shape follows it — a memory's payload
  // can never be sitting under a skill's key.
  const skill =
    reading.kind === "skill" ? (data as SkillBody | undefined) : undefined;

  return (
    <div>
      <button
        onClick={onBack}
        className="mb-3 flex items-center gap-1 text-[11px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
      >
        <ArrowLeft className="h-3 w-3" />
        Back to {reading.kind === "skill" ? "skills" : "memories"}
      </button>

      <h3 className="text-sm font-semibold text-[var(--color-text)]">
        {reading.title}
      </h3>
      {skill?.when_to_use && (
        <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">
          {skill.when_to_use}
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
