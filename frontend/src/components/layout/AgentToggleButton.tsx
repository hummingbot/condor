import { MessageSquare } from "lucide-react";

interface AgentToggleButtonProps {
  /** Whether the chat panel is currently open (only AppShell knows this). */
  active?: boolean;
  /** Override the default trigger. Defaults to toggling the chat via the ⌘K shortcut. */
  onClick?: () => void;
  className?: string;
}

/** Dispatches the synthetic Cmd+K keydown that ChatPanel listens for to toggle the chat. */
function dispatchChatShortcut() {
  window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true, bubbles: true }));
}

/** Amber "Agent" pill that toggles the chat panel. */
export function AgentToggleButton({ active = false, onClick, className }: AgentToggleButtonProps) {
  return (
    <button
      onClick={onClick ?? dispatchChatShortcut}
      className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-all ${
        active
          ? "bg-amber-500 text-black shadow-sm shadow-amber-500/25"
          : "bg-amber-500/15 text-amber-500 hover:bg-amber-500/25 border border-amber-500/30"
      }${className ? ` ${className}` : ""}`}
      title="Agent (⌘K)"
    >
      <MessageSquare className="h-3.5 w-3.5" />
      <span>Agent</span>
    </button>
  );
}
