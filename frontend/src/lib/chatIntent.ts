/**
 * "Open the chat on *this*" — a one-way channel into the panel.
 *
 * The panel is an overlay owned by `AppShell`, not a route, so a page like
 * `AgentDetail` cannot reach it through props or the router. Same shape as
 * `viewContext`: a module-level channel, deliberately tiny, because the panel
 * is a singleton and there is nothing to key it by.
 */
export interface ChatIntent {
  /** Bind the new conversation to this domain Agent. */
  agentSlug: string;
}

type Listener = (intent: ChatIntent) => void;

const listeners = new Set<Listener>();

/** Ask the panel to open a chat. A no-op if no panel is mounted. */
export function requestChat(intent: ChatIntent): void {
  listeners.forEach((listener) => listener(intent));
}

export function onChatRequest(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
