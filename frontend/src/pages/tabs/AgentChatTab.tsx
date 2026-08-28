import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Bot,
  BrainCircuit,
  ClipboardList,
  PanelLeftClose,
  PanelLeftOpen,
  ShieldAlert,
  Wallet,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  BrainPicker,
  type BrainSelection,
} from "@/components/chat/BrainPicker";
import { ChatInput } from "@/components/chat/ChatInput";
import { ChatRail } from "@/components/chat/ChatRail";
import { ChatSessionIdentity } from "@/components/chat/ChatSessionIdentity";
import { ChatThread } from "@/components/chat/ChatThread";
import { ContextDock } from "@/components/chat/ContextDock";
import { SessionTabs } from "@/components/chat/SessionTabs";
import {
  WorkspacePaneOutlet,
  WorkspacePaneProvider,
} from "@/components/chat/WorkspacePane";
import { Starters, type Starter } from "@/components/chat/Starters";
import { useBrainSwitch } from "@/hooks/useBrainSwitch";
import { useChat, useSessionOptions } from "@/hooks/useChat";
import { useServer } from "@/hooks/useServer";
import { useStarters } from "@/hooks/useStarters";
import { normalizeAgentSlug } from "@/lib/agentSlug";
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
      // Links speak the registry's spelling — `/?agent=condor` from an agent
      // page — while a chat binds Condor by binding nobody. Translate once,
      // here, or the lookup below misses the live Condor conversation and
      // spawns a bound-`"condor"` one the rail can never light up.
      const slug = normalizeAgentSlug(agentSlug);
      if ((opts?.intent ?? "focus") === "focus") {
        const live = slotsRef.current.find(
          (s) => (s.info.agent_slug || "") === slug,
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
        pendingAgentKey ?? (slug ? "" : defaultAgent),
        server || undefined,
        slug || undefined,
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
      chat.resumeConversation(meta.id, {
        agent_key: meta.agent_key,
        server_name: meta.server_name || undefined,
        agent_slug: meta.agent_slug,
      });
    },
    [chat.resumeConversation],
  );
  // The rail says who was picked and the tab remembers it, because the hero it
  // colours is the tab's, not the rail's.
  const onTalk = useCallback(
    (slug: string, agent: AgentSummary | null, fresh?: boolean) => {
      setPendingAgent(agent);
      talkTo(slug, fresh ? { intent: "fresh" } : undefined);
    },
    [talkTo],
  );
  const closeRail = useCallback(() => setRailOpen(false), []);

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

  const runningTasks = (delegationData?.delegations ?? []).filter(
    (d) => d.status === "running",
  ).length;

  return (
    <WorkspacePaneProvider>
      <div className="flex h-full min-h-0">
        <ChatRail
          agents={agents}
          runningTasks={runningTasks}
          activeSlug={activeSlot?.info.agent_slug || ""}
          hasSession={!!activeSlot}
          liveIds={liveIds}
          activeId={activeSlot?.info.conversation_id || chat.activeSlotId}
          open={railOpen}
          onClose={closeRail}
          onTalk={onTalk}
          onNew={openFresh}
          onOpenConversation={openConversation}
        />

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

          {/* What a dock row opened, beside the conversation rather than on top
              of it — so the agent that produced the report is still there to
              ask about it. */}
          <WorkspacePaneOutlet />

          <ContextDock
            delegations={delegationData?.delegations ?? []}
            conversationId={activeSlot?.info.conversation_id || ""}
            agentSlug={activeSlot?.info.agent_slug || ""}
          />
        </div>
      </div>
    </WorkspacePaneProvider>
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
