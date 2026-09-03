import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Bot,
  ClipboardList,
  PanelLeftClose,
  PanelLeftOpen,
  ShieldAlert,
  Wallet,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { AccountDock } from "@/components/chat/AccountDock";
import { deskWasOpen, useAccountPanels } from "@/components/chat/accountPanels";
import { AgentPanel } from "@/components/chat/AgentPanel";
import {
  BrainPicker,
  type BrainSelection,
} from "@/components/chat/BrainPicker";
import { ChatInput } from "@/components/chat/ChatInput";
import { ChatRail } from "@/components/chat/ChatRail";
import { ChatThread } from "@/components/chat/ChatThread";
import { ContextDock } from "@/components/chat/ContextDock";
import { useContextPanels } from "@/components/chat/contextPanels";
import type { LibraryFocus } from "@/components/chat/DockRoutines";
import { SessionTabs } from "@/components/chat/SessionTabs";
import { ShareChatButton } from "@/components/chat/ShareChatButton";
import { WorkspaceRail } from "@/components/chat/WorkspaceRail";
import {
  WorkspacePaneOutlet,
  WorkspacePaneProvider,
} from "@/components/chat/WorkspacePane";
import { Starters, type Starter } from "@/components/chat/Starters";
import { WORKSPACE_BAR } from "@/components/chat/workspaceBar";
import { useBrainSwitch } from "@/hooks/useBrainSwitch";
import { useChat, useSessionOptions } from "@/hooks/useChat";
import { webSessionKey } from "@/hooks/useChatSocket";
import { useServer } from "@/hooks/useServer";
import { useAuth } from "@/lib/auth";
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
 * What is in the workspace pane, if anything.
 *
 * One union rather than three booleans, because the pane is one column:
 * opening the agent panel puts the routine library away and vice versa, and
 * that is the shape of the state rather than a rule three components have to
 * remember (FEAT-081). The library's focus used to live inside `ContextDock`,
 * which could not know about a second occupant.
 *
 * `desk` joined it when the account panels stopped being a column of their own
 * — see `AccountDock`. The three big surfaces of this workspace are the agent,
 * the portfolio and the execution table, and they are exactly the three nobody
 * reads at the same time; making them one union is what stopped the row from
 * asking for more width than a laptop has. Which *sections* the desk is
 * showing is `useAccountPanels`', not this: this only says the desk is on.
 */
type PaneView =
  | { kind: "agent" }
  | { kind: "desk" }
  | { kind: "routines"; focus: LibraryFocus }
  | null;

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
  // Its id is half the session key a run from the dock's library carries.
  const { user } = useAuth();
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
  /**
   * What is in the pane, restored for the one occupant that has a memory.
   *
   * The desk is a workspace fixture — you leave a balance up the way you leave
   * a dock open — while the agent panel and the routine library are things you
   * go and open. So the desk comes back and the other two do not, which is the
   * behaviour the desk had as a column of its own and the reason the sections
   * are recorded at all.
   */
  const [pane, setPane] = useState<PaneView>(() =>
    deskWasOpen() ? { kind: "desk" } : null,
  );

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

  // Read for the same reason and kept out of `talkTo`'s deps for the same one:
  // focus moves on every tab switch, and a `talkTo` re-created there would
  // defeat the rail's `memo` exactly as a `chat.slots` dependency would.
  const activeRef = useRef(chat.activeSlotId);
  useEffect(() => {
    activeRef.current = chat.activeSlotId;
  }, [chat.activeSlotId]);

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
        // Which conversation "mine with this agent" means, when there are
        // several: the one already focused, else the newest. Position alone is
        // not an answer — taking the *first* match sent "Open chat" back to the
        // oldest thread with that agent while the bubble on the page it came
        // from was showing another, and the two surfaces told the user
        // different stories about which conversation they were in. Same rule as
        // `adoptableSlot` in `ChatBubble`, deliberately.
        const mine = slotsRef.current.filter(
          (s) => (s.info.agent_slug || "") === slug,
        );
        const live =
          mine.find((s) => s.info.slot_id === activeRef.current) ?? mine.at(-1);
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
  //
  // `?ask=` rides beside it when the page's brain panel handed something over
  // (FEAT-092) — the request is carried in the URL because navigating is the
  // only channel that page has. It is read under the same guard and stripped in
  // the same call, so a reload does not send it twice; and it is what makes the
  // spawn `fresh`, so a bare `?agent=` keeps today's focus-or-start exactly.
  const consumedAgentParam = useRef(false);
  const agentParam = searchParams.get("agent");
  const askParam = searchParams.get("ask");
  useEffect(() => {
    if (!agentParam || consumedAgentParam.current) return;
    consumedAgentParam.current = true;
    if (askParam) talkTo(agentParam, { intent: "fresh", text: askParam });
    else talkTo(agentParam);
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
  // The tab strip's `+`: always a *second* conversation, never a jump to one
  // that already exists — "new chat" is the whole of what the button says, and
  // focusing an existing tab is what the tabs beside it are for.
  const newChat = useCallback(
    (slug: string, agent: AgentSummary | null) => onTalk(slug, agent, true),
    [onTalk],
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
   * Whose agent panel the rail opens, and what to call them.
   *
   * Both read off the slug, never off `boundAgent`/`pendingAgent` in their own
   * order: an unbound Condor conversation leaves `boundAgent` undefined while a
   * rail click can leave `pendingAgent` pointing at a specialist, so a name
   * resolved separately from the slug titled the panel "Orca LP Expert" over
   * Condor's brain until the fetch landed and corrected it.
   */
  const panelSlug = selectedSlug || CHAT_SLUG;
  const panelAgent = agents.find((a) => a.slug === panelSlug);

  const runningTasks = (delegationData?.delegations ?? []).filter(
    (d) => d.status === "running",
  ).length;

  /**
   * The desk the account panels read.
   *
   * The chat's own server, falling back to the ambient selection: a session
   * pinned to one server must not show another's balance under a chat about it.
   */
  const dockServer = activeSlot?.info.server_name || server || null;
  // Which panels are open lives here rather than in the docks, because the
  // tiles that open them sit on one workspace rail with the agent's. Both docks
  // answer the same way (`useAccountPanels` / `useContextPanels`), so all five
  // tiles behave identically: a click opens or closes the named panel, and a
  // dock with nothing open is not a column at all.
  //
  // The desk's tiles reach through to the pane, which is what makes them
  // exclusive with the agent's without either component knowing about the
  // other: whoever asks for the pane gets it, and the last occupant is gone.
  const account = useAccountPanels({
    server: dockServer,
    open: pane?.kind === "desk",
    onOpenChange: (open) => setPane(open ? { kind: "desk" } : null),
  });
  const context = useContextPanels({
    delegations: delegationData?.delegations ?? [],
    conversationId: activeSlot?.info.conversation_id || "",
    agentSlug: activeSlot?.info.agent_slug || "",
    libraryOpen: pane?.kind === "routines",
  });

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
            escaping to the page, the mirror of what the rail does below `md`.

            `overflow-hidden` because this row is the one place in the workspace
            that can be asked for more width than the window has: five columns,
            each with a floor under which it stops being readable, and no
            arithmetic that makes them all fit at 1280. Whatever cannot fit is
            clipped here rather than spilling past the window — which is what
            used to carry the rail off the right edge (see below). Menus inside
            portal to the body, so nothing that has to escape is clipped by it. */}
        <div className="relative flex min-w-0 flex-1 overflow-hidden">
          {/* The transcript's floor is the other half of the pane's: past `xl`
              the pane can be dragged wider, but never far enough to squeeze the
              conversation out of readability. `xl` is the same 1280 at which the
              split exists at all (see `WorkspacePane`), so below it this is the
              plain `min-w-0` column it has always been. */}
          <div className="flex min-w-0 flex-1 flex-col xl:min-w-[360px]">
            {/* Which sessions are live, and the one door into whoever is
                answering. Nothing else: the agent is named by its own tab and
                again by the panel the button opens — a chip here repeating
                both, plus the model and the server, was the same agent said
                three times across one row. */}
            <div className={`${WORKSPACE_BAR} gap-2 px-3`}>
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
                activeSlotId={chat.activeSlotId}
                isSlotStreaming={chat.isSlotStreaming}
                permissionRequests={chat.permissionRequests}
                onSelect={(slotId) => chat.setActiveSlotId(slotId)}
                // The session ends; the transcript stays on the server, so the
                // conversation is still in the rail and clicking it respawns it.
                onClose={(slotId) => chat.destroySession(slotId)}
                // The strip's own `+`: another conversation, with whoever you
                // pick — the rail is where that used to be, and the rail is a
                // column most readers keep collapsed.
                agents={agents}
                onNew={newChat}
                className="min-w-0 flex-1"
              />
              {/* Share the chat that is open. The rail has the same gesture per
                  row, but only under a hover on a column most readers keep
                  collapsed — so the case that actually comes up, sharing what
                  you are reading, had nothing visible on screen. */}
              <ShareChatButton
                conversationId={
                  activeSlot
                    ? activeSlot.info.conversation_id || activeSlot.info.slot_id
                    : null
                }
              />
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
              // The domain roster, so a turn stamped with a slug can be named
              // by whoever actually took it — `modelOptions` above is the brain
              // list and cannot answer that.
              roster={agents}
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

          {/* What a dock row or the header button opened, beside the
              conversation rather than on top of it — so the agent that produced
              the report is still there to ask about it. */}
          <WorkspacePaneOutlet />

          {pane?.kind === "agent" && (
            <AgentPanel
              slug={panelSlug}
              name={panelAgent?.name || "Condor"}
              // What the conversation runs on, in the panel's own bar — the
              // first thing waiting on the other side of the click.
              slot={activeSlot}
              pendingAgentKey={pendingAgentKey ?? defaultAgent}
              ambientServer={server || ""}
              agents={modelOptions}
              customProviders={customProviders}
              agentBindings={agentBindings}
              isStreaming={isActiveStreaming}
              // With a session this moves the conversation; without one it is
              // the model the next `start_session` carries, which is the same
              // field the hero's picker sets.
              onSelectBrain={(sel) => {
                if (activeSlot) switchBrain(sel);
                else if (sel.agentKey !== undefined)
                  setPendingAgentKey(sel.agentKey);
              }}
              onSelectServer={(name) => {
                if (activeSlot) switchServer(activeSlot.info.slot_id, name);
              }}
              // The pane's routine house is the one FEAT-077 built; the panel
              // hands it over rather than growing a second one.
              onOpenRoutine={(name) =>
                setPane({ kind: "routines", focus: { source: name } })
              }
              // A revision is its own thread: `fresh`, not `focus`, so the
              // request does not land under whatever unrelated thing this
              // agent was last asked. The workspace itself stays put — the
              // detail page has to navigate for this, the chat does not.
              onAskAgent={(text) =>
                talkTo(panelSlug, { intent: "fresh", text })
              }
              onClose={() => setPane(null)}
            />
          )}

          {/* The desk this conversation trades on — the pane's other big
              occupant (FEAT-094, revised): a sheet like the agent panel above,
              at the same split, so the two cannot be on screen together and
              neither has to shrink for the other. It renders nothing unless
              `pane` is the desk, because `account.shown` is empty unless it is;
              there is no second condition to keep in step.

              The chat's own server, falling back to the ambient selection — a
              session pinned to one server must not show another's balance under
              a chat about it, and the panel's bar names the one it is
              reading. */}
          <AccountDock
            server={dockServer}
            shown={account.shown}
            onToggle={account.toggle}
            onClose={account.close}
          />

          <ContextDock
            panels={context}
            delegations={delegationData?.delegations ?? []}
            conversationId={activeSlot?.info.conversation_id || ""}
            agentSlug={activeSlot?.info.agent_slug || ""}
            agentName={boundAgent?.name}
            // A routine launched from the dock's library is this
            // conversation's: it runs on the server the chat is talking to,
            // reports back into it, and is filed under whoever is answering.
            runContext={
              activeSlot && user
                ? {
                    serverName: activeSlot.info.server_name || server || "",
                    sessionKey: webSessionKey(user.id, activeSlot.info.slot_id),
                    agentSlug: activeSlot.info.agent_slug || undefined,
                  }
                : undefined
            }
            library={pane?.kind === "routines" ? pane.focus : null}
            onLibraryChange={(focus) =>
              setPane(focus ? { kind: "routines", focus } : null)
            }
          />
        </div>

        {/* Everything that opens beside the conversation, in one strip on the
            far edge — the agent, then the desk it trades, then this
            conversation. It used to be two strips: this one, and a second the
            context dock drew for itself whenever it was collapsed, sitting
            flush against it. Two identical 40 px rails with a border between
            them reads as one control group that has been cut in half for no
            stated reason, which is exactly what it was.

            The agent leads it: what you are talking to is the first thing you
            change about a conversation, and it lived alone in the top bar as
            a lozenge competing with the tabs for the same row. Below it, ruled
            off, the desk's two tiles and then the conversation's two — three
            groups because they follow three different selectors, and a rule
            rather than a caption because in a 64 px strip the words cost more
            than the separation they were explaining (see `WorkspaceRail`).

            ## Why it is outside the row it belongs to

            It is a sibling of the workspace, not the last column in it. Inside,
            it was the last item of a flex row whose other columns all carry
            floors they refuse to shrink past — chat 360, pane 400, the dock its
            own — and the sum of those floors was larger than a laptop window
            back when the desk was a fourth column too. Flexbox does not report that: it lays the row out
            past the right edge and the last child is simply not on screen. So
            opening the desk and the agent together took the rail away, and with
            it every control for closing what had covered it — a state a reader
            can enter and cannot leave. Out here the rail's 64 px come off the
            top of the row's budget instead, so it is on screen at every width
            and whatever is short is short inside the row, where a scrollbar or
            a squeeze is recoverable. It is also why the dock's overlay below
            `xl` no longer covers it: the overlay is anchored to the row. */}
        <WorkspaceRail
          groups={[
            {
              id: "agent",
              items: [
                {
                  id: "agent",
                  label: "Agent",
                  Icon: Bot,
                  hint: `Tune ${panelAgent?.name || "Condor"} — read and change what this agent is`,
                  active: pane?.kind === "agent",
                  onToggle: () =>
                    setPane((p) =>
                      p?.kind === "agent" ? null : { kind: "agent" },
                    ),
                },
              ],
            },
            { id: "desk", items: account.railItems },
            { id: "conversation", items: context.railItems },
          ]}
        />
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
