import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Bot,
  Brain,
  History,
  Loader2,
  MessageSquare,
  Minus,
  Plus,
  Server,
  X,
  Zap,
} from "lucide-react";
import { useChatSocket, type ChatSlot } from "@/hooks/useChatSocket";
import { ChatMessageView } from "./ChatMessage";
import { ChatInput } from "./ChatInput";
import { BrainPicker, type BrainSelection } from "./BrainPicker";
import { ConversationList } from "./ConversationList";
import {
  api,
  type AgentBindingOption,
  type ChatAgentOption,
  type ChatModeOption,
  type CustomProvider,
} from "@/lib/api";
import { onChatRequest } from "@/lib/chatIntent";
import { useServer } from "@/hooks/useServer";
import { useResizeDrag } from "@/hooks/useResizeDrag";

const MIN_WIDTH = 360;
const MAX_WIDTH = 1200;
const DEFAULT_WIDTH = 480;

const MODE_ICONS: Record<string, typeof Zap> = {
  condor: Zap,
  agent_builder: Brain,
};

interface ChatPanelProps {
  isOpen: boolean;
  onToggle: (open: boolean | ((prev: boolean) => boolean)) => void;
}

export function ChatPanel({ isOpen, onToggle }: ChatPanelProps) {
  const chat = useChatSocket();
  const { server } = useServer();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const [showNewMenu, setShowNewMenu] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [switchError, setSwitchError] = useState<string | null>(null);

  // Chat options from backend
  const [agents, setAgents] = useState<ChatAgentOption[]>([]);
  const [customProviders, setCustomProviders] = useState<CustomProvider[]>([]);
  const [agentBindings, setAgentBindings] = useState<AgentBindingOption[]>([]);
  const [modes, setModes] = useState<ChatModeOption[]>([]);
  const [defaultAgent, setDefaultAgent] = useState("claude-code");
  const [defaultMode, setDefaultMode] = useState("condor");
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [selectedSlug, setSelectedSlug] = useState("");
  const [selectedMode, setSelectedMode] = useState<string | null>(null);
  const optionsFetched = useRef(false);

  // Fetch chat options on first open. /sessions/options is /chat/options plus
  // the domain Agents a session can be bound to, which is what the picker's
  // "Agents" section is.
  useEffect(() => {
    if (isOpen && !optionsFetched.current) {
      optionsFetched.current = true;
      api.getSessionOptions().then((opts) => {
        setAgents(opts.agents);
        setCustomProviders(opts.custom_providers ?? []);
        setAgentBindings(opts.agent_bindings ?? []);
        setModes(opts.modes);
        setDefaultAgent(opts.default_agent);
        setDefaultMode(opts.default_mode);
      }).catch(() => {
        // Fallback defaults
        setAgents([{ key: "claude-code", label: "Claude Code" }]);
        setModes([
          { key: "condor", label: "Condor", description: "" },
          { key: "agent_builder", label: "Agent Builder", description: "" },
        ]);
      });
    }
  }, [isOpen]);

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

  // Auto-scroll on new messages in the active slot
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat.activeSlot?.messages]);

  // Resize drag handling
  const { onMouseDown: startDrag, isDragging } = useResizeDrag({
    axis: "x",
    value: width,
    onChange: setWidth,
    min: MIN_WIDTH,
    max: MAX_WIDTH,
    direction: "inverted",
  });

  const handleNewSession = (agentKey: string, mode: string, agentSlug?: string) => {
    onToggle(true);
    chat.startSession(agentKey, mode, server || undefined, agentSlug);
    setShowNewMenu(false);
    setShowHistory(false);
    setSelectedAgent(null);
    setSelectedSlug("");
    setSelectedMode(null);
  };

  // "Chat" on an agent's detail page lands here: one start_session already
  // bound to the agent, so the click costs one spawn rather than a start
  // followed by a switch.
  useEffect(
    () =>
      onChatRequest(({ agentSlug }) => {
        onToggle(true);
        chat.startSession(defaultAgent, defaultMode, server || undefined, agentSlug);
        setShowNewMenu(false);
        setShowHistory(false);
      }),
    [chat, defaultAgent, defaultMode, onToggle, server],
  );

  const activeSlot = chat.activeSlot;
  const isActiveStreaming = chat.streamingSlotId === chat.activeSlotId;

  // Resolve effective selections for the new-session menu
  const effectiveAgent = selectedAgent || defaultAgent;
  const effectiveMode = selectedMode || defaultMode;

  const handleSwitch = (selection: BrainSelection) => {
    if (!chat.activeSlotId) return;
    setSwitchError(null);
    chat
      .switchBrain(chat.activeSlotId, selection)
      .catch((e: Error) => setSwitchError(e.message || "Could not switch"));
  };

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
            {chat.isConnected && (
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-green-500" />
            )}
            {/* Who is answering. One picker, because the user is asking one
                question — a bound Agent brings its own model. */}
            {activeSlot ? (
              <BrainPicker
                agents={agents}
                customProviders={customProviders}
                agentBindings={agentBindings}
                selectedAgentKey={activeSlot.info.agent_key}
                selectedAgentSlug={activeSlot.info.agent_slug || ""}
                onSelect={handleSwitch}
                variant="inline"
                disabled={activeSlot.pending || isActiveStreaming}
              />
            ) : (
              <>
                <span className="text-sm font-semibold whitespace-nowrap">Agent</span>
                <kbd className="rounded bg-[var(--color-surface-hover)] px-1.5 py-0.5 text-[10px] font-medium tracking-wide text-[var(--color-text-muted)] border border-[var(--color-border)]">
                  ⌘K
                </kbd>
              </>
            )}
          </div>
          {/* Active session server indicator */}
          {activeSlot?.info.server_name && (
            <div className="flex items-center gap-1 rounded bg-[var(--color-surface-hover)] px-2 py-0.5 text-[10px] text-[var(--color-text-muted)] border border-[var(--color-border)]">
              <Server className="h-2.5 w-2.5" />
              <span className="truncate max-w-[80px]">{activeSlot.info.server_name}</span>
            </div>
          )}
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
                  modes={modes}
                  selectedAgent={effectiveAgent}
                  selectedAgentSlug={selectedSlug}
                  selectedMode={effectiveMode}
                  onSelectBrain={(sel) => {
                    if (sel.agentSlug !== undefined) setSelectedSlug(sel.agentSlug);
                    if (sel.agentKey !== undefined) setSelectedAgent(sel.agentKey);
                  }}
                  onSelectMode={setSelectedMode}
                  onStart={(agent, mode) =>
                    handleNewSession(agent, mode, selectedSlug || undefined)
                  }
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
                modes={modes}
                isActive={slot.info.slot_id === chat.activeSlotId}
                isStreaming={slot.info.slot_id === chat.streamingSlotId}
                onClick={() => chat.setActiveSlotId(slot.info.slot_id)}
                onClose={() => chat.destroySession(slot.info.slot_id)}
              />
            ))}
          </div>
        )}

        {/* Permission request banner */}
        {chat.permissionRequest && (
          <div className="border-b border-amber-500/30 bg-amber-500/10 px-4 py-3">
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-yellow)]" />
              <div className="flex-1 text-sm">
                <p className="font-medium text-[var(--color-yellow)]">Confirm action</p>
                <p className="mt-0.5 text-[var(--color-text-muted)]">
                  {chat.permissionRequest.summary}
                </p>
                <div className="mt-2 flex gap-2">
                  <button
                    onClick={() =>
                      chat.resolvePermission(chat.permissionRequest!.request_id, true)
                    }
                    className="rounded bg-[var(--color-green)] px-3 py-1 text-xs font-medium text-white hover:opacity-90"
                  >
                    Approve
                  </button>
                  <button
                    onClick={() =>
                      chat.resolvePermission(chat.permissionRequest!.request_id, false)
                    }
                    className="rounded bg-[var(--color-red)] px-3 py-1 text-xs font-medium text-white hover:opacity-90"
                  >
                    Reject
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Switch failure — the header still shows whoever is actually answering */}
        {switchError && (
          <div className="flex items-start gap-2 border-b border-red-500/30 bg-red-500/10 px-4 py-2 text-xs text-red-400">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span className="flex-1">{switchError}</span>
            <button onClick={() => setSwitchError(null)} title="Dismiss">
              <X className="h-3 w-3" />
            </button>
          </div>
        )}

        {/* Messages area */}
        <div className="flex-1 overflow-y-auto px-4 py-4">
          {!activeSlot ? (
            <EmptyState
              agents={agents}
              customProviders={customProviders}
              agentBindings={agentBindings}
              modes={modes}
              defaultAgent={defaultAgent}
              defaultMode={defaultMode}
              onStart={handleNewSession}
            />
          ) : activeSlot.messages.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center text-center">
              {(() => {
                const ModeIcon = MODE_ICONS[activeSlot.info.mode] || MessageSquare;
                return <ModeIcon className="mb-3 h-10 w-10 text-[var(--color-text-muted)] opacity-30" />;
              })()}
              <p className="text-sm font-medium text-[var(--color-text)]">
                {modes.find((m) => m.key === activeSlot.info.mode)?.label || "Assistant"}
              </p>
              <p className="mt-1 text-xs text-[var(--color-text-muted)]">
                {modes.find((m) => m.key === activeSlot.info.mode)?.description ||
                  "Ask about your portfolio, prices, trades, or bot status."}
              </p>
              <p className="mt-2 flex items-center gap-1 text-[10px] text-[var(--color-text-muted)] opacity-60">
                {activeSlot.info.agent_slug && <Bot className="h-2.5 w-2.5" />}
                {activeSlot.info.label && activeSlot.info.agent_slug
                  ? activeSlot.info.label
                  : resolveAgentLabel(activeSlot.info.agent_key, agents)}
              </p>
              {activeSlot.pending && (
                <p className="mt-3 flex items-center gap-1.5 text-[10px] text-[var(--color-text-muted)]">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Warming up — type away, it will be sent
                </p>
              )}
            </div>
          ) : (
            activeSlot.messages.map((msg) => (
              <ChatMessageView key={msg.id} message={msg} />
            ))
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        {activeSlot && (
          <ChatInput
            onSend={(text) => chat.sendMessage(activeSlot.info.slot_id, text)}
            disabled={isActiveStreaming}
            isStreaming={isActiveStreaming}
            onAbort={() => chat.activeSlotId && chat.abortPrompt(chat.activeSlotId)}
          />
        )}

        {/* Conversation history — a slide-over, so the panel stays an overlay
            available on every page rather than becoming a route. */}
        {isOpen && showHistory && (
          <ConversationList
            liveIds={liveIds}
            activeId={activeSlot?.info.conversation_id || chat.activeSlotId}
            onNew={() => handleNewSession(effectiveAgent, effectiveMode)}
            onOpen={(meta) => {
              chat.resumeConversation(meta.id, {
                agent_key: meta.agent_key,
                mode: meta.mode,
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

/** Resolve a short label for an agent key */
function resolveAgentLabel(agentKey: string, agents: ChatAgentOption[]): string {
  const match = agents.find((a) => a.key === agentKey);
  if (match) return match.label;
  // Handle dynamic keys like "openrouter:anthropic/claude-3.5-sonnet"
  if (agentKey.includes(":")) {
    const [provider, model] = agentKey.split(":", 2);
    return model || provider;
  }
  return agentKey;
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
  modes,
  selectedAgent,
  selectedAgentSlug,
  selectedMode,
  onSelectBrain,
  onSelectMode,
  onStart,
  onClose,
}: {
  agents: ChatAgentOption[];
  customProviders?: CustomProvider[];
  agentBindings?: AgentBindingOption[];
  modes: ChatModeOption[];
  selectedAgent: string;
  selectedAgentSlug: string;
  selectedMode: string;
  onSelectBrain: (selection: BrainSelection) => void;
  onSelectMode: (key: string) => void;
  onStart: (agent: string, mode: string) => void;
  onClose: () => void;
}) {
  const ModeIcon = MODE_ICONS[selectedMode] || Zap;

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

        {/* Mode buttons */}
        <div className="px-3 pt-2 pb-1">
          <label className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
            Mode
          </label>
        </div>
        {modes.map(({ key, label }) => {
          const Icon = MODE_ICONS[key] || Zap;
          return (
            <button
              key={key}
              onClick={() => onSelectMode(key)}
              className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-[var(--color-surface-hover)] ${
                key === selectedMode
                  ? "text-[var(--color-primary)]"
                  : "text-[var(--color-text)]"
              }`}
            >
              <Icon className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
              {label}
            </button>
          );
        })}

        {/* Start button */}
        <div className="mt-1 border-t border-[var(--color-border)] px-3 pt-2 pb-2">
          <button
            onClick={() => onStart(selectedAgent, selectedMode)}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-[var(--color-primary)] px-3 py-1.5 text-xs font-medium text-white hover:bg-[var(--color-primary)]/80"
          >
            <ModeIcon className="h-3.5 w-3.5" />
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
  modes,
  defaultAgent,
  onStart,
}: {
  agents: ChatAgentOption[];
  customProviders?: CustomProvider[];
  agentBindings?: AgentBindingOption[];
  modes: ChatModeOption[];
  defaultAgent: string;
  defaultMode?: string;
  onStart: (agent: string, mode: string, agentSlug?: string) => void;
}) {
  const [selectedAgent, setSelectedAgent] = useState(defaultAgent);
  const [selectedSlug, setSelectedSlug] = useState("");

  return (
    <div className="flex h-full flex-col items-center justify-center text-center">
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

      {/* Mode buttons */}
      <div className="mt-2 flex gap-2">
        {modes.map(({ key, label }) => {
          const Icon = MODE_ICONS[key] || Zap;
          return (
            <button
              key={key}
              onClick={() => onStart(selectedAgent, key, selectedSlug || undefined)}
              className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2 text-sm text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
            >
              <Icon className="h-3.5 w-3.5 text-[var(--color-primary)]" />
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ── Session Tab ──

function SessionTab({
  slot,
  agents,
  modes,
  isActive,
  isStreaming,
  onClick,
  onClose,
}: {
  slot: ChatSlot;
  agents: ChatAgentOption[];
  modes: ChatModeOption[];
  isActive: boolean;
  isStreaming: boolean;
  onClick: () => void;
  onClose: () => void;
}) {
  const modeLabel =
    modes.find((m) => m.key === slot.info.mode)?.label || slot.info.mode;
  // A bound Agent names the tab; only an unbound one falls back to the model,
  // which is the same rule the header's picker follows.
  const agentShort = slot.info.agent_slug
    ? slot.info.label || slot.info.agent_slug
    : shortAgentLabel(slot.info.agent_key, agents);
  const ModeIcon = slot.info.agent_slug ? Bot : MODE_ICONS[slot.info.mode] || Zap;

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
      <ModeIcon className="h-3 w-3 shrink-0" />
      <span className="max-w-[140px] truncate">
        {slot.info.agent_slug ? agentShort : modeLabel}
        {!slot.info.agent_slug && (
          <span className="text-[var(--color-text-muted)]"> · {agentShort}</span>
        )}
        {slot.info.server_name && (
          <span className="text-[var(--color-text-muted)]"> · {slot.info.server_name}</span>
        )}
      </span>
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
