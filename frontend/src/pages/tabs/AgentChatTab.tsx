import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Bot,
  ClipboardList,
  PanelLeftClose,
  PanelLeftOpen,
  Rocket,
  ShieldAlert,
  Wallet,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";

import type { KnowledgeTabId } from "@/components/agent/knowledgeTabs";
import { AccountDock } from "@/components/chat/AccountDock";
import {
  DESK_PARAM,
  deskWasOpen,
  useAccountPanels,
} from "@/components/chat/accountPanels";
import {
  lastKnowledgeTab,
  rememberKnowledgeTab,
} from "@/components/agent/knowledgeTabs";
import { AgentPanel } from "@/components/chat/AgentPanel";
import { AgentWiring } from "@/components/chat/AgentWiring";
import {
  BrainPicker,
  type BrainSelection,
} from "@/components/chat/BrainPicker";
import { ChatInput } from "@/components/chat/ChatInput";
import { ChatRail } from "@/components/chat/ChatRail";
import { ChatThread } from "@/components/chat/ChatThread";
import { ContextDock } from "@/components/chat/ContextDock";
import { useContextPanels } from "@/components/chat/contextPanels";
import {
  PANEL_PARAM,
  readPane,
  writePane,
  type PaneView,
} from "@/components/chat/paneUrl";
import type { LibraryFocus } from "@/components/chat/DockRoutines";
import { LoopsPanel } from "@/components/chat/LoopsPanel";
import { ChatIdentity } from "@/components/chat/ChatIdentity";
import { StrategySheet } from "@/components/chat/StrategySheet";
import { usePaneGuard } from "@/components/chat/usePaneGuard";
import { ShareChatButton } from "@/components/chat/ShareChatButton";
import { LayoutPicker } from "@/components/chat/LayoutPicker";
import {
  isSide,
  type LayoutChoice,
  type MainSlot,
  type SlotId,
} from "@/components/chat/layoutSlots";
import { WorkspaceRail, type RailItem } from "@/components/chat/WorkspaceRail";
import {
  WorkspacePaneOutlet,
  WorkspacePaneProvider,
} from "@/components/chat/WorkspacePane";
import { Starters, type Starter } from "@/components/chat/Starters";
import { WORKSPACE_BAR } from "@/components/chat/workspaceBar";
import { useBrainSwitch } from "@/hooks/useBrainSwitch";
import { useChat, useSessionOptions } from "@/hooks/useChat";
import { webSessionKey } from "@/hooks/useChatSocket";
import { loopsOnServer, useLiveLoops } from "@/hooks/useLiveLoops";
import { useServer } from "@/hooks/useServer";
import { useAuth } from "@/lib/auth";
import { AgentHub } from "@/pages/AgentHub";
import { useStarters } from "@/hooks/useStarters";
import { normalizeAgentSlug, slotFor } from "@/lib/agentSlug";
import {
  pickFor,
  sessionAgentKey,
  type PendingPick,
} from "@/lib/sessionAgentKey";
import { agentsQuery } from "@/lib/queryClient";
import { LAYOUT_STACKED_KEY } from "@/lib/sessionState";
import {
  api,
  CHAT_SLUG,
  type AgentSummary,
  type ConversationMeta,
  type Delegation,
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
    icon: Bot,
    title: "What does this agent do?",
    hint: "Strategy, markets, and how it makes money",
    prompt:
      "Explain what this agent does in plain language: the strategy it runs, which markets or pairs it trades, what it is trying to capture, and the main risks. Facts only — don't change configs or place/cancel orders.",
  },
  {
    icon: ClipboardList,
    title: "Am I ready to run?",
    hint: "Missing setup, capital, connectors, or other blockers",
    prompt:
      "Preflight only — don't start trading yet. Check whatever this agent needs (deps, controllers/tools, funding, permissions). List what's OK and what's blocking, in plain language.",
  },
  {
    icon: Rocket,
    title: "Help me start this agent",
    hint: "Test mode, readiness check, then launch if clear",
    prompt:
      "Help me start this agent. Use its test mode settings. Do a short readiness check first, report anything blocking, then start or launch only if clear. Don't switch to full-size or production settings unless I ask.",
  },
  {
    icon: Activity,
    title: "What is the status?",
    hint: "Running or not, last activity, key metrics, issues",
    prompt:
      "Give a concise status of what this agent is doing now: running or not, last meaningful activity, key metrics it tracks, and anything that looks wrong or still warming up. Facts only — don't change configs or place/cancel orders unless I ask.",
  },
];

/** Go to my conversation with them, or start another one regardless. */
type TalkIntent = "focus" | "fresh";

/**
 * The delegation list before the first poll lands, one identity: a `?? []`
 * fallback is a fresh array per render, which would break `ContextDock`'s memo
 * on every stream flush (PERF-394). Once loaded, react-query's structural
 * sharing keeps the array stable between polls.
 */
const NO_DELEGATIONS: Delegation[] = [];

/**
 * The chat workspace — what `/` opens on.
 *
 * A rail of who you can talk to and what you already said, a conversation
 * beside it, and a dock of the work it set in motion. Since the fleet grid was
 * removed this is the whole of the workspace: the rail is how you switch who
 * you are talking to and start another conversation, which is the gesture the
 * old header selector could not do.
 */
/**
 * The two panels of a free "Chat + 2" (see `stack` in the tab): `top` is the
 * pane, `bottom` the slot under it (`dir: "col"`, stacked) or beside it
 * (`dir: "row"`, side by side).
 */
type StackPick = { top: SlotId; bottom: SlotId; dir: "col" | "row" };

const SLOT_IDS: SlotId[] = ["agent", "portfolio", "execution", "loops", "tasks", "routines"];

/** The stacked layout this browser last had, or `null` for none (or anything unreadable). */
function readStack(): StackPick | null {
  try {
    const raw = JSON.parse(localStorage.getItem(LAYOUT_STACKED_KEY) || "null");
    if (
      raw &&
      SLOT_IDS.includes(raw.top) &&
      SLOT_IDS.includes(raw.bottom) &&
      raw.top !== raw.bottom
    ) {
      return { top: raw.top, bottom: raw.bottom, dir: raw.dir === "row" ? "row" : "col" };
    }
  } catch {
    // Unreadable or the old "1" flag: start side by side.
  }
  return null;
}

export function AgentChatTab() {
  const chat = useChat();
  const { server } = useServer();
  // Its id is half the session key a run from the dock's library carries.
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  // `/hub` is this same workspace with the Hub in the conversation's column
  // (FEAT-153), so picking anything that means "a conversation" leaves it.
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const onHub = pathname === "/hub";
  const leaveHub = useCallback(() => {
    if (onHub) navigate("/");
  }, [onHub, navigate]);
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
   *
   * Scoped to the agent the hero or panel was showing when it was picked, and
   * only ever sent to a spawn with that agent (`sessionAgentKey`) — a bare key
   * rode every later spawn and rewrote each specialist's AGENT.md model.
   */
  const [pendingPick, setPendingPick] = useState<PendingPick>(null);
  /**
   * What is in the pane — read from `?panel=`, so Escape and browser Back both
   * close it and a panel can be sent to someone (FEAT-103).
   *
   * The routine library's focus rides beside it rather than in the URL: it
   * changes several times a minute while somebody browses reports, and a
   * parameter per click is a history stack nobody can press Back through. See
   * `paneUrl.ts`.
   */
  const [libraryFocus, setLibraryFocus] = useState<LibraryFocus>({});
  const pane: PaneView = readPane(searchParams, libraryFocus);

  /**
   * Put `next` in the pane, unguarded — every caller goes through `openPane`
   * below, which asks first when this would drop an agent panel's unsaved
   * editor (CORR-395).
   */
  const applyPane = (next: PaneView) => {
    if (next?.kind === "routines") setLibraryFocus(next.focus);
    // The agent panel comes back on the section it was left on: which one that
    // is survives the close in this browser, because closing the pane takes the
    // whole address — `?tab=` included — with it. A caller that names a section
    // still wins; this only fills in the ones that do not (the rail's tile, an
    // Execution row, the strategy sheet handing the pane back).
    let resolved = next;
    if (resolved?.kind === "agent") {
      if (resolved.tab) rememberKnowledgeTab(resolved.tab);
      else resolved = { ...resolved, tab: lastKnowledgeTab() };
    }
    setSearchParams(writePane(searchParams, resolved));
  };

  /**
   * The desk is the one occupant with a memory.
   *
   * It is a workspace fixture — you leave a balance up the way you leave a dock
   * open — while the agent panel and the routine library are things you go and
   * open. So it comes back on a bare `/`, once, as a `replace` rather than a
   * history entry: restoring a preference is not a step anybody navigated.
   */
  const restoredDesk = useRef(false);
  useEffect(() => {
    if (restoredDesk.current) return;
    restoredDesk.current = true;
    if (!searchParams.get(PANEL_PARAM) && deskWasOpen()) {
      setSearchParams(writePane(searchParams, { kind: "desk" }), {
        replace: true,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The shared roster: key and cadence live in `agentsQuery`.
  const { data: agents = [], isLoading: agentsLoading } = useQuery(
    agentsQuery(),
  );
  const queryClient = useQueryClient();
  // Retires a cloned agent's sidebar "New" badge the instant a chat with it
  // actually starts — wherever that start came from (the rail row, the
  // hero's composer, an agent page's "Chat" button, `?agent=` on load), since
  // all of them fund into `talkTo`'s `startSession` branch below. Applied to
  // the cache directly rather than invalidated: a refetch would still show
  // the badge for however long the round trip takes, and the whole point is
  // that starting the chat is what makes it go away.
  const markAgentSeen = useMutation({
    mutationFn: (slug: string) => api.markAgentSeen(slug),
    onMutate: (slug: string) => {
      queryClient.setQueryData<AgentSummary[]>(agentsQuery().queryKey, (prev) =>
        prev?.map((a) => (a.slug === slug ? { ...a, is_new: false } : a)),
      );
    },
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
    (
      agentSlug: string,
      opts?: { intent?: TalkIntent; text?: string; files?: File[] },
    ) => {
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
        // different stories about which conversation they were in.
        const live = slotFor(slotsRef.current, slug, activeRef.current);
        if (live) {
          chat.setActiveSlotId(live.info.slot_id);
          if (opts?.text || opts?.files?.length)
            chat.sendMessage(live.info.slot_id, opts.text ?? "", opts.files);
          return;
        }
      }
      // A conversation is genuinely starting — the one moment that counts as
      // "opened it" for a cloned agent's "New" badge, wherever this call came
      // from.
      if (agents.find((a) => a.slug === slug)?.is_new) markAgentSeen.mutate(slug);
      const slotId = chat.startSession(
        // `""` asks whoever is bound for their own model; only an unbound chat
        // needs a model named. Volunteering `defaultAgent` here is what used to
        // claim an override the user never made, so a bound Agent ran on
        // Condor's model instead of its own.
        sessionAgentKey(pendingPick, slug, defaultAgent),
        server || undefined,
        slug || undefined,
      );
      // The tab is on screen before the spawn is; the outbox flushes this the
      // moment the session lands, which is what makes a new chat feel warm.
      // Attachments wait in that same queue and are uploaded at flush, so an
      // image can open a conversation that does not exist yet.
      if (opts?.text || opts?.files?.length)
        chat.sendMessage(slotId, opts.text ?? "", opts.files);
    },
    [
      agents,
      chat.sendMessage,
      chat.setActiveSlotId,
      chat.startSession,
      defaultAgent,
      markAgentSeen,
      pendingPick,
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
    // Reset on every clearing of the param, not just once on mount: this
    // effect deletes `agent` itself right after consuming it, so a *second*
    // "Start chatting" (Hub -> `/?agent=A` -> Hub -> `/?agent=B`) must find
    // the guard open again rather than stuck true from the first agent.
    if (!agentParam) {
      consumedAgentParam.current = false;
      return;
    }
    if (consumedAgentParam.current) return;
    consumedAgentParam.current = true;
    if (askParam) talkTo(agentParam, { intent: "fresh", text: askParam });
    else talkTo(agentParam);
    // Only the two it consumed: `?panel=` is the pane's state now, and
    // clearing the whole query string would close whatever was open.
    const rest = new URLSearchParams(searchParams);
    rest.delete("agent");
    rest.delete("ask");
    setSearchParams(rest, { replace: true });
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

  // What the session tabs used to carry, now on the conversation rows: which
  // chats are answering in the background and which wait on an approval.
  // Joined keys again, so the rail's `memo` holds while a reply streams.
  const convIdOf = (s: (typeof chat.slots)[number]) =>
    s.info.conversation_id || s.info.slot_id;
  // A turn is in flight from the moment it is sent, not from its first
  // fragment: `isSlotStreaming` only turns on when the answer starts, so a
  // chat whose message is on the wire (or whose send is queued) is working
  // too, and must neither read as idle nor be closed behind the user. Tracked
  // by the socket rather than read off the transcript, so a turn stopped or
  // ended before its first token stops reading as "Answering".
  const inFlight = (s: (typeof chat.slots)[number]) =>
    !!s.pending ||
    chat.isSlotStreaming(s.info.slot_id) ||
    chat.isSlotQueued(s.info.slot_id) ||
    chat.isSlotAwaiting(s.info.slot_id);
  const busyKey = chat.slots
    .filter(inFlight)
    .map(convIdOf)
    .join("|");
  const waitingKey = chat.slots
    .filter((s) => chat.permissionRequests[s.info.slot_id])
    .map(convIdOf)
    .join("|");
  const busyIds = useMemo(
    () => new Set(busyKey ? busyKey.split("|") : []),
    [busyKey],
  );
  const waitingIds = useMemo(
    () => new Set(waitingKey ? waitingKey.split("|") : []),
    [waitingKey],
  );

  /**
   * One chat on screen, like Claude: no session tabs to collect. Opening
   * another conversation (or a new one) ends the sessions left behind once
   * they have nothing to do — not answering, not waiting on an approval. One
   * still working keeps running and shows on its row; it is ended the same
   * way once it finishes. The transcript stays on the server, so clicking
   * the row picks the conversation back up; a chat closed without a word in
   * it is discarded by the server.
   */
  const destroySession = chat.destroySession;
  const activeSlotId = chat.activeSlotId;
  const idleBehindKey = chat.slots
    .filter(
      (s) =>
        s.info.slot_id !== activeSlotId &&
        !inFlight(s) &&
        !chat.permissionRequests[s.info.slot_id],
    )
    .map((s) => s.info.slot_id)
    .join("|");
  useEffect(() => {
    if (!idleBehindKey) return;
    for (const id of idleBehindKey.split("|")) destroySession(id);
  }, [idleBehindKey, destroySession]);

  // The list is the only way between chats now, so a chat appears in it (and
  // re-sorts, and gets its title) when an answer starts or ends rather than
  // on the next visit.
  useEffect(() => {
    queryClient.invalidateQueries({ queryKey: ["conversations"] });
  }, [busyKey, queryClient]);

  /**
   * Chats on screen that the server does not list yet. A conversation's first
   * turn is written when its prompt ends, and the list leaves out
   * conversations with nothing in them — so a new chat is absent from the rail
   * for as long as its first answer runs or waits on an approval. With the
   * session tabs gone the rail is the only way back to it, so it is listed
   * from the session itself until the server has it.
   */
  const liveDraftKey = chat.slots
    .filter((s) => s.messages.some((m) => m.role === "user"))
    .map((s) =>
      [
        convIdOf(s),
        s.info.agent_slug || "",
        (s.messages.find((m) => m.role === "user")?.text || "").slice(0, 80).replace(/[|\n]/g, " "),
      ].join("\u0001"),
    )
    .join("|");
  const liveDrafts = useMemo<ConversationMeta[]>(() => {
    if (!liveDraftKey) return [];
    const now = new Date().toISOString();
    return liveDraftKey.split("|").map((row) => {
      const [id, agent_slug, title] = row.split("\u0001");
      return {
        id,
        user_id: 0,
        surface: "web",
        title,
        agent_key: "",
        agent_slug,
        server_name: null,
        created_at: now,
        updated_at: now,
        turn_count: 1,
        last_snippet: "",
      };
    });
  }, [liveDraftKey]);

  // Stable identities, so the rail's `memo` holds while an answer streams.
  // `selectedSlug` is a string and the rest are `useCallback`s on genuinely
  // stable deps, so these only change when the rail's answer actually changes.
  const openFresh = useCallback(() => {
    leaveHub();
    talkTo(selectedSlug, { intent: "fresh" });
  }, [leaveHub, talkTo, selectedSlug]);
  const openConversation = useCallback(
    (meta: ConversationMeta) => {
      leaveHub();
      chat.resumeConversation(meta.id, {
        agent_key: meta.agent_key,
        server_name: meta.server_name || undefined,
        agent_slug: meta.agent_slug,
      });
    },
    [leaveHub, chat.resumeConversation],
  );
  // The rail says who was picked and the tab remembers it, because the hero it
  // colours is the tab's, not the rail's.
  const onTalk = useCallback(
    (slug: string, agent: AgentSummary | null, fresh?: boolean) => {
      leaveHub();
      setPendingAgent(agent);
      talkTo(slug, fresh ? { intent: "fresh" } : undefined);
    },
    [leaveHub, talkTo],
  );
  const closeRail = useCallback(() => setRailOpen(false), []);
  // The server already stopped the chat's session on delete; the slot on
  // screen is closed too, or it would stay open on a chat that no longer exists.
  // Read through a ref: `chat.slots` changes with every streamed fragment, and
  // the rail's `memo` must not.
  const slotsNow = useRef(chat.slots);
  useEffect(() => {
    slotsNow.current = chat.slots;
  });
  const onDeletedConversation = useCallback(
    (id: string) => {
      const slot = slotsNow.current.find((s) => convIdOf(s) === id);
      if (slot) destroySession(slot.info.slot_id);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- convIdOf is pure
    [destroySession],
  );

  const boundAgent = activeSlot?.info.agent_slug
    ? agents.find((a) => a.slug === activeSlot.info.agent_slug)
    : undefined;
  const heroAgent = activeSlot ? undefined : pendingAgent;
  // The open chat's title, from the rail's own cached list; a chat with no
  // answer yet is titled by its first message, or not at all.
  const { data: conversationList = [] } = useQuery<ConversationMeta[]>({
    queryKey: ["conversations"],
    queryFn: () => api.listConversations(),
  });
  const activeConvId = activeSlot?.info.conversation_id || activeSlot?.info.slot_id;
  const activeTitle =
    conversationList.find((c) => c.id === activeConvId)?.title ||
    activeSlot?.messages.find((m) => m.role === "user")?.text?.slice(0, 80) ||
    "";

  // What the agent in focus has learned this user asks it for, over the
  // static list. Two calls because the two empty states can be looking at
  // different agents at once: the thread renders the slot that exists, the
  // hero the one the rail is pointing at before any slot does.
  const slotSlug = activeSlot?.info.agent_slug ?? "";
  const slotStarters = useStarters(
    slotSlug,
    slotSlug ? AGENT_STARTERS : CONDOR_STARTERS,
    true,
    slotSlug ? 4 : 3,
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

  /**
   * Whose panel is actually in the pane (FEAT-114).
   *
   * The conversation's, unless `?panel=agent` carries a slug of its own — which
   * is what an Execution row clicks. `pane.slug ?? panelSlug` is the whole
   * rule, so every link ever written to a bare `?panel=agent` keeps meaning
   * "the agent I am talking to".
   */
  const openSlug = (pane?.kind === "agent" && pane.slug) || panelSlug;
  /**
   * Every door out of the agent panel — its own close, the rail's tile, a desk
   * tile, the routine library, a strategy card, an Execution row naming another
   * agent — is a call to this, so this is where an unsaved editor is asked
   * about (CORR-395).
   */
  const paneGuard = usePaneGuard({ pane, panelSlug, apply: applyPane });
  const openPane = paneGuard.openPane;
  const openAgent = agents.find((a) => a.slug === openSlug);

  /**
   * The agent panel's handlers, each under one identity while its inputs hold
   * (PERF-393).
   *
   * `AgentKnowledge` is `memo`'d, and inline arrows here were new on every
   * 50 ms stream flush, so the panel re-parsed the whole AGENT.md through
   * ReactMarkdown twenty times a second while an answer streamed beside it.
   * `openPane` and `talkTo` are stable and the rest are strings. Not keyed on
   * `pane`: `readPane` builds a fresh object every render, so the section
   * change rebuilds the agent pane from its scalar slug instead.
   */
  const paneSlug = pane?.kind === "agent" ? pane.slug : undefined;
  const onPanelTabChange = useCallback(
    (t: KnowledgeTabId) =>
      openPane({
        kind: "agent",
        ...(paneSlug ? { slug: paneSlug } : {}),
        tab: t,
      }),
    [openPane, paneSlug],
  );
  // The pane's routine house is the one FEAT-077 built; the panel hands it
  // over rather than growing a second one.
  const onPanelOpenRoutine = useCallback(
    (name: string) => openPane({ kind: "routines", focus: { source: name } }),
    [openPane],
  );
  // And a strategy card to the workbench sheet, which never went anywhere —
  // FEAT-117 only stopped the panel from opening it.
  const onPanelOpenStrategy = useCallback(
    (sslug: string) =>
      openPane({ kind: "strategy", agentSlug: openSlug, strategySlug: sslug }),
    [openPane, openSlug],
  );
  // A revision is its own thread: `fresh`, not `focus`, so the request does not
  // land under whatever unrelated thing this agent was last asked. The
  // workspace itself stays put — the detail page has to navigate for this, the
  // chat does not.
  const onPanelAskAgent = useCallback(
    (text: string) => talkTo(openSlug, { intent: "fresh", text }),
    [talkTo, openSlug],
  );

  const delegations = delegationData?.delegations ?? NO_DELEGATIONS;
  const runningTasks = delegations.filter(
    (d) => d.status === "running",
  ).length;

  /**
   * The desk the account panels read.
   *
   * The chat's own server, falling back to the ambient selection: a session
   * pinned to one server must not show another's balance under a chat about it.
   */
  const dockServer = activeSlot?.info.server_name || server || null;
  // Every strategy with a live tick loop, from `fleet-map` (FEAT-123) — read
  // here, ahead of the desk, so both the desk's own Loops section (scoped to
  // `dockServer`) and its rail tile's badge can be handed a number without
  // either one polling twice. Same `["fleet-map"]` key `useFleetData` already
  // holds, so a reader with any fleet surface open shares the poll.
  const { loops, isLoading: loopsLoading } = useLiveLoops();
  const loopsHere = useMemo(
    () => loopsOnServer(loops, agents, dockServer),
    [loops, agents, dockServer],
  );
  // Which panels are open lives here rather than in the docks, because the
  // tiles that open them sit on one workspace rail with the agent's. Both docks
  // answer the same way (`useAccountPanels` / `useContextPanels`), so all six
  // tiles behave identically: a click opens or closes the named panel, and a
  // dock with nothing open is not a column at all.
  //
  // The desk's tiles reach through to the pane, which is what makes them
  // exclusive with the agent's without either component knowing about the
  // other: whoever asks for the pane gets it, and the last occupant is gone.
  const account = useAccountPanels({
    server: dockServer,
    open: pane?.kind === "desk",
    // The desk is addressable now: `/fleet` redirects here, and `?desk=` is
    // what makes that redirect open the Execution section rather than whatever
    // this browser happened to have recorded (FEAT-114).
    desk: searchParams.get(DESK_PARAM),
    onOpenChange: (open) => openPane(open ? { kind: "desk" } : null),
    // Settled only once the roster has too: `loopsOnServer` cannot place a
    // loop without its agent, so a roster still loading reads as zero here
    // and would otherwise decide the Loops default from it (CORR-431).
    loopsActivity: {
      count: loopsHere.length,
      isLoading: loopsLoading || agentsLoading,
    },
  });
  const conversationId = activeSlot?.info.conversation_id || "";
  const context = useContextPanels({
    delegations,
    conversationId,
    agentSlug: activeSlot?.info.agent_slug || "",
    libraryOpen: pane?.kind === "routines",
  });
  // The dock is memoised (PERF-394), so what it is handed must hold across a
  // stream flush. `activeSlot` is a new object on every flush, so the run
  // context is keyed on the scalars it reads, never on the slot itself.
  const runSlotId = activeSlot?.info.slot_id;
  const runSlotServer = activeSlot?.info.server_name;
  const runAgentSlug = activeSlot?.info.agent_slug;
  const runUserId = user?.id;
  const runContext = useMemo(
    () =>
      runSlotId !== undefined && runUserId !== undefined
        ? {
            serverName: runSlotServer || server || "",
            sessionKey: webSessionKey(runUserId, runSlotId),
            agentSlug: runAgentSlug || undefined,
          }
        : undefined,
    [runSlotId, runSlotServer, runAgentSlug, runUserId, server],
  );
  const onLibraryChange = useCallback(
    (focus: LibraryFocus | null) =>
      openPane(focus ? { kind: "routines", focus } : null),
    [openPane],
  );

  /**
   * "Chat + 2" — the layout picker's free arrangement: the room beside the
   * chat split in two, stacked or side by side, and *any* panel in either
   * half. Side by side used to be the wide pane plus the conversation's side
   * column, which only took Tasks or Routines; it is now this same split
   * turned on its side.
   *
   * The top half is still the pane — the agent or the desk claim it exactly as
   * they always have, and Tasks/Routines draw into it without a claim. The
   * bottom half is the slot under it, filled by a second instance of whichever
   * panel was picked (`slot="below"`). So this records the two picks, and the
   * pane and the docks are driven to match them. Remembered per browser: it
   * is how this person likes the screen divided.
   */
  const [stack, setStackState] = useState<StackPick | null>(readStack);
  const setStack = (next: StackPick | null) => {
    setStackState(next);
    try {
      if (next) localStorage.setItem(LAYOUT_STACKED_KEY, JSON.stringify(next));
      else localStorage.removeItem(LAYOUT_STACKED_KEY);
    } catch {
      /* private mode: the arrangement just lasts the session */
    }
  };
  // The agent panel in the bottom half keeps its own section — the pane's
  // section lives on the URL and belongs to the top.
  const [belowAgentTab, setBelowAgentTab] = useState<KnowledgeTabId | undefined>(undefined);
  // The lower Agent panel's unsaved edit. The pane's own panel is guarded by
  // `usePaneGuard`; this one is unmounted by layout changes instead, so every
  // layout change that would drop it asks first.
  const [belowDirty, setBelowDirty] = useState(false);
  const keepsBelowDraft = () =>
    !belowDirty ||
    stack?.bottom !== "agent" ||
    window.confirm("The lower Agent panel has unsaved changes. Discard them?");

  /**
   * The layout picker's view of the row: what fills the wide pane, what fills
   * the side column, and so how many panels are beside the chat. Derived from
   * the pane and the docks, so the rail tiles and the picker can never disagree.
   */
  const layoutMain: MainSlot | null =
    pane?.kind === "agent" || pane?.kind === "strategy"
      ? "agent"
      : pane?.kind === "desk"
        ? (account.shown[0] ?? null)
        : null;
  const layoutSide = context.shown[0] ?? null;
  // What the top half really shows: the pane when something holds it (a strategy
  // sheet opened from the agent still reads as the agent), else the side panel
  // that was picked for it.
  const stackTop: SlotId | null = !stack
    ? null
    : pane
      ? layoutMain
      : isSide(stack.top)
        ? stack.top
        : null;
  const stackBottom: SlotId | null =
    stack && stackTop && stack.bottom !== stackTop ? stack.bottom : null;
  const stackActive = !!stack && !!stackTop;
  const stackDir = stack?.dir ?? "col";
  const panelCount = (pane ? 1 : 0) + (context.shown.length > 0 ? 1 : 0);
  const layoutChoice: LayoutChoice = stackActive
    ? stackDir === "row"
      ? "side"
      : "stack"
    : panelCount === 0
      ? "chat"
      : panelCount === 1
        ? "one"
        : "side";
  // `then` runs once the pick has happened: a guarded pane can still say no.
  const pickMain = (id: MainSlot, then?: () => void) => {
    if (id === "agent") return openPane({ kind: "agent" }, then);
    account.showOnly(id);
    then?.();
  };
  /**
   * Put `id` in the top half: the pane for agent/desk, nothing in it for a side
   * panel. Emptying the pane goes through the desk's own close, which also
   * forgets its sections — otherwise `deskWasOpen` would put the desk back in
   * the top half on the next load, over the Tasks/Routines picked for it.
   */
  const applyTop = (id: SlotId) => {
    if (!isSide(id)) return pickMain(id);
    account.close();
    openPane(null);
  };
  /**
   * Bring a remembered split back on arrival. Its picks are restored from
   * storage, but the first panel lives in the pane, which follows the URL —
   * arriving through the Agents link (no `?panel=`) left the pane empty, so
   * the split read as inactive and never came back. Once per mount, and only
   * when the URL named no panel of its own.
   */
  const restoredSplit = useRef(false);
  useEffect(() => {
    if (restoredSplit.current) return;
    restoredSplit.current = true;
    if (stack && !pane && !isSide(stack.top)) pickMain(stack.top as MainSlot);
    // Mount only: later changes are the reader's own.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  /**
   * Show exactly `id` beside the chat, outside the stacked layout. Nothing
   * moves until the pane's guard accepts: Cancel on unsaved edits leaves the
   * screen exactly as it was. `then` finishes the caller's own change.
   */
  const showOne = (id: SlotId, then?: () => void) => {
    if (isSide(id)) {
      openPane(null, () => {
        context.showOnly(id);
        then?.();
      });
    } else {
      pickMain(id, () => {
        context.closeAll();
        then?.();
      });
    }
  };
  const enterStack = (dir: StackPick["dir"]) => {
    const top: SlotId = layoutMain ?? layoutSide ?? "agent";
    const bottom: SlotId =
      layoutMain && layoutSide
        ? layoutSide
        : (["tasks", "portfolio", "agent"] as SlotId[]).find((x) => x !== top)!;
    applyTop(top);
    setStack({ top, bottom, dir });
  };
  /**
   * The split as it stands, for a pick to start from. Two panels opened from
   * the rail's tiles (the pane plus the side column) read as "side by side"
   * without being the free split yet; the first pick in the picker turns them
   * into it, keeping both where they are.
   */
  const currentSplit = (): StackPick & { top: SlotId } =>
    stackActive && stack
      ? { ...stack, top: stackTop! }
      : { top: layoutMain ?? "agent", bottom: layoutSide ?? "tasks", dir: "row" };
  const pickStackTop = (id: SlotId) => {
    if (!keepsBelowDraft()) return;
    const cur = currentSplit();
    // Picking what the other half holds swaps them rather than showing one twice.
    const bottom = id === cur.bottom ? cur.top : cur.bottom;
    applyTop(id);
    setStack({ top: id, bottom, dir: cur.dir });
  };
  const pickStackBottom = (id: SlotId) => {
    if (id !== "agent" && !keepsBelowDraft()) return;
    const cur = currentSplit();
    if (id === cur.top) {
      applyTop(cur.bottom);
      setStack({ top: cur.bottom, bottom: id, dir: cur.dir });
    } else {
      if (!stackActive) applyTop(cur.top);
      setStack({ top: cur.top, bottom: id, dir: cur.dir });
    }
  };
  /** Close one half: the other stays, as the one panel beside the chat. */
  const closeStackHalf = (half: "top" | "bottom") => {
    if (!keepsBelowDraft()) return;
    const keep = half === "top" ? stackBottom : stackTop;
    const leaveSplit = () => setStack(null);
    if (keep) showOne(keep, leaveSplit);
    else openPane(null, leaveSplit);
  };
  const setLayoutChoice = (choice: LayoutChoice) => {
    // Turning the split on its side keeps both panels mounted; only leaving it drops one.
    if ((choice === "chat" || choice === "one") && !keepsBelowDraft()) return;
    if (choice === "stack" || choice === "side") {
      const dir = choice === "stack" ? "col" : "row";
      // Already split: turn it on its side, both panels staying put.
      if (stackActive && stack) setStack({ ...stack, top: stackTop!, dir });
      else enterStack(dir);
      return;
    }
    if (stackActive) {
      const halves = [stackTop, stackBottom].filter(Boolean) as SlotId[];
      // The split is forgotten only once the pane's guard has let it go.
      const leaveSplit = () => setStack(null);
      if (choice === "chat") {
        openPane(null, () => {
          context.closeAll();
          leaveSplit();
        });
      } else {
        showOne(halves[0] ?? "agent", leaveSplit);
      }
      return;
    }
    if (choice === "chat") {
      openPane(null, () => context.closeAll());
    } else {
      // Keep whichever panel is already up; with both up, the wide one stays.
      if (pane && context.shown.length > 0) context.closeAll();
      else if (!pane && context.shown.length === 0) pickMain("agent");
    }
  };
  /**
   * The rail's tiles, while stacked: a tile on screen closes its half, any
   * other tile takes the bottom half. Outside the stacked layout they are the
   * tiles they have always been.
   */
  const stackTile = (item: RailItem): RailItem => {
    if (!stackActive) return item;
    const id = item.id as SlotId;
    const half = id === stackTop ? "top" : id === stackBottom ? "bottom" : null;
    return {
      ...item,
      active: half !== null,
      onToggle: () => (half ? closeStackHalf(half) : pickStackBottom(id)),
    };
  };
  // Tasks/Routines in the stacked halves: the dock's own state is left alone
  // (it is the side column's), and each half gets the one pane it was picked for.
  const stackedContext = useMemo(
    () =>
      ({
        ...context,
        shown: stackTop && isSide(stackTop) ? [stackTop] : [],
        closeAll: () => closeStackHalf("top"),
        toggle: () => closeStackHalf("top"),
      }) as typeof context,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [context, stackTop, stackBottom],
  );
  const belowContext = useMemo(
    () =>
      ({
        ...context,
        shown: stackBottom && isSide(stackBottom) ? [stackBottom] : [],
        closeAll: () => closeStackHalf("bottom"),
        toggle: () => closeStackHalf("bottom"),
      }) as typeof context,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [context, stackTop, stackBottom],
  );

  const agentWiring = (panelOwner: string) => (
    <AgentWiring
      slot={activeSlot}
      pendingAgentKey={pickFor(pendingPick, normalizeAgentSlug(panelOwner)) ?? defaultAgent}
      ambientServer={server || ""}
      agents={modelOptions}
      customProviders={customProviders}
      agentBindings={agentBindings}
      isStreaming={isActiveStreaming}
      // With a session this moves the conversation; without one it is the
      // model the next `start_session` carries, which is the same field the
      // hero's picker sets.
      onSelectBrain={(sel) => {
        if (activeSlot) switchBrain(sel);
        else if (sel.agentKey !== undefined)
          setPendingPick({ slug: normalizeAgentSlug(panelOwner), key: sel.agentKey });
      }}
      onSelectServer={(name) => {
        if (activeSlot) switchServer(activeSlot.info.slot_id, name);
      }}
    />
  );

  return (
    <WorkspacePaneProvider>
      <div className="flex h-full min-h-0">
        <ChatRail
          agents={agents}
          runningTasks={runningTasks}
          activeSlug={activeSlot?.info.agent_slug || ""}
          hasSession={!!activeSlot}
          liveIds={liveIds}
          busyIds={busyIds}
          waitingIds={waitingIds}
          liveDrafts={liveDrafts}
          activeId={
            onHub ? null : activeSlot?.info.conversation_id || chat.activeSlotId
          }
          hubActive={onHub}
          open={railOpen}
          onClose={closeRail}
          onTalk={onTalk}
          onNew={openFresh}
          onOpenConversation={openConversation}
          onDeletedConversation={onDeletedConversation}
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
            {/* Which sessions are live: the rail toggle (narrow screens only)
                and the session tabs. Nothing else, and no door to the agent
                panel — that is the `WorkspaceRail` Agent tile (also reachable
                from an Execution row and from closing a strategy sheet, all via
                `openPane({ kind: "agent" })`). The agent is named by its own
                tab and again by that panel — a chip here repeating both, plus
                the model and the server, was the same agent said three times
                across one row.

                Not on the Hub: there is no conversation to tab between, so this
                bar would be either empty chrome or a strip of tabs with none of
                them lit — the same reason Settings and every other full-pane
                screen carries no bar here at all. The rail's mobile toggle lives
                inside `AgentHub`'s own header instead, so narrow screens keep a
                door to the rail without resurrecting the tab strip for it. */}
            {onHub ? null : (
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
              {/* Who this chat is with, and what it is about — the place
                  the session tabs held. The list on the left is how you move
                  between chats; this says which one you are in. */}
              {!onHub && (
                <ChatIdentity
                  agentName={
                    activeSlot
                      ? activeSlot.info.agent_slug
                        ? (boundAgent?.name ?? activeSlot.info.agent_slug)
                        : (agents.find((a) => a.slug === CHAT_SLUG)?.name ?? "Condor")
                      : (heroAgent?.name ?? "Condor")
                  }
                  bound={!!(activeSlot ? activeSlot.info.agent_slug : heroAgent)}
                  title={activeTitle}
                  busy={isActiveStreaming || !!activeSlot?.pending}
                  // Renamable once the conversation is saved, which is once
                  // something has been said in it.
                  conversationId={
                    conversationList.some((c) => c.id === activeConvId)
                      ? activeConvId
                      : undefined
                  }
                />
              )}
            </div>
            )}

            {onHub ? (
              <div className="min-h-0 flex-1 overflow-y-auto">
                <AgentHub onOpenRail={() => setRailOpen(true)} />
              </div>
            ) : (
            <ChatThread
              slot={activeSlot}
              agents={modelOptions}
              isStreaming={isActiveStreaming}
              isQueued={chat.isSlotQueued(chat.activeSlotId)}
              permissionRequest={chat.permissionFor(chat.activeSlotId)}
              onResolvePermission={chat.resolvePermission}
              switchError={switchError}
              onDismissSwitchError={dismissSwitchError}
              onRetryModel={(agentKey, messageId) =>
                switchBrain({ agentKey }, messageId)
              }
              defaultAgentKey={defaultAgent}
              agentBindings={agentBindings}
              onSend={(text, files) =>
                activeSlot &&
                chat.sendMessage(activeSlot.info.slot_id, text, files)
              }
              onAbort={() =>
                chat.activeSlotId && chat.abortPrompt(chat.activeSlotId)
              }
              // Share the chat that is open, from the box that is open with
              // it. The rail has the same gesture per row, but only under a
              // hover on a column most readers keep collapsed — so the case
              // that actually comes up, sharing what you are reading, had
              // nothing visible on screen. It sat in the bar above for a
              // while, where it read as an action on the session strip beside
              // it rather than on the transcript below; the composer is the
              // one place where "this chat" needs no explaining.
              composerLeading={
                <ShareChatButton
                  conversationId={
                    activeSlot
                      ? activeSlot.info.conversation_id ||
                        activeSlot.info.slot_id
                      : null
                  }
                />
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
                  selectedKey={
                    pickFor(pendingPick, normalizeAgentSlug(heroAgent?.slug)) ??
                    defaultAgent
                  }
                  onAsk={(text, files) =>
                    talkTo(heroAgent?.slug || "", { text, files })
                  }
                  // The picker moves the model and nothing else; who answers is
                  // the rail's question, and it is already answered by the row
                  // the user highlighted.
                  onPickBrain={(sel) => {
                    if (sel.agentKey !== undefined)
                      setPendingPick({
                        slug: normalizeAgentSlug(heroAgent?.slug),
                        key: sel.agentKey,
                      });
                  }}
                />
              }
            />
            )}
          </div>

          {/* Whatever has claimed the pane — the agent panel, a strategy
              sheet, the desk, the routine library — beside the conversation
              rather than on top of it, so the agent that produced the report is
              still there to ask about it. The doors are the `WorkspaceRail`
              tiles at the far edge (plus an Execution row in the desk and a
              pasted `?panel=`); the bar above the transcript carries none. */}
          <WorkspacePaneOutlet
            // The column is split, with its seam, only while both halves hold
            // something (the layout picker's stacked "Chat + 2").
            split={stackActive && stackBottom ? stackDir : null}
          />

          {pane?.kind === "agent" && (
            <AgentPanel
              // Keyed on whose panel it is: an Execution row re-slugging the
              // pane must remount the sections, or the previous agent's open
              // editor carries over, seeded with the next agent's text.
              key={openSlug}
              slug={openSlug}
              name={openAgent?.name || "Condor"}
              // What the conversation runs on, in the panel's own bar — the
              // first thing waiting on the other side of the click.
              wiring={agentWiring(openSlug)}
              // Which section is open lives on the home's query string
              // (FEAT-118), so Back steps through the seven and a pane open on
              // Tools can be sent to somebody.
              tab={pane.tab}
              onTabChange={onPanelTabChange}
              onOpenRoutine={onPanelOpenRoutine}
              onOpenStrategy={onPanelOpenStrategy}
              onAskAgent={onPanelAskAgent}
              onDirtyChange={paneGuard.onPanelDirtyChange}
              onClose={() => openPane(null)}
            />
          )}
          {paneGuard.dialog}

          {/* The stacked layout's bottom half, when the agent was picked for it:
              the conversation's agent, in its own instance under the pane. */}
          {stackBottom === "agent" && (
            <AgentPanel
              key={`below:${panelSlug}`}
              slot="below"
              slug={panelSlug}
              name={panelAgent?.name || "Condor"}
              wiring={agentWiring(panelSlug)}
              tab={belowAgentTab}
              onTabChange={setBelowAgentTab}
              onDirtyChange={setBelowDirty}
              onOpenRoutine={onPanelOpenRoutine}
              onOpenStrategy={(sslug) =>
                openPane({ kind: "strategy", agentSlug: panelSlug, strategySlug: sslug })
              }
              onAskAgent={(text) => talkTo(panelSlug, { intent: "fresh", text })}
              onClose={() => closeStackHalf("bottom")}
            />
          )}

          {/* One of the agent's loops, on its own — opened by a strategy card
              in the panel, or from a pasted `?panel=strategy&loop=`. It
              replaces the panel rather than stacking on it, and closing it
              puts the panel back rather than leaving an empty row. */}
          {pane?.kind === "strategy" && (
            <StrategySheet
              key={`${pane.agentSlug}/${pane.strategySlug}`}
              pane={pane}
              onPane={openPane}
              onClose={() =>
                openPane({
                  kind: "agent",
                  // Back to the agent this sheet was opened from, which is not
                  // necessarily the conversation's since FEAT-114.
                  ...(pane.agentSlug === panelSlug
                    ? {}
                    : { slug: pane.agentSlug }),
                })
              }
            />
          )}

          {/* Every live loop, across every agent — every server, not just this
              chat's. The rail's own "Loops" tile stopped opening this
              (FEAT-1xx): a fleet's own loops now read as a section of its
              own desk, at the desk's width, rather than a second overlay
              stacked on top of one already open. This global cross-server
              view is kept reachable at `?panel=loops` for whoever still has
              it bookmarked; nothing in this screen links to it anymore. */}
          {pane?.kind === "loops" && (
            <LoopsPanel
              loops={loops}
              isLoading={loopsLoading}
              agents={agents}
              onOpenLoop={(agentSlug, strategySlug) =>
                openPane({ kind: "strategy", agentSlug, strategySlug })
              }
              onClose={() => openPane(null)}
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
            // An Execution row names an agent; this is where it opens
            // (FEAT-114). The desk hands the pane over exactly as the rail's
            // agent tile does, so the two cannot be on screen at once.
            onOpenAgent={(slug) => openPane({ kind: "agent", slug })}
            // A Loops card names a loop; this is the same hand-off
            // `LoopsPanel`'s own cards make (FEAT-1xx).
            onOpenLoop={(agentSlug, strategySlug) =>
              openPane({ kind: "strategy", agentSlug, strategySlug })
            }
          />
          {/* The stacked layout's bottom half, when a desk section was picked
              for it — a second desk, showing just that one section. */}
          {stackBottom && !isSide(stackBottom) && stackBottom !== "agent" && (
            <AccountDock
              slot="below"
              server={dockServer}
              shown={[stackBottom]}
              onToggle={() => closeStackHalf("bottom")}
              onClose={() => closeStackHalf("bottom")}
              onOpenAgent={(slug) => openPane({ kind: "agent", slug })}
              onOpenLoop={(agentSlug, strategySlug) =>
                openPane({ kind: "strategy", agentSlug, strategySlug })
              }
            />
          )}

          {/* "This conversation" has nothing to say about a column that has
              no conversation in it — the dock would show only its own empty
              states (no delegation, no routine run) beside the catalogue,
              chrome for a subject that is not there. Hidden here rather than
              inside `ContextDock`: it is about the chat this column no longer
              holds while the Hub has it. */}
          {!onHub && (
            <ContextDock
              panels={stackActive ? stackedContext : context}
              delegations={delegations}
              conversationId={activeSlot?.info.conversation_id || ""}
              agentSlug={activeSlot?.info.agent_slug || ""}
              agentName={boundAgent?.name}
              // A routine launched from the dock's library is this
              // conversation's: it runs on the server the chat is talking to,
              // reports back into it, and is filed under whoever is answering.
              runContext={runContext}
              library={pane?.kind === "routines" ? pane.focus : null}
              onLibraryChange={onLibraryChange}
              // Stacked, Tasks/Routines picked for the top half draw in the pane.
              stackSlot={stackActive ? "top" : null}
            />
          )}
          {!onHub && stackBottom && isSide(stackBottom) && (
            <ContextDock
              key="below"
              panels={belowContext}
              delegations={delegations}
              conversationId={activeSlot?.info.conversation_id || ""}
              agentSlug={activeSlot?.info.agent_slug || ""}
              agentName={boundAgent?.name}
              runContext={runContext}
              // The library opens in the pane; the top dock owns that sheet.
              library={null}
              onLibraryChange={onLibraryChange}
              stackSlot="bottom"
            />
          )}
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
            off, the desk's tiles (Portfolio, Execution and Loops, FEAT-1xx)
            and then the conversation's two — three groups because they follow
            three different selectors, and a rule rather than a caption because
            in a 64 px strip the words cost more than the separation they were
            explaining (see `WorkspaceRail`).

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
          header={
            <LayoutPicker
              choice={layoutChoice}
              main={layoutMain}
              side={layoutSide}
              // The free split's two panels — or, for two opened from the tiles
              // (pane + side column), those two, which a pick turns into it.
              stackTop={stackActive ? stackTop : layoutChoice === "side" ? layoutMain : null}
              stackBottom={stackActive ? stackBottom : layoutChoice === "side" ? layoutSide : null}
              onStackTop={pickStackTop}
              onStackBottom={pickStackBottom}
              onChoice={setLayoutChoice}
              // In "chat + 1" there is one panel: picking another replaces it,
              // whichever column either lives in.
              onMain={(id) => (layoutChoice === "one" ? showOne(id) : pickMain(id))}
              onSide={(id) => {
                // In "chat + 1" the side column *is* the one panel, so filling
                // it from the picker gives the wide pane back.
                if (layoutChoice === "one" && pane) {
                  openPane(null, () => context.showOnly(id));
                } else {
                  context.showOnly(id);
                }
              }}
            />
          }
          groups={[
            {
              id: "agent",
              items: [
                {
                  id: "agent",
                  label: "Agent",
                  Icon: Bot,
                  // What the pane opens is the agent's seven Being sections
                  // again (FEAT-118) — what it *is*. What it did is a click
                  // away, behind the panel's own Workspace door.
                  hint: `Open ${panelAgent?.name || "Condor"} — what it knows and what it runs`,
                  // A strategy opened from the panel is still the agent's
                  // subject, and the tile that opened it must not read as off
                  // while it is on screen.
                  active: pane?.kind === "agent" || pane?.kind === "strategy",
                  onToggle: () =>
                    openPane(
                      pane?.kind === "agent" || pane?.kind === "strategy"
                        ? null
                        : { kind: "agent" },
                    ),
                },
              ].map(stackTile),
            },
            // Portfolio, Execution and now Loops (FEAT-1xx) — one group, one
            // selector (the server), so the tile that used to open a global
            // "every agent" Loops sheet of its own is gone: `account.railItems`
            // already carries a Loops tile scoped to this server, with the
            // same badge the old one showed, right beside the other two.
            { id: "desk", items: account.railItems.map(stackTile) },
            /* Tasks and Routines, and no Deployed: the conversation's ledger
               went with FEAT-118, and FEAT-119 gives it a home on the run
               screen where a conversation is one more kind of run. Until then
               the fleet browser answers it, filtered to this chat's own
               deployments. */
            { id: "conversation", items: context.railItems.map(stackTile) },
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
  /** `files` only when the user attached something; `Starters` sends words. */
  onAsk: (text: string, files?: File[]) => void;
  onPickBrain: (selection: BrainSelection) => void;
}) {
  const starters = useStarters(
    agent?.slug || "",
    agent ? AGENT_STARTERS : CONDOR_STARTERS,
    true,
    agent ? 4 : 3,
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
          {/* There is no conversation to key a draft on yet, so it hangs off
              whoever the first message will go to: leaving the page before
              pressing Enter is exactly when the words are least recoverable,
              because nothing has been sent anywhere. */}
          <ChatInput
            onSend={onAsk}
            autoFocus
            draftKey={`new:${agent?.slug || "condor"}`}
          />
        </div>

        <Starters starters={starters} onAsk={onAsk} className="mt-4" />
      </div>
    </div>
  );
}
