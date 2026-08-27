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
import { useChat, useSessionOptions } from "@/hooks/useChat";
import { useServer } from "@/hooks/useServer";
import { api, CHAT_SLUG } from "@/lib/api";
import { routeFacts } from "@/lib/pageFacts";
import { collectViewFacts, renderViewBlock } from "@/lib/viewFacts";

const OPEN_KEY = "condor_bubble_open";

/** Whose bubble this is: the agent whose page you are on, else Condor. */
function bubbleAgentSlug(pathname: string): string {
  const m = pathname.match(/^\/agents\/([^/]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

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
 * Hidden on `/` — the workspace *is* the chat there. That rule carries a
 * second job: `ChatInput` binds ⌘M on `window`, so exactly one composer may
 * be mounted at a time, and hiding here is what guarantees it.
 */
export function ChatBubble() {
  const chat = useChat();
  const { server } = useServer();
  const { pathname, search } = useLocation();
  const navigate = useNavigate();

  const [open, setOpen] = useState<boolean>(() => {
    try {
      return localStorage.getItem(OPEN_KEY) === "1";
    } catch {
      return false;
    }
  });
  // The bubble's own conversation, one per bound agent ("" is Condor).
  // Deliberately not the workspace's active conversation: a quick question
  // from /bots must not land in whatever deep specialist chat was open at `/`.
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
  const slotId = storedId ? chat.resolveSlotId(storedId) : undefined;
  const slot =
    (slotId && chat.slots.find((s) => s.info.slot_id === slotId)) || null;

  const msgCount = slot?.messages.length ?? 0;

  // Hooks above, bail-out below: `/agents/:slug` keeps the bubble, `/` alone
  // loses it.
  if (pathname === "/") return null;

  const toggle = (next: boolean) => {
    setOpen(next);
    // Closing is the one moment "seen" needs capturing: the dot is never
    // visible while the panel is open, and everything up to this count was on
    // screen when it closed.
    if (!next && slot) {
      setSeen((m) => (m[slug] === msgCount ? m : { ...m, [slug]: msgCount }));
    }
    try {
      localStorage.setItem(OPEN_KEY, next ? "1" : "0");
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
        className="fixed bottom-4 right-4 z-40 flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-primary)] text-white shadow-lg transition-opacity hover:opacity-90"
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

  const facts = routeFacts(pathname, search);

  const ask = (text: string) => {
    let id = slotId;
    // The liveness check matters: the workspace's session tabs can close a
    // slot the bubble is holding, and a send into a dead id would be dropped.
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
    navigate("/");
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
        columnClassName=""
        autoFocus
        starters={bubbleStarters(facts?.label)}
        emptyState={
          <BubbleHero name={name} routeLabel={facts?.label} onAsk={ask} />
        }
      />
    </div>
  );
}

/**
 * What is being shared before the user asks: the route label on its face, the
 * full block — exactly what will ride the next message — as its tooltip.
 */
function ContextChip({ label }: { label: string }) {
  return (
    <span
      className="flex min-w-0 shrink items-center gap-1 rounded-full border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-0.5 text-[10px] text-[var(--color-text-muted)]"
      title={renderViewBlock(collectViewFacts())}
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
  onAsk,
}: {
  name: string;
  routeLabel?: string;
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
      <Starters starters={bubbleStarters(routeLabel)} onAsk={onAsk} />
    </div>
  );
}
