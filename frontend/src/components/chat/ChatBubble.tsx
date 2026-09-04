import { useQuery } from "@tanstack/react-query";
import {
  ArrowUpRight,
  Eye,
  MessageSquare,
  Minus,
  ShieldAlert,
} from "lucide-react";
import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { ChatInput } from "@/components/chat/ChatInput";
import { ChatThread } from "@/components/chat/ChatThread";
import { Starters, type Starter } from "@/components/chat/Starters";
import { useBubbleResume } from "@/hooks/useBubbleResume";
import { useChat, useSessionOptions } from "@/hooks/useChat";
import type { ChatSlot } from "@/hooks/useChatSocket";
import { useServer } from "@/hooks/useServer";
import { useStarters } from "@/hooks/useStarters";
import {
  bubbleAgentSlug,
  isAgentPage,
  normalizeAgentSlug,
} from "@/lib/agentSlug";
import { api, CHAT_SLUG } from "@/lib/api";
import { homePath, homeView } from "@/lib/homeView";
import { routeFacts } from "@/lib/pageFacts";
import { BUBBLE_OPEN_KEY } from "@/lib/sessionState";
import { collectViewFacts, renderViewBlock } from "@/lib/viewFacts";

/**
 * A quick chat over whatever page the user is on (FEAT-059).
 *
 * Chrome around the components the workspace already uses — the shell's one
 * socket, `ChatThread`, `ChatInput` — and nothing of its own: no rail, no
 * conversation history, no model picker, no server chip. That absence is the
 * design: the panel that had all of those was deleted in `3a290625` for being
 * a second workspace, and the bubble's only escape hatch is "Open in
 * workspace". It keeps one conversation per bound agent, spawns only when a
 * message is actually sent, and the send carries the page context of that
 * moment.
 *
 * Hidden on the chat view of `/` — the workspace *is* the chat there. That rule
 * carries a second job: `ChatInput` binds ⌘M on `window`, so exactly one
 * composer may be mounted at a time, and hiding here is what guarantees it.
 *
 * Which is why the rule reads the *view* and not the pathname since FEAT-104:
 * `/?view=fleet` mounts the fleet overview, which has no composer in it, so the
 * bubble belongs there like it does on every other page — and hiding it would
 * leave the home with no way to ask anything at all.
 */
export function ChatBubble() {
  const chat = useChat();
  const { server } = useServer();
  const { pathname, search } = useLocation();
  const navigate = useNavigate();

  const [open, setOpen] = useState<boolean>(() => {
    try {
      return localStorage.getItem(BUBBLE_OPEN_KEY) === "1";
    } catch {
      return false;
    }
  });
  // The bubble's own conversation, one per bound agent ("" is Condor).
  // Deliberately not the workspace's *active* conversation: a quick question
  // from /bots must not land in whatever deep specialist chat was open at `/`.
  // On an agent's own page that reasoning inverts — see the adoption below.
  const [slotBySlug, setSlotBySlug] = useState<Record<string, string>>({});
  // Message count last seen with the panel open, per slug — the unread dot.
  const [seen, setSeen] = useState<Record<string, number>>({});

  // Everything below is only needed with the panel open, so the fetch is
  // gated — and carries no refetchInterval: the workspace's own query key, so
  // react-query dedupes when both are mounted.
  const { defaultAgent, agents: modelOptions } = useSessionOptions(open);
  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: api.getAgents,
    enabled: open,
  });

  const slug = bubbleAgentSlug(pathname);
  const storedId = slotBySlug[slug];
  // The spawn renames the tab (client_ref → real slot id), so the stored id
  // is followed through the socket's alias map rather than trusted verbatim.
  const storedSlotId = storedId ? chat.resolveSlotId(storedId) : undefined;
  const slot =
    (storedSlotId &&
      chat.slots.find((s) => s.info.slot_id === storedSlotId)) ||
    adoptableSlot(chat.slots, pathname, slug, chat.activeSlotId);
  const slotId = slot?.info.slot_id;

  // Nothing live to adopt, on the agent's own page, with the panel open: the
  // conversation may still exist on the server. Reattach to it rather than
  // letting the first message mint a second one (CORR-257) — spawning stays
  // the last resort, for an agent with no history at all.
  useBubbleResume(open && !slot && isAgentPage(pathname), slug, (meta) => {
    chat.resumeConversation(
      meta.id,
      {
        agent_key: meta.agent_key,
        server_name: meta.server_name || undefined,
        agent_slug: meta.agent_slug,
      },
      // Read-only with respect to the workspace, exactly as the adoption is:
      // "Back to chat" stays the only gesture that moves `activeSlotId`.
      { focus: false },
    );
    setSlotBySlug((m) => (m[slug] === meta.id ? m : { ...m, [slug]: meta.id }));
  });

  const msgCount = slot?.messages.length ?? 0;

  const facts = routeFacts(pathname, search);
  // Learned openers over the page-shaped ones — up here with the other hooks,
  // and gated on `open` for the same reason the agent list is: a user on
  // /portfolio who never opens the bubble pays nothing for it.
  const starters = useStarters(slug, bubbleStarters(facts?.label), open);

  // Hooks above, bail-out below: `/agents/:slug` keeps the bubble, and so does
  // the fleet overview; the chat view of `/` is the only surface that loses it.
  if (pathname === "/" && homeView(search) === "chat") return null;

  const toggle = (next: boolean) => {
    setOpen(next);
    // Closing is the one moment "seen" needs capturing: the dot is never
    // visible while the panel is open, and everything up to this count was on
    // screen when it closed.
    if (!next && slot) {
      setSeen((m) => (m[slug] === msgCount ? m : { ...m, [slug]: msgCount }));
    }
    try {
      localStorage.setItem(BUBBLE_OPEN_KEY, next ? "1" : "0");
    } catch {
      /* a browser that blocks storage still gets a working bubble */
    }
  };

  const streaming = chat.isSlotStreaming(slotId);
  const unread = !!slot && (msgCount > (seen[slug] ?? 0) || streaming);

  if (!open) {
    return (
      <button
        onClick={() => toggle(true)}
        className="fixed bottom-4 right-4 z-40 flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-primary)] text-[var(--on-primary)] shadow-lg transition-opacity hover:opacity-90"
        title="Ask about this page"
        aria-label="Open quick chat"
      >
        <MessageSquare className="h-5 w-5" />
        {unread && (
          <span className="absolute right-0.5 top-0.5 h-2.5 w-2.5 rounded-full bg-[var(--color-accent)] ring-2 ring-[var(--color-bg)]" />
        )}
      </button>
    );
  }

  const boundSummary = slug ? agents.find((a) => a.slug === slug) : undefined;
  const name = slug
    ? slot?.info.label || boundSummary?.name || slug
    : agents.find((a) => a.slug === CHAT_SLUG)?.name || "Condor";

  const ask = (text: string) => {
    let id = slotId;
    // `slotId` is read off a live slot, so this is belt and braces rather than
    // the load-bearing check it used to be — but a send into a dead id is
    // dropped silently, so the guard stays.
    if (!id || !chat.slots.some((s) => s.info.slot_id === id)) {
      // Same shape as the workspace's `talkTo`: "" asks whoever is bound for
      // their own model, so a bound agent is not forced onto Condor's.
      id = chat.startSession(
        slug ? "" : defaultAgent,
        server || undefined,
        slug || undefined,
        { focus: false },
      );
    }
    setSlotBySlug((m) => (m[slug] === id ? m : { ...m, [slug]: id }));
    chat.sendMessage(id, text);
  };

  const openInWorkspace = () => {
    // Hijacking `activeSlotId` is right here: it is the gesture that asks
    // for the conversation to become the workspace's.
    if (slot && slotId) chat.setActiveSlotId(slotId);
    // By view and not by path: the workspace this button means is the
    // *conversation*, and a bare `/` is the fleet overview since FEAT-104
    // step 3 — it would have carried the hijacked slot to a page that cannot
    // show it.
    navigate(homePath("chat"));
  };

  return (
    <div className="fixed inset-x-2 bottom-2 top-16 z-40 flex flex-col overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] shadow-2xl sm:inset-x-auto sm:top-auto sm:bottom-4 sm:right-4 sm:h-[min(560px,calc(100vh-7rem))] sm:w-[380px]">
      {/* Header: who answers, what is being shared, and the way out. */}
      <div className="flex shrink-0 items-center gap-2 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2">
        <span className="min-w-0 truncate text-sm font-medium text-[var(--color-text)]">
          {name}
        </span>
        {facts && <ContextChip label={facts.label} />}
        <div className="ml-auto flex shrink-0 items-center gap-1">
          {/* Labelled, not a bare glyph: this is the way back to the full
              conversation, and a 16px arrow in a corner was missed by every
              tester who then reached for the page's own chat button and left
              the conversation they were in. The words are what make the two
              buttons tell themselves apart. */}
          <button
            onClick={openInWorkspace}
            className="flex items-center gap-1 whitespace-nowrap rounded px-1.5 py-1 text-[11px] font-medium text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            title="Continue this conversation in the full chat workspace"
          >
            <ArrowUpRight className="h-3.5 w-3.5 shrink-0" />
            Back to chat
          </button>
          <button
            onClick={() => toggle(false)}
            className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            title="Minimize"
            aria-label="Minimize quick chat"
          >
            <Minus className="h-4 w-4" />
          </button>
        </div>
      </div>

      <ChatThread
        slot={slot}
        agents={modelOptions}
        isStreaming={streaming}
        isQueued={chat.isSlotQueued(slotId)}
        permissionRequest={chat.permissionFor(slotId)}
        onResolvePermission={chat.resolvePermission}
        onSend={ask}
        onAbort={() => slotId && chat.abortPrompt(slotId)}
        boundAgent={
          boundSummary
            ? { name: boundSummary.name, description: boundSummary.description }
            : undefined
        }
        // Names a turn's stamped slug; `modelOptions` is the brain list and
        // holds no slugs at all.
        roster={agents}
        columnClassName=""
        autoFocus
        starters={starters}
        emptyState={
          <BubbleHero
            name={name}
            routeLabel={facts?.label}
            starters={starters}
            onAsk={ask}
          />
        }
      />
    </div>
  );
}

/**
 * The live conversation the bubble should show when it has none of its own.
 *
 * Only on `/agents/:slug`, and there it is the conversation bound to this
 * agent, if one is already open. Without it the bubble on an agent's page
 * rendered its empty hero over a conversation with that very agent running two
 * clicks away, and the first message spawned a durable second one beside it
 * (CORR-255) — the common way there being the workspace's own "Knowledge" link.
 *
 * **The workspace's own focus decides, when it has one.** "The conversation
 * with this agent" is only unambiguous while there is one; with several — and
 * opening an older thread from the rail is how a user gets several — array
 * order is not an answer, and answering from it is what made the bubble show a
 * *different* conversation from the tab the user had just been reading. So
 * `activeSlotId`, the workspace's own record of which one that is, is
 * consulted first. Reading it is not the same as moving it: the bubble still
 * must not focus what it adopts.
 *
 * Position is only the fallback, for when the workspace has no focus at all —
 * a reload that landed straight on `/agents/X`. Last match wins there: `slots`
 * is append-ordered by `startSession` / `resumeConversation`, so the newest
 * conversation with this agent is the one the user was most recently in.
 *
 * The slot's own binding is normalized because a conversation resumed from a
 * record written before the slugs were reconciled can still carry the
 * registry's spelling.
 *
 * Read-only with respect to the workspace: the caller must not focus what it
 * adopts. `startSession(..., { focus: false })` and `permissionFor` being a
 * selector exist precisely so a second surface can drive a slot it did not
 * focus, and "Back to chat" stays the only gesture that moves focus.
 *
 * Off an agent page the fallback must not fire, which is why the route — not
 * `slug !== ""` — is the test: `/agents/condor` normalizes to the empty slug
 * and is exactly the case that has to keep working, while `/bots` produces the
 * same empty slug and must keep FEAT-059's rule.
 */
function adoptableSlot(
  slots: ChatSlot[],
  pathname: string,
  slug: string,
  activeSlotId: string | null,
): ChatSlot | null {
  if (!isAgentPage(pathname)) return null;
  const mine = slots.filter(
    (s) => normalizeAgentSlug(s.info.agent_slug) === slug,
  );
  return (
    mine.find((s) => s.info.slot_id === activeSlotId) ?? mine.at(-1) ?? null
  );
}

/**
 * What is being shared before the user asks: the route label on its face, the
 * full block — exactly what will ride the next message — as its tooltip.
 *
 * The block is built on hover, not on render (PERF-296). Inline, it re-ran
 * every registered `useViewFacts` getter — the route table's react-query cache
 * scan plus each page's own formatting of its figures — on every render the
 * chat store causes, which with the panel open is a chunk of every streaming
 * slot and every notification frame, all for a string nobody is looking at.
 * `viewFacts` designs those getters to be called at send time and free while
 * idle; this call site was the exception. Recomputing on each `mouseenter`
 * keeps the "true of this moment" guarantee — the native tooltip delay
 * comfortably outlasts a state re-render, so what appears includes facts that
 * changed since the chip mounted. A `span` is not focusable, so there is no
 * keyboard path to mirror; the value stays in the DOM between hovers so a
 * second hover has something to show while the fresh one lands.
 */
function ContextChip({ label }: { label: string }) {
  const [title, setTitle] = useState<string>();
  return (
    <span
      className="flex min-w-0 shrink items-center gap-1 rounded-full border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-0.5 text-[10px] text-[var(--color-text-muted)]"
      title={title}
      onMouseEnter={() => setTitle(renderViewBlock(collectViewFacts()))}
    >
      <Eye className="h-2.5 w-2.5 shrink-0" />
      <span className="truncate">{label}</span>
    </span>
  );
}

/** Openers for the page the bubble is standing on. */
function bubbleStarters(routeLabel?: string): Starter[] {
  return [
    {
      icon: Eye,
      title: "What am I looking at?",
      hint: routeLabel
        ? `Walk me through this ${routeLabel.toLowerCase()}`
        : "Walk me through what is on this page",
      prompt: routeLabel
        ? `Tell me about this ${routeLabel.toLowerCase()}`
        : "What am I looking at?",
    },
    {
      icon: ShieldAlert,
      title: "Anything need my attention?",
      hint: "Risks, stalled work or numbers that look off",
      prompt: "Anything here that needs my attention?",
    },
  ];
}

/** No conversation yet — a composer and a reason to use it. */
function BubbleHero({
  name,
  routeLabel,
  starters,
  onAsk,
}: {
  name: string;
  routeLabel?: string;
  /** Resolved by the bubble, so the hero and the thread never disagree. */
  starters: Starter[];
  onAsk: (text: string) => void;
}) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center px-3 text-center">
      <MessageSquare className="mb-3 h-8 w-8 text-[var(--color-text-muted)] opacity-30" />
      <p className="text-sm font-medium text-[var(--color-text)]">
        Ask {name} about this page
      </p>
      {routeLabel && (
        <div className="mt-2">
          <ContextChip label={routeLabel} />
        </div>
      )}
      <div className="mt-3 w-full">
        <ChatInput onSend={onAsk} autoFocus />
      </div>
      <Starters starters={starters} onAsk={onAsk} />
    </div>
  );
}
