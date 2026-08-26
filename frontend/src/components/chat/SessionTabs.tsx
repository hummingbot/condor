import { AlertTriangle, Bot, Loader2, X, Zap } from "lucide-react";

import type { ChatSlot } from "@/hooks/useChatSocket";
import type { ChatAgentOption } from "@/lib/api";

import { resolveAgentLabel } from "./ChatThread";

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
 * Closing a tab ends the session and nothing else: the transcript stays on the
 * server, so the conversation remains in the rail and clicking it there
 * respawns it.
 *
 * It renders inside the header row rather than above it. The active tab
 * already names who is answering, which is the identity row's own job, so a
 * second stacked bar would say the same thing twice and cost the transcript
 * ~36 px on every screen.
 */
export function SessionTabs({
  slots,
  agents,
  activeSlotId,
  isSlotStreaming,
  permissionRequests,
  onSelect,
  onClose,
  className = "",
}: {
  slots: ChatSlot[];
  agents: ChatAgentOption[];
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
    <div className={`flex items-center gap-0 overflow-x-auto ${className}`}>
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
            agents={agents}
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

/** Shorten agent label for tab display */
function shortAgentLabel(agentKey: string, agents: ChatAgentOption[]): string {
  const full = resolveAgentLabel(agentKey, agents);
  // Shorten common names
  const shortMap: Record<string, string> = {
    "Claude Code": "Claude",
    "Gemini CLI": "Gemini",
    "GitHub Copilot CLI": "Copilot",
    "ChatGPT Codex": "Codex",
  };
  return shortMap[full] || (full.length > 12 ? full.slice(0, 12) + "..." : full);
}

// ── Session Tab ──

function SessionTab({
  slot,
  agents,
  suffix,
  isActive,
  isStreaming,
  needsApproval,
  onClick,
  onClose,
}: {
  slot: ChatSlot;
  agents: ChatAgentOption[];
  /** `#2`, `#3` … when this agent has more than one chat open. */
  suffix: string;
  isActive: boolean;
  isStreaming: boolean;
  /** This conversation is holding a tool call that is waiting on the user. */
  needsApproval?: boolean;
  onClick: () => void;
  onClose: () => void;
}) {
  // A bound Agent names the tab; only an unbound one falls back to the model,
  // which is the same rule the header's picker follows.
  const agentShort = slot.info.agent_slug
    ? slot.info.label || slot.info.agent_slug
    : shortAgentLabel(slot.info.agent_key, agents);
  const TabIcon = slot.info.agent_slug ? Bot : Zap;

  return (
    <button
      onClick={onClick}
      className={`group relative flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded px-3 py-1.5 text-xs transition-colors ${
        isActive
          ? "bg-[var(--color-bg)] text-[var(--color-text)]"
          : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
      }`}
    >
      {isActive && (
        <div className="absolute bottom-0 left-1 right-1 h-0.5 rounded-full bg-[var(--color-primary)]" />
      )}
      <TabIcon className="h-3 w-3 shrink-0" />
      <span className="max-w-[140px] truncate">
        {agentShort}
        {suffix}
        {slot.info.server_name && (
          <span className="text-[var(--color-text-muted)]"> · {slot.info.server_name}</span>
        )}
      </span>
      {needsApproval && (
        <AlertTriangle
          className="h-3 w-3 shrink-0 text-[var(--color-yellow)]"
          aria-label="Waiting for your approval"
        />
      )}
      {(isStreaming || slot.pending) && (
        <Loader2 className="h-3 w-3 shrink-0 animate-spin text-[var(--color-primary)]" />
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
