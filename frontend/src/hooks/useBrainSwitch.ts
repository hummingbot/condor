import { useState } from "react";

import type { BrainSelection } from "@/components/chat/BrainPicker";
import { useChat } from "@/hooks/useChat";

/**
 * Changing who answers the conversation on screen.
 *
 * The switch is a respawn behind the scenes, so it can fail for reasons the
 * user needs to read — a model that is not configured, a session that died
 * mid-flight. Every chat surface handles that the same way: clear the last
 * message, try, and keep whatever came back so the transcript can show it in
 * `ChatThread`'s dismissible banner.
 *
 * It lives here rather than inline in each surface because the error is raised
 * by the identity strip and rendered by the thread — two siblings — so neither
 * can own it alone without the other copying the state.
 */
export function useBrainSwitch() {
  const chat = useChat();
  const [switchError, setSwitchError] = useState<string | null>(null);

  const switchBrain = (selection: BrainSelection) => {
    if (!chat.activeSlotId) return;
    setSwitchError(null);
    chat
      .switchBrain(chat.activeSlotId, selection)
      .catch((e: Error) => setSwitchError(e.message || "Could not switch"));
  };

  return {
    switchBrain,
    switchError,
    dismissSwitchError: () => setSwitchError(null),
  };
}
