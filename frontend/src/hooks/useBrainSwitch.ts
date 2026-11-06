import { useState } from "react";

import type { BrainSelection } from "@/components/chat/BrainPicker";
import { useChat } from "@/hooks/useChat";

/**
 * Changing who answers the conversation on screen, and where that answer runs.
 *
 * Both switches are a respawn behind the scenes, so both can fail for reasons
 * the user needs to read — a model that is not configured, a server the session
 * is not allowed on, a session that died mid-flight. Every chat surface handles
 * that the same way: clear the last message, try, and keep whatever came back
 * so the transcript can show it in `ChatThread`'s dismissible banner.
 *
 * It lives here rather than inline in each surface because the error is raised
 * by the identity strip and rendered by the thread — two siblings — so neither
 * can own it alone without the other copying the state. The server switch
 * shares the state rather than growing a banner of its own: only one of the two
 * controls can be moving at a time, and a second red strip would just be more
 * chrome saying the same thing.
 */
export function useBrainSwitch() {
  const chat = useChat();
  const [switchError, setSwitchError] = useState<string | null>(null);

  const switchBrain = (selection: BrainSelection, startErrorMessageId?: string) => {
    if (!chat.activeSlotId) return;
    setSwitchError(null);
    chat
      .switchBrain(chat.activeSlotId, selection, startErrorMessageId)
      .catch((e: Error) => {
        // A retry named by `startErrorMessageId` already reports its own
        // failure inline, on the note it came from — the dismissible banner
        // below is for every other picker, which has no note of its own to
        // report into, and would otherwise just repeat the same words twice.
        if (startErrorMessageId) return;
        setSwitchError(e.message || "Could not switch");
      });
  };

  const switchServer = (slotId: string, serverName: string) => {
    setSwitchError(null);
    chat
      .switchServer(slotId, serverName)
      .catch((e: Error) =>
        setSwitchError(e.message || `Could not switch to server ${serverName}`),
      );
  };

  return {
    switchBrain,
    switchServer,
    switchError,
    dismissSwitchError: () => setSwitchError(null),
  };
}
