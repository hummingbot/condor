import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowUpRight,
  Bot,
  BrainCircuit,
  ChevronDown,
  ClipboardList,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  ShieldAlert,
  Wallet,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { deriveAgentStatus } from "@/components/agent/agentStatus";
import {
  BrainPicker,
  type BrainSelection,
} from "@/components/chat/BrainPicker";
import { ChatInput } from "@/components/chat/ChatInput";
import { ChatSessionIdentity } from "@/components/chat/ChatSessionIdentity";
import { ChatThread } from "@/components/chat/ChatThread";
import { ContextDock } from "@/components/chat/ContextDock";
import { ConversationList } from "@/components/chat/ConversationList";
import { SessionTabs } from "@/components/chat/SessionTabs";
import { Starters, type Starter } from "@/components/chat/Starters";
import { useBrainSwitch } from "@/hooks/useBrainSwitch";
import { useChat, useSessionOptions } from "@/hooks/useChat";
import { useServer } from "@/hooks/useServer";
import { useStarters } from "@/hooks/useStarters";
import {
  api,
  CHAT_SLUG,
  type AgentSummary,
  type ConversationMeta,
} from "@/lib/api";

/** Openers offered when nothing is bound, and when something is. */
const CONDOR_STARTERS: Starter[] = [
  {
    icon: Wallet,
    title: "How is my portfolio doing?",
    hint: "Balances, PNL and what moved since yesterday",
  },
  {
    icon: Bot,
    title: "What are my bots doing right now?",
    hint: "Running controllers, open executors and their state",
  },
  {
    icon: ShieldAlert,
    title: "Any positions at risk?",
    hint: "Exposure, drawdown and anything near a limit",
  },
];
const AGENT_STARTERS: Starter[] = [
  {
    icon: Activity,
    title: "What are you working on?",
    hint: "Current strategies, open tasks and the last tick",
  },
  {
    icon: ClipboardList,
    title: "Review your last session",
    hint: "What it decided, what it traded, what it learned",
  },
];

/** Go to my conversation with them, or start another one regardless. */
type TalkIntent = "focus" | "fresh";

/**
 * The chat workspace — what `/` opens on.
 *
 * A rail of who you can talk to and what you already said, a conversation
 * beside it, and a dock of the work it set in motion. Since the fleet grid was
 * removed this is the whole of the workspace: the rail is how you switch who
 * you are talking to and start another conversation, which is the gesture the
 * old header selector could not do.
 */
export function AgentChatTab() {
  const chat = useChat();
  const { server } = useServer();
  const [searchParams, setSearchParams] = useSearchParams();
  const {
    agents: modelOptions,
    customProviders,
    agentBindings,
    defaultAgent,
  } = useSessionOptions();

  const { switchBrain, switchServer, switchError, dismissSwitchError } =
    useBrainSwitch();
  const [railOpen, setRailOpen] = useState(false);
  /** The rail row selected while no session exists yet, to colour the hero. */
  const [pendingAgent, setPendingAgent] = useState<AgentSummary | null>(null);
  /**
   * The model picked before there is a session to switch. The hero's picker is
   * the only model control on this screen, so what it says has to be what the
   * next `start_session` carries — otherwise the pick is silently dropped.
   * `null` means "never touched it", which is what falls back to `defaultAgent`.
   */
  const [pendingAgentKey, setPendingAgentKey] = useState<string | null>(null);

  // Same keys and intervals the fleet tab uses, so react-query dedupes rather
  // than polling `/agents` twice.
  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: api.getAgents,
    refetchInterval: 10000,
  });
  const { data: delegationData } = useQuery({
    queryKey: ["delegations"],
    queryFn: api.getDelegations,
    refetchInterval: 5000,
  });

  // The shell already holds the socket open on every route; this is the one
  // surface allowed to prewarm on top of it, which is what keeps a subprocess
  // from being spawned for a user who only opened /portfolio.
  useEffect(() => {
    chat.enablePrewarm();
  }, [chat.enablePrewarm]);

  const activeSlot = chat.activeSlot;
  const isActiveStreaming = chat.isSlotStreaming(chat.activeSlotId);

  /**
   * The slots, read without depending on them.
   *
   * `chat.slots` is a new array on every 50 ms stream flush, so a `talkTo`
   * that closed over it would be re-created 20 times a second — and the three
   * props it feeds the rail would defeat the rail's `memo`. The lookup only
   * ever runs from an event handler, which is after the commit that refreshed
   * this, so the ref is never behind when it is read.
   */
  const slotsRef = useRef(chat.slots);
  useEffect(() => {
    slotsRef.current = chat.slots;
  }, [chat.slots]);

  /**
   * Talk to someone.
   *
   * `"focus"` is the contact-list gesture — go to my conversation with them,
   * starting one only if none is live. `"fresh"` skips the lookup and always
   * spawns, which is the only way to get a second chat with the same agent.
   * Nothing spawns that the user did not ask for either way, so the per-user
   * session cap stays a non-issue.
   */
  const talkTo = useCallback(
    (agentSlug: string, opts?: { intent?: TalkIntent; text?: string }) => {
      if ((opts?.intent ?? "focus") === "focus") {
        const live = slotsRef.current.find(
          (s) => (s.info.agent_slug || "") === agentSlug,
        );
        if (live) {
          chat.setActiveSlotId(live.info.slot_id);
          if (opts?.text) chat.sendMessage(live.info.slot_id, opts.text);
          return;
        }
      }
      const slotId = chat.startSession(
        // `""` asks whoever is bound for their own model; only an unbound chat
        // needs a model named. Volunteering `defaultAgent` here is what used to
        // claim an override the user never made, so a bound Agent ran on
        // Condor's model instead of its own.
        pendingAgentKey ?? (agentSlug ? "" : defaultAgent),
        server || undefined,
        agentSlug || undefined,
      );
      // The tab is on screen before the spawn is; the outbox flushes this the
      // moment the session lands, which is what makes a new chat feel warm.
      if (opts?.text) chat.sendMessage(slotId, opts.text);
    },
    [
      chat.sendMessage,
      chat.setActiveSlotId,
      chat.startSession,
      defaultAgent,
      pendingAgentKey,
      server,
    ],
  );

  /**
   * Who "New chat" means: the conversation you are in, or — before there is
   * one — the rail row you highlighted. Both are what the user is pointing at.
   */
  const selectedSlug = activeSlot?.info.agent_slug ?? pendingAgent?.slug ?? "";

  // "Chat" on an agent's detail page arrives as `?agent=<slug>`: focus or start
  // that conversation once, then drop the param so a reload is not a respawn.
  const consumedAgentParam = useRef(false);
  const agentParam = searchParams.get("agent");
  useEffect(() => {
    if (!agentParam || consumedAgentParam.current) return;
    consumedAgentParam.current = true;
    talkTo(agentParam);
    setSearchParams({}, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentParam]);

  // Keyed on the joined ids rather than on `chat.slots`: the array itself is
  // re-created by every stream flush, so a memo keyed on it would hand the
  // rail a fresh `Set` 20 times a second. The join is O(#slots) — a handful,
  // not the 100 rows the rail would otherwise re-render.
  const liveKey = chat.slots
    .map((s) => s.info.conversation_id || s.info.slot_id)
    .join("|");
  const liveIds = useMemo(
    () => new Set(liveKey ? liveKey.split("|") : []),
    [liveKey],
  );

  // Stable identities, so the rail's `memo` holds while an answer streams.
  // `selectedSlug` is a string and the rest are `useCallback`s on genuinely
  // stable deps, so these only change when the rail's answer actually changes.
  const openFresh = useCallback(
    () => talkTo(selectedSlug, { intent: "fresh" }),
    [talkTo, selectedSlug],
  );
  const openConversation = useCallback(
    (meta: ConversationMeta) => {
      setRailOpen(false);
      chat.resumeConversation(meta.id, {
        agent_key: meta.agent_key,
        server_name: meta.server_name || undefined,
        agent_slug: meta.agent_slug,
      });
    },
    [chat.resumeConversation],
  );

  const boundAgent = activeSlot?.info.agent_slug
    ? agents.find((a) => a.slug === activeSlot.info.agent_slug)
    : undefined;
  const heroAgent = activeSlot ? undefined : pendingAgent;

  // What the agent in focus has learned this user asks it for, over the
  // static list. Two calls because the two empty states can be looking at
  // different agents at once: the thread renders the slot that exists, the
  // hero the one the rail is pointing at before any slot does.
  const slotSlug = activeSlot?.info.agent_slug ?? "";
  const slotStarters = useStarters(
    slotSlug,
    slotSlug ? AGENT_STARTERS : CONDOR_STARTERS,
  );

  /**
   * Whose knowledge the Knowledge link opens, and what to call them.
   *
   * Both read off the slug, never off `boundAgent`/`pendingAgent` in their own
   * order: an unbound Condor conversation leaves `boundAgent` undefined while a
   * rail click can leave `pendingAgent` pointing at a specialist, so a name
   * resolved separately from the slug titled the panel "Orca LP Expert" over
   * Condor's brain until the fetch landed and corrected it.
   */
  const knowledgeSlug = selectedSlug || CHAT_SLUG;
  const agentName = (slug: string) =>
    agents.find((a) => a.slug === slug)?.name || "Condor";

  // Condor is in the registry like every other Agent (FEAT-033), but it is not
  // a specialist you bind: you get it by binding nothing, which is what the
  // empty slug means everywhere else here. So it is lifted out of the list and
  // rendered once, at the top, as the row it has always been — reading its name
  // and description off its own AGENT.md rather than repeating them here.
  const condor = agents.find((a) => a.slug === CHAT_SLUG);
  const specialists = agents.filter((a) => a.slug !== CHAT_SLUG);

  const liveAgents = specialists.filter(
    (a) => deriveAgentStatus(a) === "running",
  );
  const runningTasks = (delegationData?.delegations ?? []).filter(
    (d) => d.status === "running",
  ).length;

  return (
    <div className="flex h-full min-h-0">
      {/* ── Rail ── */}
      <aside
        className={`${
          railOpen ? "flex" : "hidden"
        } absolute inset-y-0 left-0 z-30 w-[260px] shrink-0 flex-col border-r border-[var(--color-border)] bg-[var(--color-bg)] md:relative md:flex`}
      >
        {/* What is looping right now, and the way into it. */}
        <LiveStrip agents={liveAgents} runningTasks={runningTasks} />

        {/* Who you can talk to */}
        <div className="border-b border-[var(--color-border)] py-1">
          <div className="px-3 pb-1 pt-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
            Agents
          </div>
          <RailRow
            label={condor?.name || "Condor"}
            icon={<MessageSquare className="h-3 w-3 shrink-0" />}
            active={!!activeSlot && !activeSlot.info.agent_slug}
            title={condor?.description || "General trading assistant"}
            onClick={() => {
              setPendingAgent(null);
              talkTo("");
            }}
            newTitle={`New chat with ${condor?.name || "Condor"}`}
            onNew={() => {
              setPendingAgent(null);
              talkTo("", { intent: "fresh" });
            }}
          />
          {specialists.map((agent) => (
            <RailRow
              key={agent.slug}
              label={agent.name}
              icon={
                <Bot className="h-3 w-3 shrink-0 text-[var(--color-accent)]" />
              }
              live={deriveAgentStatus(agent) === "running"}
              active={activeSlot?.info.agent_slug === agent.slug}
              title={agent.description || agent.name}
              onClick={() => {
                setPendingAgent(agent);
                talkTo(agent.slug);
              }}
              newTitle={`New chat with ${agent.name}`}
              onNew={() => {
                setPendingAgent(agent);
                talkTo(agent.slug, { intent: "fresh" });
              }}
            />
          ))}
        </div>

        {/* What you already said — the rail's own list, in flow */}
        <ConversationList
          liveIds={liveIds}
          activeId={activeSlot?.info.conversation_id || chat.activeSlotId}
          // "New chat" means a fresh one with whoever is selected — Condor
          // only when Condor is who you are pointing at.
          onNew={openFresh}
          onOpen={openConversation}
        />
      </aside>

      {/* ── Conversation, and what it set in motion ──
          `relative` so the dock overlays the transcript below `xl` rather than
          escaping to the page, the mirror of what the rail does below `md`. */}
      <div className="relative flex min-w-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
          {/* Which sessions are live, and who is answering in this one — one
              row, because the active tab and the identity name the same chat. */}
          <div className="flex shrink-0 items-center gap-2 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2">
            <button
              onClick={() => setRailOpen((v) => !v)}
              className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] md:hidden"
              title={railOpen ? "Hide conversations" : "Show conversations"}
            >
              {railOpen ? (
                <PanelLeftClose className="h-4 w-4" />
              ) : (
                <PanelLeftOpen className="h-4 w-4" />
              )}
            </button>
            {/* Every live session, including the ones answering in the
                background — switching, and the only way to stop one. */}
            <SessionTabs
              slots={chat.slots}
              agents={modelOptions}
              activeSlotId={chat.activeSlotId}
              isSlotStreaming={chat.isSlotStreaming}
              permissionRequests={chat.permissionRequests}
              onSelect={(slotId) => chat.setActiveSlotId(slotId)}
              // The session ends; the transcript stays on the server, so the
              // conversation is still in the rail and clicking it respawns it.
              onClose={(slotId) => chat.destroySession(slotId)}
              className="min-w-0 flex-1"
            />
            {/* Who is answering and where it runs, plus the way out to the
                agent's own page — pinned right, whatever the strip does. */}
            <div className="ml-auto flex shrink-0 items-center gap-2">
              <ChatSessionIdentity
                slot={activeSlot}
                agents={modelOptions}
                customProviders={customProviders}
                agentBindings={agentBindings}
                isStreaming={isActiveStreaming}
                onSelectBrain={switchBrain}
                onSelectServer={(name) => {
                  if (activeSlot) switchServer(activeSlot.info.slot_id, name);
                }}
              />
              {/* What the thing on the other side of this conversation
                  actually knows — its brain, playbooks, memories, tools,
                  strategies and routines — and the place to change any of it.
                  It goes straight to the agent's page: this used to open a
                  sheet over the chat that carried a link to that same page,
                  which is one door too many for the one destination. The
                  conversation is held by the shell's socket, not by this
                  route, so it is still there when you come back — and the
                  page's own Chat button is that way back. */}
              <Link
                to={`/agents/${encodeURIComponent(knowledgeSlug)}`}
                className="flex items-center gap-1 rounded px-1.5 py-1 text-[11px] text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-primary)]"
                title={`What ${agentName(knowledgeSlug)} knows and can do — read and edit`}
              >
                <BrainCircuit className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Knowledge</span>
              </Link>
            </div>
          </div>

          <ChatThread
            slot={activeSlot}
            agents={modelOptions}
            isStreaming={isActiveStreaming}
            isQueued={chat.isSlotQueued(chat.activeSlotId)}
            permissionRequest={chat.permissionFor(chat.activeSlotId)}
            onResolvePermission={chat.resolvePermission}
            switchError={switchError}
            onDismissSwitchError={dismissSwitchError}
            onSend={(text) =>
              activeSlot && chat.sendMessage(activeSlot.info.slot_id, text)
            }
            onAbort={() =>
              chat.activeSlotId && chat.abortPrompt(chat.activeSlotId)
            }
            boundAgent={boundAgent}
            // The hero's openers, again — a session that spawned but was never
            // written in is as empty as no session at all.
            starters={slotStarters}
            columnClassName="mx-auto w-full max-w-3xl"
            autoFocus
            emptyState={
              <Hero
                agent={heroAgent}
                modelOptions={modelOptions}
                customProviders={customProviders}
                agentBindings={agentBindings}
                selectedKey={pendingAgentKey ?? defaultAgent}
                onAsk={(text) => talkTo(heroAgent?.slug || "", { text })}
                // The picker moves the model and nothing else; who answers is
                // the rail's question, and it is already answered by the row
                // the user highlighted.
                onPickBrain={(sel) => {
                  if (sel.agentKey !== undefined)
                    setPendingAgentKey(sel.agentKey);
                }}
              />
            }
          />
        </div>

        <ContextDock
          delegations={delegationData?.delegations ?? []}
          conversationId={activeSlot?.info.conversation_id || ""}
          agentSlug={activeSlot?.info.agent_slug || ""}
        />
      </div>
    </div>
  );
}

// ── Live strip ──

/**
 * What is looping, and the door into it.
 *
 * Strategies live on an agent's own page, so that is where this points — one
 * live agent is a direct link, several open a short list. This replaced the
 * fleet grid: the grid's only unique job was showing which agents are running,
 * and a line at the top of the rail does that without a second page.
 */
function LiveStrip({
  agents,
  runningTasks,
}: {
  agents: AgentSummary[];
  runningTasks: number;
}) {
  const [open, setOpen] = useState(false);
  const tasks =
    runningTasks > 0
      ? ` · ${runningTasks} task${runningTasks !== 1 ? "s" : ""}`
      : "";

  const icon = (
    <Zap
      className={`h-3 w-3 shrink-0 ${agents.length > 0 ? "text-emerald-400" : ""}`}
    />
  );
  const rowClass =
    "flex min-w-0 flex-1 items-center gap-2 text-left text-[11px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]";

  return (
    <div className="relative flex items-center gap-2 border-b border-[var(--color-border)] px-3 py-2">
      {agents.length === 0 ? (
        <span className={rowClass}>
          {icon}
          <span className="min-w-0 flex-1 truncate">
            Nothing looping{tasks}
          </span>
        </span>
      ) : agents.length === 1 ? (
        <Link
          to={`/agents/${agents[0].slug}`}
          className={rowClass}
          title={`Open ${agents[0].name}'s strategies`}
        >
          {icon}
          <span className="min-w-0 flex-1 truncate">
            {agents[0].name} live{tasks}
          </span>
          <ArrowUpRight className="h-3 w-3 shrink-0" />
        </Link>
      ) : (
        <button
          onClick={() => setOpen((v) => !v)}
          className={rowClass}
          title="Open a running agent's strategies"
        >
          {icon}
          <span className="min-w-0 flex-1 truncate">
            {agents.length} live{tasks}
          </span>
          <ChevronDown className="h-3 w-3 shrink-0" />
        </button>
      )}

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute left-2 top-full z-50 mt-1 flex w-[236px] flex-col rounded border border-[var(--color-border)] bg-[var(--color-surface)] py-0.5 shadow-lg">
            {agents.map((agent) => (
              <Link
                key={agent.slug}
                to={`/agents/${agent.slug}`}
                onClick={() => setOpen(false)}
                className="flex items-center gap-2 px-2.5 py-1.5 text-xs text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
              >
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400" />
                <span className="min-w-0 flex-1 truncate">{agent.name}</span>
                <ArrowUpRight className="h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
              </Link>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// ── Rail row ──

function RailRow({
  label,
  icon,
  live,
  active,
  title,
  onClick,
  onNew,
  newTitle,
}: {
  label: string;
  icon: React.ReactNode;
  live?: boolean;
  active?: boolean;
  title?: string;
  onClick: () => void;
  /** Start a *second* conversation with this row, rather than focusing one. */
  onNew?: () => void;
  newTitle?: string;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`group flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors ${
        active
          ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
          : "text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
      }`}
    >
      {icon}
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {live && (
        <span
          className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400"
          title="Loop running"
        />
      )}
      {onNew && (
        // A span, not a button: this sits inside the row's own button. Below
        // `md` the rail is a touch overlay where hover does not exist, so the
        // `+` stays visible there and only hides behind hover on the desktop
        // rail. The same shape the panel's session tabs use to close.
        <span
          role="button"
          tabIndex={0}
          aria-label={newTitle}
          title={newTitle}
          onClick={(e) => {
            e.stopPropagation();
            onNew();
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              e.stopPropagation();
              onNew();
            }
          }}
          className="shrink-0 rounded p-0.5 text-[var(--color-text-muted)] transition-opacity hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] focus-visible:opacity-100 group-focus-within:opacity-100 md:opacity-0 md:group-hover:opacity-100"
        >
          <Plus className="h-3 w-3" />
        </span>
      )}
    </button>
  );
}

// ── Empty state ──

/**
 * No conversation yet — so this is one, minus the transcript.
 *
 * A live composer rather than a "Start Session" button: typing here starts the
 * session and queues the message in the same gesture.
 */
function Hero({
  agent,
  modelOptions,
  customProviders,
  agentBindings,
  selectedKey,
  onAsk,
  onPickBrain,
}: {
  agent: AgentSummary | null | undefined;
  modelOptions: ReturnType<typeof useSessionOptions>["agents"];
  customProviders: ReturnType<typeof useSessionOptions>["customProviders"];
  agentBindings: ReturnType<typeof useSessionOptions>["agentBindings"];
  /** The model the next session starts on — owned by the tab, not by the hero,
   *  because the tab is what calls `startSession`. */
  selectedKey: string;
  onAsk: (text: string) => void;
  onPickBrain: (selection: BrainSelection) => void;
}) {
  const starters = useStarters(
    agent?.slug || "",
    agent ? AGENT_STARTERS : CONDOR_STARTERS,
  );

  return (
    <div className="flex flex-1 flex-col items-center justify-center px-2">
      <div className="w-full max-w-xl">
        <h2 className="text-center text-xl font-semibold text-[var(--color-text)]">
          Ask {agent?.name || "Condor"}
        </h2>
        <p className="mt-1 text-center text-sm text-[var(--color-text-muted)]">
          {agent?.description ||
            "Your portfolio, your bots, your positions — just ask."}
        </p>

        <div className="mt-4 flex justify-center">
          <BrainPicker
            agents={modelOptions}
            customProviders={customProviders}
            agentBindings={agentBindings}
            selectedAgentKey={selectedKey}
            selectedAgentSlug={agent?.slug || ""}
            onSelect={onPickBrain}
            variant="inline"
          />
        </div>

        <div className="mt-4">
          <ChatInput onSend={onAsk} autoFocus />
        </div>

        <Starters starters={starters} onAsk={onAsk} className="mt-4" />
      </div>
    </div>
  );
}
