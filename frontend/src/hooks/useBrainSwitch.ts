import { useCallback, useState } from "react";

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
  // Read field by field: the `chat` object is a new one on every stream flush,
  // and the two callbacks below are handed to memoised children (FU-04).
  const {
    activeSlotId,
    switchBrain: chatSwitchBrain,
    switchServer: chatSwitchServer,
  } = useChat();
  const [switchError, setSwitchError] = useState<string | null>(null);

  const switchBrain = useCallback(
    (selection: BrainSelection, startErrorMessageId?: string) => {
      if (!activeSlotId) return;
      setSwitchError(null);
      chatSwitchBrain(activeSlotId, selection, startErrorMessageId).catch(
        (e: Error) => {
          // A retry named by `startErrorMessageId` already reports its own
          // failure inline, on the note it came from — the dismissible banner
          // below is for every other picker, which has no note of its own to
          // report into, and would otherwise just repeat the same words twice.
          if (startErrorMessageId) return;
          setSwitchError(e.message || "Could not switch");
        },
      );
    },
    [activeSlotId, chatSwitchBrain],
  );

  const switchServer = useCallback(
    (slotId: string, serverName: string) => {
      setSwitchError(null);
      chatSwitchServer(slotId, serverName).catch((e: Error) =>
        setSwitchError(e.message || `Could not switch to server ${serverName}`),
      );
    },
    [chatSwitchServer],
  );

  return {
    switchBrain,
    switchServer,
    switchError,
    dismissSwitchError: () => setSwitchError(null),
  };
}
