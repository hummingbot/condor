/**
 * Marks an element as part of a chat — the transcript, the composer.
 *
 * Escape stops the answer only when the key was pressed inside one (FU-05):
 * the chat is on screen on every page that has the bubble open, and an Escape
 * meant for something else there must not stop an agent mid-trade. Spread the
 * props onto the element; `inChatSurface` is the matching test.
 */
export const CHAT_SURFACE_PROPS = { "data-chat-surface": "" } as const;

/** Was this key pressed with focus inside a chat surface? */
export function inChatSurface(e: KeyboardEvent): boolean {
  return e.target instanceof Element && !!e.target.closest("[data-chat-surface]");
}
