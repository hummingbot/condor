import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Brain,
  ChevronDown,
  Loader2,
  MessageSquare,
  Minus,
  Plus,
  X,
  Zap,
} from "lucide-react";
import { useChatSocket, type ChatSlot } from "@/hooks/useChatSocket";
import { ChatMessageView } from "./ChatMessage";
import { ChatInput } from "./ChatInput";
import { api, type ChatAgentOption, type ChatModeOption } from "@/lib/api";

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
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const [isDragging, setIsDragging] = useState(false);
  const [showNewMenu, setShowNewMenu] = useState(false);
  const [pendingSession, setPendingSession] = useState(false);

  // Chat options from backend
  const [agents, setAgents] = useState<ChatAgentOption[]>([]);
  const [modes, setModes] = useState<ChatModeOption[]>([]);
  const [defaultAgent, setDefaultAgent] = useState("claude-code");
  const [defaultMode, setDefaultMode] = useState("condor");
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [selectedMode, setSelectedMode] = useState<string | null>(null);
  const optionsFetched = useRef(false);

  // Fetch chat options on first open
  useEffect(() => {
    if (isOpen && !optionsFetched.current) {
      optionsFetched.current = true;
      api.getChatOptions().then((opts) => {
        setAgents(opts.agents);
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
  const startDrag = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      setIsDragging(true);
      const startX = e.clientX;
      const startWidth = width;

      const onMove = (ev: MouseEvent) => {
        const delta = startX - ev.clientX;
        setWidth(Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, startWidth + delta)));
      };
      const onUp = () => {
        setIsDragging(false);
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [width],
  );

  // Clear pending state when active slot becomes available
  useEffect(() => {
    if (chat.activeSlot && pendingSession) {
      setPendingSession(false);
    }
  }, [chat.activeSlot, pendingSession]);

  const handleNewSession = (agentKey: string, mode: string) => {
    setPendingSession(true);
    onToggle(true);
    chat.startSession(agentKey, mode);
    setShowNewMenu(false);
    setSelectedAgent(null);
    setSelectedMode(null);
  };

  const activeSlot = chat.activeSlot;
  const isActiveStreaming = chat.streamingSlotId === chat.activeSlotId;

  // Resolve effective selections for the new-session menu
  const effectiveAgent = selectedAgent || defaultAgent;
  const effectiveMode = selectedMode || defaultMode;

  // Filter out sentinel keys (ending with :) that are pickers, not direct agents
  const directAgents = agents.filter((a) => !a.key.endsWith(":"));

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
          <div className="flex items-center gap-2">
            <MessageSquare className="h-4 w-4 text-[var(--color-primary)]" />
            <span className="text-sm font-semibold whitespace-nowrap">Agent</span>
            <kbd className="rounded bg-[var(--color-surface-hover)] px-1.5 py-0.5 text-[10px] font-medium tracking-wide text-[var(--color-text-muted)] border border-[var(--color-border)]">
              ⌘K
            </kbd>
            {chat.isConnected && (
              <span className="h-1.5 w-1.5 rounded-full bg-green-500" />
            )}
          </div>
          <div className="flex items-center gap-1">
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
                  agents={directAgents}
                  modes={modes}
                  selectedAgent={effectiveAgent}
                  selectedMode={effectiveMode}
                  onSelectAgent={setSelectedAgent}
                  onSelectMode={setSelectedMode}
                  onStart={(agent, mode) => handleNewSession(agent, mode)}
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
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
              <div className="flex-1 text-sm">
                <p className="font-medium text-amber-200">Confirm action</p>
                <p className="mt-0.5 text-[var(--color-text-muted)]">
                  {chat.permissionRequest.summary}
                </p>
                <div className="mt-2 flex gap-2">
                  <button
                    onClick={() =>
                      chat.resolvePermission(chat.permissionRequest!.request_id, true)
                    }
                    className="rounded bg-green-600 px-3 py-1 text-xs font-medium text-white hover:bg-green-500"
                  >
                    Approve
                  </button>
                  <button
                    onClick={() =>
                      chat.resolvePermission(chat.permissionRequest!.request_id, false)
                    }
                    className="rounded bg-red-600 px-3 py-1 text-xs font-medium text-white hover:bg-red-500"
                  >
                    Reject
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Messages area */}
        <div className="flex-1 overflow-y-auto px-4 py-4">
          {pendingSession && !activeSlot ? (
            <div className="flex h-full flex-col items-center justify-center text-center">
              <div className="relative mb-4">
                <div className="h-12 w-12 rounded-full border-2 border-[var(--color-primary)]/20" />
                <div className="absolute inset-0 h-12 w-12 animate-spin rounded-full border-2 border-transparent border-t-[var(--color-primary)]" style={{ animationDuration: "1s" }} />
                <Zap className="absolute inset-0 m-auto h-5 w-5 text-[var(--color-primary)]" />
              </div>
              <p className="text-sm font-medium text-[var(--color-text)]">
                Starting session...
              </p>
              <p className="mt-1 text-xs text-[var(--color-text-muted)]">
                Connecting to Condor agent
              </p>
            </div>
          ) : !activeSlot ? (
            <EmptyState
              agents={directAgents}
              modes={modes}
              defaultAgent={defaultAgent}
              defaultMode={defaultMode}
              onStart={handleNewSession}
            />
          ) : activeSlot.messages.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center text-center">
              {activeSlot.info.mode === "agent_builder" ? (
                <Brain className="mb-3 h-10 w-10 text-[var(--color-text-muted)] opacity-30" />
              ) : (
                <MessageSquare className="mb-3 h-10 w-10 text-[var(--color-text-muted)] opacity-30" />
              )}
              <p className="text-sm font-medium text-[var(--color-text)]">
                {activeSlot.info.mode === "agent_builder"
                  ? "Agent Builder"
                  : "Condor Assistant"}
              </p>
              <p className="mt-1 text-xs text-[var(--color-text-muted)]">
                {activeSlot.info.mode === "agent_builder"
                  ? "Create and manage autonomous trading strategies."
                  : "Ask about your portfolio, prices, trades, or bot status."}
              </p>
              <p className="mt-2 text-[10px] text-[var(--color-text-muted)] opacity-60">
                {resolveAgentLabel(activeSlot.info.agent_key, agents)}
              </p>
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
  modes,
  selectedAgent,
  selectedMode,
  onSelectAgent,
  onSelectMode,
  onStart,
  onClose,
}: {
  agents: ChatAgentOption[];
  modes: ChatModeOption[];
  selectedAgent: string;
  selectedMode: string;
  onSelectAgent: (key: string) => void;
  onSelectMode: (key: string) => void;
  onStart: (agent: string, mode: string) => void;
  onClose: () => void;
}) {
  const [showAgentPicker, setShowAgentPicker] = useState(false);

  const agentLabel = agents.find((a) => a.key === selectedAgent)?.label || selectedAgent;
  const ModeIcon = MODE_ICONS[selectedMode] || Zap;

  return (
    <>
      <div className="fixed inset-0 z-50" onClick={onClose} />
      <div className="absolute right-0 top-full z-50 mt-1 w-56 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] py-1 shadow-xl">
        {/* Model selector */}
        <div className="px-3 pt-2 pb-1">
          <label className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
            Model
          </label>
          <div className="relative mt-1">
            <button
              onClick={() => setShowAgentPicker((v) => !v)}
              className="flex w-full items-center justify-between rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-1.5 text-left text-xs text-[var(--color-text)] hover:border-[var(--color-primary)]/40"
            >
              <span className="truncate">{agentLabel}</span>
              <ChevronDown className="ml-1 h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
            </button>
            {showAgentPicker && (
              <div className="absolute left-0 top-full z-10 mt-1 max-h-48 w-full overflow-y-auto rounded border border-[var(--color-border)] bg-[var(--color-surface)] py-0.5 shadow-lg">
                {agents.map((a) => (
                  <button
                    key={a.key}
                    onClick={() => {
                      onSelectAgent(a.key);
                      setShowAgentPicker(false);
                    }}
                    className={`flex w-full items-center px-2.5 py-1.5 text-left text-xs hover:bg-[var(--color-surface-hover)] ${
                      a.key === selectedAgent
                        ? "text-[var(--color-primary)] font-medium"
                        : "text-[var(--color-text)]"
                    }`}
                  >
                    {a.label}
                  </button>
                ))}
              </div>
            )}
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
            className="flex w-full items-center justify-center gap-2 rounded-md bg-[var(--color-primary)] px-3 py-1.5 text-xs font-medium text-black hover:bg-[var(--color-primary)]/80"
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
  modes,
  defaultAgent,
  defaultMode,
  onStart,
}: {
  agents: ChatAgentOption[];
  modes: ChatModeOption[];
  defaultAgent: string;
  defaultMode: string;
  onStart: (agent: string, mode: string) => void;
}) {
  const [selectedAgent, setSelectedAgent] = useState(defaultAgent);
  const [showAgentPicker, setShowAgentPicker] = useState(false);

  const agentLabel = agents.find((a) => a.key === selectedAgent)?.label || selectedAgent;

  return (
    <div className="flex h-full flex-col items-center justify-center text-center">
      <MessageSquare className="mb-3 h-10 w-10 text-[var(--color-text-muted)] opacity-30" />
      <p className="text-sm text-[var(--color-text-muted)]">
        Start a new session to chat with the AI assistant.
      </p>

      {/* Agent picker */}
      {agents.length > 1 && (
        <div className="relative mt-4 mb-2">
          <button
            onClick={() => setShowAgentPicker((v) => !v)}
            className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs text-[var(--color-text)] hover:border-[var(--color-primary)]/40"
          >
            <span>{agentLabel}</span>
            <ChevronDown className="h-3 w-3 text-[var(--color-text-muted)]" />
          </button>
          {showAgentPicker && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setShowAgentPicker(false)} />
              <div className="absolute left-1/2 top-full z-50 mt-1 max-h-48 w-48 -translate-x-1/2 overflow-y-auto rounded border border-[var(--color-border)] bg-[var(--color-surface)] py-0.5 shadow-lg">
                {agents.map((a) => (
                  <button
                    key={a.key}
                    onClick={() => {
                      setSelectedAgent(a.key);
                      setShowAgentPicker(false);
                    }}
                    className={`flex w-full items-center px-3 py-1.5 text-left text-xs hover:bg-[var(--color-surface-hover)] ${
                      a.key === selectedAgent
                        ? "text-[var(--color-primary)] font-medium"
                        : "text-[var(--color-text)]"
                    }`}
                  >
                    {a.label}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {/* Mode buttons */}
      <div className="mt-2 flex gap-2">
        {modes.map(({ key, label }) => {
          const Icon = MODE_ICONS[key] || Zap;
          return (
            <button
              key={key}
              onClick={() => onStart(selectedAgent, key)}
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
  isActive,
  isStreaming,
  onClick,
  onClose,
}: {
  slot: ChatSlot;
  agents: ChatAgentOption[];
  isActive: boolean;
  isStreaming: boolean;
  onClick: () => void;
  onClose: () => void;
}) {
  const modeLabel =
    slot.info.mode === "agent_builder" ? "Builder" : "Condor";
  const agentShort = shortAgentLabel(slot.info.agent_key, agents);
  const ModeIcon = MODE_ICONS[slot.info.mode] || Zap;

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
      <span className="max-w-[100px] truncate">
        {modeLabel}
        <span className="text-[var(--color-text-muted)]"> · {agentShort}</span>
      </span>
      {isStreaming && (
        <Loader2 className="h-3 w-3 shrink-0 animate-spin text-[var(--color-primary)]" />
      )}
      <span
        role="button"
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
        className="ml-0.5 rounded p-0.5 opacity-0 transition-opacity hover:bg-[var(--color-surface-hover)] group-hover:opacity-100"
      >
        <X className="h-2.5 w-2.5" />
      </span>
    </button>
  );
}
