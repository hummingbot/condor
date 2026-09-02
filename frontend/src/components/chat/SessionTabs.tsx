import { AlertTriangle, Bot, Loader2, X, Zap } from "lucide-react";

import type { ChatSlot } from "@/hooks/useChatSocket";

/**
 * The live sessions, as tabs.
 *
 * `useChatSocket` can hold several sessions at once, each with its own
 * transcript, streaming flag and permission request. This is where they become
 * visible: one tab per slot, the active one underlined, a spinner while a
 * background chat is still answering, a ⚠ when one is holding a tool call that
 * only its own conversation can approve — and an X, which is the only way to
 * end a session at all.
 *
 * Every tab carries a status marker in a fixed slot at its head: a green dot
 * while the session is live and idle, a spinner in its place while it answers,
 * a hollow dot when the backend reports no subprocess behind the slot.
 * That is the rail's own vocabulary — the green dot on a conversation row —
 * brought up here, because the strip used to read as a row of plain labels and
 * gave no hint that each one is a process that is alive right now. The dots
 * carry that on their own; a "N live" count chip beside them only restated
 * what the row already shows. The hollow dot exists because the green one was
 * being shown for a slot the roster had already reported as reaped, which made
 * the tab's only status affordance lie about the one thing it reports.
 *
 * It renders inside the header row rather than above it, and fills it: the
 * tabs stretch to the bar's full height so the active one's underline lands on
 * the bar's own bottom border, the way a tab strip is supposed to sit. The
 * active tab already names who is answering, so a second stacked bar would say
 * the same thing twice and cost the transcript ~36 px on every screen.
 */
export function SessionTabs({
  slots,
  activeSlotId,
  isSlotStreaming,
  permissionRequests,
  onSelect,
  onClose,
  className = "",
}: {
  slots: ChatSlot[];
  activeSlotId: string | null;
  isSlotStreaming: (slotId: string | null) => boolean;
  /** Keyed by slot id — a request in a background chat is only visible here. */
  permissionRequests: Record<string, unknown>;
  onSelect: (slotId: string) => void;
  onClose: (slotId: string) => void;
  className?: string;
}) {
  if (slots.length === 0) return null;

  // Two chats with the same agent render identically otherwise, which makes a
  // `fresh` session indistinguishable from the one it was started beside. The
  // first keeps the bare name; the rest are numbered from `#2`.
  const groupSizes = new Map<string, number>();
  for (const slot of slots) {
    const key = groupKey(slot);
    groupSizes.set(key, (groupSizes.get(key) || 0) + 1);
  }
  const seen = new Map<string, number>();

  return (
    <div
      className={`flex items-stretch gap-1 self-stretch overflow-x-auto ${className}`}
    >
      {slots.map((slot) => {
        const key = groupKey(slot);
        const ordinal = (seen.get(key) || 0) + 1;
        seen.set(key, ordinal);
        const suffix =
          (groupSizes.get(key) || 0) > 1 && ordinal > 1 ? ` #${ordinal}` : "";

        return (
          <SessionTab
            key={slot.info.slot_id}
            slot={slot}
            suffix={suffix}
            isActive={slot.info.slot_id === activeSlotId}
            isStreaming={isSlotStreaming(slot.info.slot_id)}
            // A confirmation is only answerable from the conversation that
            // raised it, so the tab is where a request in a background chat
            // becomes visible at all.
            needsApproval={Boolean(permissionRequests[slot.info.slot_id])}
            onClick={() => onSelect(slot.info.slot_id)}
            onClose={() => onClose(slot.info.slot_id)}
          />
        );
      })}
    </div>
  );
}

/** What makes two tabs "the same agent" for numbering. */
function groupKey(slot: ChatSlot): string {
  return slot.info.agent_slug || slot.info.agent_key;
}

/**
 * A session is alive: a subprocess is behind the slot right now.
 *
 * `--color-green` rather than a literal `green-500`, so the colour-blind theme
 * — where green is redefined as blue — still gets a hue it can separate from
 * the yellow that means "waiting on you".
 */
function LiveDot() {
  return (
    <span className="relative flex h-1.5 w-1.5 shrink-0">
      <span className="relative h-1.5 w-1.5 rounded-full bg-[var(--color-green)]" />
    </span>
  );
}

/**
 * The conversation is intact, but nothing is running behind it.
 *
 * An idle detach, an eviction, a subprocess that died — the tab stays and its
 * transcript still hydrates, so this is not the absence of a session but a
 * session that has to be reattached, which the next message does on its way
 * through. Hollow rather than a second hue: it reads as "the dot, unlit" at
 * the same size and on the same tokens, and costs the palette nothing.
 */
function DetachedDot() {
  return (
    <span className="relative flex h-1.5 w-1.5 shrink-0">
      <span className="relative h-1.5 w-1.5 rounded-full border border-[var(--color-text-muted)]" />
    </span>
  );
}

// ── Session Tab ──

function SessionTab({
  slot,
  suffix,
  isActive,
  isStreaming,
  needsApproval,
  onClick,
  onClose,
}: {
  slot: ChatSlot;
  /** `#2`, `#3` … when this agent has more than one chat open. */
  suffix: string;
  isActive: boolean;
  isStreaming: boolean;
  /** This conversation is holding a tool call that is waiting on the user. */
  needsApproval?: boolean;
  onClick: () => void;
  onClose: () => void;
}) {
  // The tab answers one question: *who* you are talking to. The model and the
  // server belong to the agent panel, one click away in the dock, so neither is
  // restated here. An unbound chat is "Condor" — the same word the rail uses
  // for the same conversation.
  const agentShort = slot.info.label || slot.info.agent_slug || "Condor";
  const TabIcon = slot.info.agent_slug ? Bot : Zap;
  const busy = isStreaming || slot.pending;
  // Strictly `=== false`: a roster from a backend older than `alive` omits the
  // key entirely, and an unknown state has to keep reading as it did before.
  const detached = slot.info.alive === false;
  const status = busy
    ? "Answering"
    : detached
      ? "Detached — reattaches on your next message"
      : "Session live";
  // The server is not on the tab, and nothing else in this row carries it any
  // more, so the tooltip is where that fact stays reachable without a click.
  const title = slot.info.server_name
    ? `${agentShort}${suffix} — ${slot.info.server_name}`
    : `${agentShort}${suffix}`;

  return (
    <button
      onClick={onClick}
      title={title}
      // The active tab borrows the rail's own highlight — a primary tint and
      // primary text — so the tab and the rail row for the same conversation
      // read as one selection rather than two unrelated ones. The bottom
      // border is on every tab, transparent when inactive, so switching does
      // not shift the row by 2 px.
      className={`group relative flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-t border-b-2 px-3 text-xs transition-colors ${
        isActive
          ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10 font-medium text-[var(--color-primary)]"
          : "border-transparent text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
      }`}
    >
      {/* One marker, in a fixed-width slot so the label does not jump when a
          steady dot becomes a spinner: green while the session is live and
          idle, spinning while it is answering, hollow while it is detached. */}
      <span
        className="flex h-3 w-3 shrink-0 items-center justify-center"
        title={status}
        aria-label={status}
      >
        {busy ? (
          <Loader2 className="h-3 w-3 animate-spin text-[var(--color-primary)]" />
        ) : detached ? (
          <DetachedDot />
        ) : (
          <LiveDot />
        )}
      </span>
      <TabIcon className="h-3 w-3 shrink-0" />
      <span className="max-w-[140px] truncate">
        {agentShort}
        {suffix}
      </span>
      {needsApproval && (
        <AlertTriangle
          className="h-3 w-3 shrink-0 text-[var(--color-yellow)]"
          aria-label="Waiting for your approval"
        />
      )}
      <span
        role="button"
        tabIndex={0}
        aria-label="Close session"
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            e.stopPropagation();
            onClose();
          }
        }}
        className="ml-0.5 rounded p-0.5 opacity-0 transition-opacity hover:bg-[var(--color-surface-hover)] group-hover:opacity-100 focus-visible:opacity-100 group-focus-within:opacity-100"
      >
        <X className="h-2.5 w-2.5" />
      </span>
    </button>
  );
}
