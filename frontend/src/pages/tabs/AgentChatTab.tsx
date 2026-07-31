import { useQuery } from "@tanstack/react-query";
import {
  ArrowUpRight,
  Bot,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Server,
  Zap,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { deriveAgentStatus } from "@/components/agent/agentStatus";
import { BrainPicker, type BrainSelection } from "@/components/chat/BrainPicker";
import { ChatInput } from "@/components/chat/ChatInput";
import { ChatThread } from "@/components/chat/ChatThread";
import { ConversationList } from "@/components/chat/ConversationList";
import { useChat, useSessionOptions } from "@/hooks/useChat";
import { useServer } from "@/hooks/useServer";
import { api, type AgentSummary } from "@/lib/api";

/** Openers offered when nothing is bound, and when something is. */
const CONDOR_STARTERS = [
  "How is my portfolio doing?",
  "What are my bots doing right now?",
  "Any positions at risk?",
];
const AGENT_STARTERS = ["What are you working on?", "Review your last session"];

/**
 * The chat workspace — what `/agents` opens on.
 *
 * A rail of who you can talk to and what you already said, and a conversation
 * beside it. It renders the same `ChatThread` as the overlay panel and reads
 * the same `useChat()` state, so this is a second view of one chat rather than
 * a second chat.
 */
export function AgentChatTab() {
  const chat = useChat();
  const { server } = useServer();
  const [searchParams, setSearchParams] = useSearchParams();
  const {
    agents: modelOptions,
    customProviders,
    agentBindings,
    modes,
    defaultAgent,
    defaultMode,
  } = useSessionOptions();

  const [switchError, setSwitchError] = useState<string | null>(null);
  const [railOpen, setRailOpen] = useState(false);
  /** The rail row selected while no session exists yet, to colour the hero. */
  const [pendingAgent, setPendingAgent] = useState<AgentSummary | null>(null);

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
  const isActiveStreaming = chat.streamingSlotId === chat.activeSlotId;

  /**
   * Talk to someone: focus their live conversation if there is one, else spawn
   * exactly one session already bound to them.
   */
  const talkTo = (agentSlug: string, initialText?: string) => {
    const live = chat.slots.find((s) => (s.info.agent_slug || "") === agentSlug);
    if (live) {
      chat.setActiveSlotId(live.info.slot_id);
      if (initialText) chat.sendMessage(live.info.slot_id, initialText);
      return;
    }
    const slotId = chat.startSession(
      defaultAgent,
      defaultMode,
      server || undefined,
      agentSlug || undefined,
    );
    // The tab is on screen before the spawn is; the outbox flushes this the
    // moment the session lands, which is what makes a new chat feel warm.
    if (initialText) chat.sendMessage(slotId, initialText);
  };

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

  const handleSwitch = (selection: BrainSelection) => {
    if (!chat.activeSlotId) return;
    setSwitchError(null);
    chat
      .switchBrain(chat.activeSlotId, selection)
      .catch((e: Error) => setSwitchError(e.message || "Could not switch"));
  };

  const liveIds = new Set(
    chat.slots.map((s) => s.info.conversation_id || s.info.slot_id),
  );

  const boundAgent = activeSlot?.info.agent_slug
    ? agents.find((a) => a.slug === activeSlot.info.agent_slug)
    : undefined;
  const heroAgent = activeSlot ? undefined : pendingAgent;

  const liveAgents = agents.filter((a) => deriveAgentStatus(a) === "running");
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
        {/* What is running, from the chat tab. One click to the numbers. */}
        <button
          onClick={() => setSearchParams({ tab: "fleet" }, { replace: true })}
          className="flex items-center gap-2 border-b border-[var(--color-border)] px-3 py-2 text-left text-[11px] text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          title="Open the fleet report"
        >
          <Zap
            className={`h-3 w-3 shrink-0 ${
              liveAgents.length > 0 ? "text-emerald-400" : ""
            }`}
          />
          <span className="flex-1 truncate">
            {liveAgents.length} live
            {runningTasks > 0 && ` · ${runningTasks} task${runningTasks !== 1 ? "s" : ""}`}
          </span>
          <ArrowUpRight className="h-3 w-3 shrink-0" />
        </button>

        {/* Who you can talk to */}
        <div className="border-b border-[var(--color-border)] py-1">
          <div className="px-3 pb-1 pt-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
            Agents
          </div>
          <RailRow
            label="Condor"
            icon={<MessageSquare className="h-3 w-3 shrink-0" />}
            active={!!activeSlot && !activeSlot.info.agent_slug}
            onClick={() => {
              setPendingAgent(null);
              talkTo("");
            }}
          />
          {agents.map((agent) => (
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
            />
          ))}
        </div>

        {/* What you already said — the panel's own list, in flow */}
        <ConversationList
          variant="inline"
          liveIds={liveIds}
          activeId={activeSlot?.info.conversation_id || chat.activeSlotId}
          onNew={() => {
            setPendingAgent(null);
            chat.startSession(defaultAgent, defaultMode, server || undefined);
          }}
          onOpen={(meta) => {
            setRailOpen(false);
            chat.resumeConversation(meta.id, {
              agent_key: meta.agent_key,
              mode: meta.mode,
              server_name: meta.server_name || undefined,
              agent_slug: meta.agent_slug,
            });
          }}
        />
      </aside>

      {/* ── Conversation ── */}
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
          {chat.isConnected && (
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-green-500" />
          )}
          {activeSlot ? (
            <BrainPicker
              agents={modelOptions}
              customProviders={customProviders}
              agentBindings={agentBindings}
              selectedAgentKey={activeSlot.info.agent_key}
              selectedAgentSlug={activeSlot.info.agent_slug || ""}
              onSelect={handleSwitch}
              variant="inline"
              disabled={activeSlot.pending || isActiveStreaming}
            />
          ) : (
            <span className="text-sm font-semibold">Chat</span>
          )}
          {activeSlot?.info.server_name && (
            <div className="flex items-center gap-1 rounded border border-[var(--color-border)] bg-[var(--color-surface-hover)] px-2 py-0.5 text-[10px] text-[var(--color-text-muted)]">
              <Server className="h-2.5 w-2.5" />
              <span className="max-w-[120px] truncate">{activeSlot.info.server_name}</span>
            </div>
          )}
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
          modes={modes}
          isStreaming={isActiveStreaming}
          permissionRequest={chat.permissionRequest}
          onResolvePermission={chat.resolvePermission}
          switchError={switchError}
          onDismissSwitchError={() => setSwitchError(null)}
          onSend={(text) =>
            activeSlot && chat.sendMessage(activeSlot.info.slot_id, text)
          }
          onAbort={() => chat.activeSlotId && chat.abortPrompt(chat.activeSlotId)}
          columnClassName="mx-auto w-full max-w-3xl"
          autoFocus
          emptyState={
            <Hero
              agent={heroAgent}
              modelOptions={modelOptions}
              customProviders={customProviders}
              agentBindings={agentBindings}
              defaultAgent={defaultAgent}
              onAsk={(text) => talkTo(heroAgent?.slug || "", text)}
              onPickBrain={(sel) => {
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
}: {
  label: string;
  icon: React.ReactNode;
  live?: boolean;
  active?: boolean;
  title?: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors ${
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
  defaultAgent,
  onAsk,
  onPickBrain,
}: {
  agent: AgentSummary | null | undefined;
  modelOptions: ReturnType<typeof useSessionOptions>["agents"];
  customProviders: ReturnType<typeof useSessionOptions>["customProviders"];
  agentBindings: ReturnType<typeof useSessionOptions>["agentBindings"];
  defaultAgent: string;
  onAsk: (text: string) => void;
  onPickBrain: (selection: BrainSelection) => void;
}) {
  const [selectedKey, setSelectedKey] = useState(defaultAgent);
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
            onSelect={(sel) => {
              if (sel.agentKey !== undefined) setSelectedKey(sel.agentKey);
              onPickBrain(sel);
            }}
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
