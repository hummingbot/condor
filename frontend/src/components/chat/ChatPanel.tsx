import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Brain,
  GripVertical,
  Loader2,
  MessageSquare,
  Plus,
  X,
  Zap,
} from "lucide-react";
import { useChatSocket, type ChatSlot } from "@/hooks/useChatSocket";
import { ChatMessageView } from "./ChatMessage";
import { ChatInput } from "./ChatInput";

const MIN_WIDTH = 360;
const MAX_WIDTH = 1200;
const DEFAULT_WIDTH = 480;

const MODE_OPTIONS = [
  { key: "condor", label: "Condor", icon: Zap },
  { key: "agent_builder", label: "Agent Builder", icon: Brain },
] as const;

export function ChatPanel() {
  const chat = useChatSocket();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const [isOpen, setIsOpen] = useState(false);
  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const [isDragging, setIsDragging] = useState(false);
  const [showNewMenu, setShowNewMenu] = useState(false);
  const [pendingSession, setPendingSession] = useState(false);

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

  const handleNewSession = (mode: string) => {
    setPendingSession(true);
    setIsOpen(true);
    chat.startSession("claude-code", mode);
    setShowNewMenu(false);
  };

  const activeSlot = chat.activeSlot;
  const isActiveStreaming = chat.streamingSlotId === chat.activeSlotId;

  return (
    <>
      {/* Floating chat button — always visible when panel is closed */}
      {!isOpen && (
        <button
          onClick={() => setIsOpen(true)}
          className="fixed bottom-6 right-6 z-50 flex h-14 w-14 items-center justify-center rounded-full bg-[var(--color-primary)] text-white shadow-lg shadow-[var(--color-primary)]/25 transition-all duration-200 hover:scale-105 hover:shadow-xl hover:shadow-[var(--color-primary)]/30 active:scale-95"
          title="Chat with Condor"
        >
          <MessageSquare className="h-6 w-6" />
          {/* Subtle ping animation */}
          <span className="absolute inset-0 animate-ping rounded-full bg-[var(--color-primary)] opacity-20" style={{ animationDuration: "3s" }} />
        </button>
      )}

      {/* Panel */}
      <div
        ref={panelRef}
        style={{ width: isOpen ? width : 0 }}
        className={`fixed right-0 top-0 z-50 flex h-full flex-col border-l border-[var(--color-border)] bg-[var(--color-bg)] shadow-xl transition-[width] duration-200 ease-out ${
          isOpen ? "" : "overflow-hidden border-l-0"
        }`}
      >
        {/* Resize handle */}
        {isOpen && (
          <div
            onMouseDown={startDrag}
            className={`absolute left-0 top-0 z-10 flex h-full w-1.5 cursor-col-resize items-center justify-center transition-colors hover:bg-[var(--color-primary)]/20 ${
              isDragging ? "bg-[var(--color-primary)]/30" : ""
            }`}
          >
            <GripVertical className="h-4 w-4 text-[var(--color-text-muted)] opacity-0 transition-opacity group-hover:opacity-100" />
          </div>
        )}

        {/* Header */}
        <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2">
          <div className="flex items-center gap-2">
            <MessageSquare className="h-4 w-4 text-[var(--color-primary)]" />
            <span className="text-sm font-semibold whitespace-nowrap">AI Chat</span>
            {chat.isConnected && (
              <span className="h-1.5 w-1.5 rounded-full bg-green-500" />
            )}
          </div>
          <div className="flex items-center gap-1">
            {/* New session button with mode selector */}
            <div className="relative">
              <button
                onClick={() => setShowNewMenu((v) => !v)}
                className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                title="New session"
              >
                <Plus className="h-4 w-4" />
              </button>
              {showNewMenu && (
                <>
                  <div
                    className="fixed inset-0 z-50"
                    onClick={() => setShowNewMenu(false)}
                  />
                  <div className="absolute right-0 top-full z-50 mt-1 w-44 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] py-1 shadow-xl">
                    {MODE_OPTIONS.map(({ key, label, icon: Icon }) => (
                      <button
                        key={key}
                        onClick={() => handleNewSession(key)}
                        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
                      >
                        <Icon className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
                        {label}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
            <button
              onClick={() => setIsOpen(false)}
              className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title="Close"
            >
              <X className="h-4 w-4" />
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
            <div className="flex h-full flex-col items-center justify-center text-center">
              <MessageSquare className="mb-3 h-10 w-10 text-[var(--color-text-muted)] opacity-30" />
              <p className="text-sm text-[var(--color-text-muted)]">
                Start a new session to chat with the AI assistant.
              </p>
              <div className="mt-4 flex gap-2">
                {MODE_OPTIONS.map(({ key, label, icon: Icon }) => (
                  <button
                    key={key}
                    onClick={() => handleNewSession(key)}
                    className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2 text-sm text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
                  >
                    <Icon className="h-3.5 w-3.5 text-[var(--color-primary)]" />
                    {label}
                  </button>
                ))}
              </div>
            </div>
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

function SessionTab({
  slot,
  isActive,
  isStreaming,
  onClick,
  onClose,
}: {
  slot: ChatSlot;
  isActive: boolean;
  isStreaming: boolean;
  onClick: () => void;
  onClose: () => void;
}) {
  const modeLabel =
    slot.info.mode === "agent_builder" ? "Builder" : "Condor";
  const ModeIcon =
    slot.info.mode === "agent_builder" ? Brain : Zap;

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
      <span className="max-w-[80px] truncate">{modeLabel}</span>
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
