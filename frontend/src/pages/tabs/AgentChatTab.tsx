import { useQuery } from "@tanstack/react-query";
import {
  ArrowUpRight,
  Bot,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Zap,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { deriveAgentStatus } from "@/components/agent/agentStatus";
import { AgentsTabSwitch, type AgentsTab } from "@/components/chat/AgentsTabSwitch";
import { BrainPicker, type BrainSelection } from "@/components/chat/BrainPicker";
import { ChatInput } from "@/components/chat/ChatInput";
import { ChatSessionIdentity } from "@/components/chat/ChatSessionIdentity";
import { ChatThread } from "@/components/chat/ChatThread";
import { ContextDock } from "@/components/chat/ContextDock";
import { ConversationList } from "@/components/chat/ConversationList";
import { useBrainSwitch } from "@/hooks/useBrainSwitch";
import { useChat, useSessionOptions } from "@/hooks/useChat";
import { useServer } from "@/hooks/useServer";
import { api, CHAT_SLUG, type AgentSummary } from "@/lib/api";

/** Openers offered when nothing is bound, and when something is. */
const CONDOR_STARTERS = [
  "How is my portfolio doing?",
  "What are my bots doing right now?",
  "Any positions at risk?",
];
const AGENT_STARTERS = ["What are you working on?", "Review your last session"];

/** Go to my conversation with them, or start another one regardless. */
type TalkIntent = "focus" | "fresh";

/**
 * The chat workspace — what `/agents` opens on.
 *
 * A rail of who you can talk to and what you already said, and a conversation
 * beside it. It renders the same `ChatThread` as the overlay panel and reads
 * the same `useChat()` state, so this is a second view of one chat rather than
 * a second chat.
 */
export function AgentChatTab({
  onTabChange,
}: {
  /** Flip `/agents` to the fleet report — the host owns the `?tab=` param. */
  onTabChange: (tab: AgentsTab) => void;
}) {
  const chat = useChat();
  const { server } = useServer();
  const [searchParams, setSearchParams] = useSearchParams();
  const {
    agents: modelOptions,
    customProviders,
    agentBindings,
    defaultAgent,
  } = useSessionOptions();

  const { switchBrain, switchError, dismissSwitchError } = useBrainSwitch();
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

  // Nobody else opens the socket here: the overlay panel is hidden on this
  // route, and `connect()` no-ops on an open socket.
  useEffect(() => {
    chat.connect();
  }, [chat.connect]);

  const activeSlot = chat.activeSlot;
  const isActiveStreaming = chat.isSlotStreaming(chat.activeSlotId);

  /**
   * Talk to someone.
   *
   * `"focus"` is the contact-list gesture — go to my conversation with them,
   * starting one only if none is live. `"fresh"` skips the lookup and always
   * spawns, which is the only way to get a second chat with the same agent.
   * Nothing spawns that the user did not ask for either way, so the per-user
   * session cap stays a non-issue.
   */
  const talkTo = (
    agentSlug: string,
    opts?: { intent?: TalkIntent; text?: string },
  ) => {
    if ((opts?.intent ?? "focus") === "focus") {
      const live = chat.slots.find(
        (s) => (s.info.agent_slug || "") === agentSlug,
      );
      if (live) {
        chat.setActiveSlotId(live.info.slot_id);
        if (opts?.text) chat.sendMessage(live.info.slot_id, opts.text);
        return;
      }
    }
    const slotId = chat.startSession(
      pendingAgentKey ?? defaultAgent,
      server || undefined,
      agentSlug || undefined,
    );
    // The tab is on screen before the spawn is; the outbox flushes this the
    // moment the session lands, which is what makes a new chat feel warm.
    if (opts?.text) chat.sendMessage(slotId, opts.text);
  };

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

  const liveIds = new Set(
    chat.slots.map((s) => s.info.conversation_id || s.info.slot_id),
  );

  const boundAgent = activeSlot?.info.agent_slug
    ? agents.find((a) => a.slug === activeSlot.info.agent_slug)
    : undefined;
  const heroAgent = activeSlot ? undefined : pendingAgent;

  // Condor is in the registry like every other Agent (FEAT-033), but it is not
  // a specialist you bind: you get it by binding nothing, which is what the
  // empty slug means everywhere else here. So it is lifted out of the list and
  // rendered once, at the top, as the row it has always been — reading its name
  // and description off its own AGENT.md rather than repeating them here.
  const condor = agents.find((a) => a.slug === CHAT_SLUG);
  const specialists = agents.filter((a) => a.slug !== CHAT_SLUG);

  const liveAgents = specialists.filter((a) => deriveAgentStatus(a) === "running");
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
        {/* Where you are and what is running. Two doors to the fleet report —
            fine, when one of them is a live count. */}
        <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-3 py-2">
          <AgentsTabSwitch tab="chat" onChange={onTabChange} />
          <button
            onClick={() => onTabChange("fleet")}
            className="flex min-w-0 flex-1 items-center gap-2 text-left text-[11px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
            title="Open the fleet report"
          >
            <Zap
              className={`h-3 w-3 shrink-0 ${
                liveAgents.length > 0 ? "text-emerald-400" : ""
              }`}
            />
            <span className="min-w-0 flex-1 truncate">
              {liveAgents.length} live
              {runningTasks > 0 && ` · ${runningTasks} task${runningTasks !== 1 ? "s" : ""}`}
            </span>
            <ArrowUpRight className="h-3 w-3 shrink-0" />
          </button>
        </div>

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
              icon={<Bot className="h-3 w-3 shrink-0 text-[var(--color-accent)]" />}
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

        {/* What you already said — the panel's own list, in flow */}
        <ConversationList
          variant="inline"
          liveIds={liveIds}
          activeId={activeSlot?.info.conversation_id || chat.activeSlotId}
          // "New chat" means a fresh one with whoever is selected — Condor
          // only when Condor is who you are pointing at.
          onNew={() => talkTo(selectedSlug, { intent: "fresh" })}
          onOpen={(meta) => {
            setRailOpen(false);
            chat.resumeConversation(meta.id, {
              agent_key: meta.agent_key,
              server_name: meta.server_name || undefined,
              agent_slug: meta.agent_slug,
            });
          }}
        />
      </aside>

      {/* ── Conversation, and what it set in motion ──
          `relative` so the dock overlays the transcript below `xl` rather than
          escaping to the page, the mirror of what the rail does below `md`. */}
      <div className="relative flex min-w-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
          {/* Who is answering */}
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
            {/* Who is answering and where it runs — the same strip the overlay
                panel shows. */}
            <ChatSessionIdentity
              slot={activeSlot}
              agents={modelOptions}
              customProviders={customProviders}
              agentBindings={agentBindings}
              isStreaming={isActiveStreaming}
              onSelectBrain={switchBrain}
              placeholder={<span className="text-sm font-semibold">Chat</span>}
            />
            {/* Strategies, brain and routines stay on the agent's own page. */}
            {boundAgent && (
              <Link
                to={`/agents/${boundAgent.slug}`}
                className="ml-auto flex items-center gap-1 text-[11px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-primary)]"
              >
                Manage
                <ArrowUpRight className="h-3 w-3" />
              </Link>
            )}
          </div>

          <ChatThread
            slot={activeSlot}
            agents={modelOptions}
            isStreaming={isActiveStreaming}
            isQueued={chat.isSlotQueued(chat.activeSlotId)}
            permissionRequest={chat.permissionRequest}
            onResolvePermission={chat.resolvePermission}
            switchError={switchError}
            onDismissSwitchError={dismissSwitchError}
            onSend={(text) =>
              activeSlot && chat.sendMessage(activeSlot.info.slot_id, text)
            }
            onAbort={() => chat.activeSlotId && chat.abortPrompt(chat.activeSlotId)}
            boundAgent={boundAgent}
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
                onPickBrain={(sel) => {
                  // The two fields are orthogonal — the picker names whichever
                  // one the user changed — so binding an Agent leaves the model
                  // alone, and vice versa.
                  if (sel.agentKey !== undefined) setPendingAgentKey(sel.agentKey);
                  if (sel.agentSlug !== undefined) {
                    setPendingAgent(
                      agents.find((a) => a.slug === sel.agentSlug) || null,
                    );
                  }
                }}
              />
            }
          />
        </div>

        <ContextDock
          delegations={delegationData?.delegations ?? []}
          conversationId={activeSlot?.info.conversation_id || ""}
          agentSlug={activeSlot?.info.agent_slug || ""}
          onOpenFleet={() => onTabChange("fleet")}
        />
      </div>
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
  const starters = agent ? AGENT_STARTERS : CONDOR_STARTERS;

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

        <div className="mt-3 flex flex-wrap justify-center gap-2">
          {starters.map((text) => (
            <button
              key={text}
              onClick={() => onAsk(text)}
              className="rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs text-[var(--color-text-muted)] transition-colors hover:border-[var(--color-primary)]/40 hover:text-[var(--color-text)]"
            >
              {text}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
