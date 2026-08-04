import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Bot,
  History,
  Loader2,
  MessageSquare,
  Minus,
  Plus,
  X,
  Zap,
} from "lucide-react";
import { type ChatSlot } from "@/hooks/useChatSocket";
import { useChat, useSessionOptions } from "@/hooks/useChat";
import { ChatThread, resolveAgentLabel } from "./ChatThread";
import { BrainPicker, type BrainSelection } from "./BrainPicker";
import { ChatSessionIdentity } from "./ChatSessionIdentity";
import { ConversationList } from "./ConversationList";
import {
  type AgentBindingOption,
  type ChatAgentOption,
  type CustomProvider,
} from "@/lib/api";
import { useServer } from "@/hooks/useServer";
import { useResizeDrag } from "@/hooks/useResizeDrag";
import { useBrainSwitch } from "@/hooks/useBrainSwitch";

const MIN_WIDTH = 360;
const MAX_WIDTH = 1200;
const DEFAULT_WIDTH = 480;

interface ChatPanelProps {
  isOpen: boolean;
  onToggle: (open: boolean | ((prev: boolean) => boolean)) => void;
}

export function ChatPanel({ isOpen, onToggle }: ChatPanelProps) {
  const chat = useChat();
  const { server } = useServer();
  const panelRef = useRef<HTMLDivElement>(null);

  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const [showNewMenu, setShowNewMenu] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const { switchBrain, switchError, dismissSwitchError } = useBrainSwitch();

  // Chat options, shared with the `/agents` workspace on one query key: the
  // panel only asks for them once it has been opened.
  const { agents, customProviders, agentBindings, defaultAgent } =
    useSessionOptions(isOpen);

  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [selectedSlug, setSelectedSlug] = useState("");

  // Keyboard shortcut: Cmd+K (Mac) / Ctrl+K (other) to toggle panel
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        onToggle((prev) => !prev);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onToggle]);

  // Connect when panel opens
  useEffect(() => {
    if (isOpen) chat.connect();
  }, [isOpen, chat.connect]);

  // Resize drag handling
  const { onMouseDown: startDrag, isDragging } = useResizeDrag({
    axis: "x",
    value: width,
    onChange: setWidth,
    min: MIN_WIDTH,
    max: MAX_WIDTH,
    direction: "inverted",
  });

  const handleNewSession = (agentKey: string, agentSlug?: string) => {
    onToggle(true);
    chat.startSession(agentKey, server || undefined, agentSlug);
    setShowNewMenu(false);
    setShowHistory(false);
    setSelectedAgent(null);
    setSelectedSlug("");
  };

  const activeSlot = chat.activeSlot;
  const isActiveStreaming = chat.isSlotStreaming(chat.activeSlotId);

  // Resolve effective selections for the new-session menu
  const effectiveAgent = selectedAgent || defaultAgent;
  // Who a new chat is with: the conversation you are in, else whoever the
  // new-session menu has picked. The workspace's rail answers the same
  // question from its selected row, so the two views stay consistent.
  const effectiveSlug = activeSlot?.info.agent_slug ?? selectedSlug;

  // Both views of one list, keyed the same way: the tabs are the conversations
  // attached right now, the drawer is all of them.
  const liveIds = new Set(
    chat.slots.map((s) => s.info.conversation_id || s.info.slot_id),
  );

  return (
    <>
      {/* Panel -- slides from right, below navbar */}
      <div
        ref={panelRef}
        style={{ width: isOpen ? width : 0 }}
        className={`fixed right-0 top-12 z-[60] flex h-[calc(100%-3rem)] flex-col border-l border-[var(--color-border)] bg-[var(--color-bg)] shadow-xl ${
          isDragging ? "" : "transition-[width] duration-200 ease-out"
        } ${isOpen ? "" : "overflow-hidden border-l-0"}`}
      >
        {/* Resize handle */}
        {isOpen && (
          <div
            onMouseDown={startDrag}
            className={`group/resize absolute left-0 top-0 z-10 flex h-full w-1.5 cursor-col-resize items-center justify-center transition-colors hover:bg-[var(--color-primary)]/10 ${
              isDragging ? "bg-[var(--color-primary)]/20" : ""
            }`}
          >
            <div className="h-12 w-px rounded bg-amber-400/60 group-hover/resize:bg-amber-400 transition-colors" />
          </div>
        )}

        {/* Header */}
        <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2">
          <div className="flex min-w-0 items-center gap-2">
            <MessageSquare className="h-4 w-4 shrink-0 text-[var(--color-primary)]" />
            {/* Who is answering and where it runs — the same strip the
                `/agents` workspace shows. */}
            <ChatSessionIdentity
              slot={activeSlot}
              agents={agents}
              customProviders={customProviders}
              agentBindings={agentBindings}
              isStreaming={isActiveStreaming}
              onSelectBrain={switchBrain}
              placeholder={
                <>
                  <span className="text-sm font-semibold whitespace-nowrap">Agent</span>
                  <kbd className="rounded bg-[var(--color-surface-hover)] px-1.5 py-0.5 text-[10px] font-medium tracking-wide text-[var(--color-text-muted)] border border-[var(--color-border)]">
                    ⌘K
                  </kbd>
                </>
              }
            />
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setShowHistory(true)}
              className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title="Conversations"
            >
              <History className="h-4 w-4" />
            </button>
            {/* New session button */}
            <div className="relative">
              <button
                onClick={() => setShowNewMenu((v) => !v)}
                className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                title="New session"
              >
                <Plus className="h-4 w-4" />
              </button>
              {showNewMenu && (
                <NewSessionMenu
                  agents={agents}
                  customProviders={customProviders}
                  agentBindings={agentBindings}
                  selectedAgent={effectiveAgent}
                  selectedAgentSlug={selectedSlug}
                  onSelectBrain={(sel) => {
                    if (sel.agentSlug !== undefined) setSelectedSlug(sel.agentSlug);
                    if (sel.agentKey !== undefined) setSelectedAgent(sel.agentKey);
                  }}
                  onStart={(agent) => handleNewSession(agent, selectedSlug || undefined)}
                  onClose={() => setShowNewMenu(false)}
                />
              )}
            </div>
            <button
              onClick={() => onToggle(false)}
              className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title="Minimize"
            >
              <Minus className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Session tabs */}
        {chat.slots.length > 0 && (
          <div className="flex items-center gap-0 overflow-x-auto border-b border-[var(--color-border)] bg-[var(--color-surface)]">
            {chat.slots.map((slot) => (
              <SessionTab
                key={slot.info.slot_id}
                slot={slot}
                agents={agents}
                isActive={slot.info.slot_id === chat.activeSlotId}
                isStreaming={chat.isSlotStreaming(slot.info.slot_id)}
                // A confirmation is only answerable from the conversation that
                // raised it, so the tab is where a request in a background chat
                // becomes visible at all.
                needsApproval={Boolean(chat.permissionRequests[slot.info.slot_id])}
                onClick={() => chat.setActiveSlotId(slot.info.slot_id)}
                onClose={() => chat.destroySession(slot.info.slot_id)}
              />
            ))}
          </div>
        )}

        {/* The conversation — the same component the `/agents` workspace
            renders, at a different width. */}
        <ChatThread
          slot={activeSlot}
          agents={agents}
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
          emptyState={
            <EmptyState
              agents={agents}
              customProviders={customProviders}
              agentBindings={agentBindings}
              defaultAgent={defaultAgent}
              onStart={handleNewSession}
            />
          }
        />

        {/* Conversation history — a slide-over, so the panel stays an overlay
            available on every page rather than becoming a route. */}
        {isOpen && showHistory && (
          <ConversationList
            liveIds={liveIds}
            activeId={activeSlot?.info.conversation_id || chat.activeSlotId}
            onNew={() => handleNewSession(effectiveAgent, effectiveSlug || undefined)}
            onOpen={(meta) => {
              chat.resumeConversation(meta.id, {
                agent_key: meta.agent_key,
                server_name: meta.server_name || undefined,
                agent_slug: meta.agent_slug,
              });
              setShowHistory(false);
            }}
            onClose={() => setShowHistory(false)}
          />
        )}
      </div>
    </>
  );
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

// ── New Session Menu ──

function NewSessionMenu({
  agents,
  customProviders = [],
  agentBindings = [],
  selectedAgent,
  selectedAgentSlug,
  onSelectBrain,
  onStart,
  onClose,
}: {
  agents: ChatAgentOption[];
  customProviders?: CustomProvider[];
  agentBindings?: AgentBindingOption[];
  selectedAgent: string;
  selectedAgentSlug: string;
  onSelectBrain: (selection: BrainSelection) => void;
  onStart: (agent: string) => void;
  onClose: () => void;
}) {
  return (
    <>
      <div className="fixed inset-0 z-50" onClick={onClose} />
      <div className="absolute right-0 top-full z-50 mt-1 w-56 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] py-1 shadow-xl">
        {/* Who answers */}
        <div className="px-3 pt-2 pb-1">
          <label className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
            Answered by
          </label>
          <div className="mt-1">
            <BrainPicker
              agents={agents}
              customProviders={customProviders}
              agentBindings={agentBindings}
              selectedAgentKey={selectedAgent}
              selectedAgentSlug={selectedAgentSlug}
              onSelect={onSelectBrain}
              variant="block"
            />
          </div>
        </div>

        {/* Start button */}
        <div className="mt-1 border-t border-[var(--color-border)] px-3 pt-2 pb-2">
          <button
            onClick={() => onStart(selectedAgent)}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-[var(--color-primary)] px-3 py-1.5 text-xs font-medium text-white hover:bg-[var(--color-primary)]/80"
          >
            <Zap className="h-3.5 w-3.5" />
            Start Session
          </button>
        </div>
      </div>
    </>
  );
}

// ── Empty State ──

function EmptyState({
  agents,
  customProviders = [],
  agentBindings = [],
  defaultAgent,
  onStart,
}: {
  agents: ChatAgentOption[];
  customProviders?: CustomProvider[];
  agentBindings?: AgentBindingOption[];
  defaultAgent: string;
  onStart: (agent: string, agentSlug?: string) => void;
}) {
  const [selectedAgent, setSelectedAgent] = useState(defaultAgent);
  const [selectedSlug, setSelectedSlug] = useState("");

  return (
    <div className="flex flex-1 flex-col items-center justify-center text-center">
      <MessageSquare className="mb-3 h-10 w-10 text-[var(--color-text-muted)] opacity-30" />
      <p className="text-sm text-[var(--color-text-muted)]">
        Start a new session to chat with the AI assistant.
      </p>

      {/* Who answers */}
      {(agents.length > 1 || customProviders.length > 0 || agentBindings.length > 0) && (
        <div className="mt-4 mb-2">
          <BrainPicker
            agents={agents}
            customProviders={customProviders}
            agentBindings={agentBindings}
            selectedAgentKey={selectedAgent}
            selectedAgentSlug={selectedSlug}
            onSelect={(sel) => {
              if (sel.agentSlug !== undefined) setSelectedSlug(sel.agentSlug);
              if (sel.agentKey !== undefined) setSelectedAgent(sel.agentKey);
            }}
            variant="inline"
          />
        </div>
      )}

      <div className="mt-2 flex gap-2">
        <button
          onClick={() => onStart(selectedAgent, selectedSlug || undefined)}
          className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2 text-sm text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
        >
          <Zap className="h-3.5 w-3.5 text-[var(--color-primary)]" />
          Start Session
        </button>
      </div>
    </div>
  );
}

// ── Session Tab ──

function SessionTab({
  slot,
  agents,
  isActive,
  isStreaming,
  needsApproval,
  onClick,
  onClose,
}: {
  slot: ChatSlot;
  agents: ChatAgentOption[];
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
      className={`group relative flex items-center gap-1.5 whitespace-nowrap border-r border-[var(--color-border)] px-3 py-1.5 text-xs transition-colors ${
        isActive
          ? "bg-[var(--color-bg)] text-[var(--color-text)]"
          : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
      }`}
    >
      {isActive && (
        <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-[var(--color-primary)]" />
      )}
      <TabIcon className="h-3 w-3 shrink-0" />
      <span className="max-w-[140px] truncate">
        {agentShort}
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
