/**
 * A folded group still says when one of its chats needs the reader, and a chat
 * the server has not listed yet (still starting, temporary id) can be opened
 * but not renamed, shared, archived or deleted.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, type AgentSummary, type ConversationMeta } from "@/lib/api";
import { ConversationList, type AgentGroupHeader } from "./ConversationList";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const AGENTS = [
  { slug: "condor", name: "Condor" },
  { slug: "grid", name: "Grid Trader" },
] as AgentSummary[];

function conv(id: string, over: Partial<ConversationMeta> = {}): ConversationMeta {
  return {
    id,
    user_id: 1,
    surface: "web",
    title: id,
    agent_key: "",
    agent_slug: "grid",
    server_name: null,
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
    turn_count: 2,
    last_snippet: "",
    ...over,
  } as ConversationMeta;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  try {
    localStorage.clear();
  } catch {
    /* no storage: groups start closed either way */
  }
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  vi.spyOn(api, "listConversations").mockImplementation(async () => [conv("saved")]);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

async function render(props: {
  waitingIds?: Set<string>;
  busyIds?: Set<string>;
  liveDrafts?: ConversationMeta[];
}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <ConversationList
          agents={AGENTS}
          activeId={null}
          onOpen={() => {}}
          {...props}
          renderAgent={(h: AgentGroupHeader) => (
            <button
              data-group={h.name}
              data-attention={h.attention ?? ""}
              onClick={h.onToggle}
            >
              {h.name}
            </button>
          )}
        />
      </QueryClientProvider>,
    );
  });
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

const header = (name: string) =>
  container.querySelector<HTMLButtonElement>(`[data-group="${name}"]`)!;

describe("a folded group", () => {
  it("shows that one of its chats is waiting on an approval", async () => {
    await render({ waitingIds: new Set(["saved"]), busyIds: new Set(["saved"]) });
    expect(header("Grid Trader").dataset.attention).toBe("waiting");
  });

  it("shows that one of its chats is answering", async () => {
    await render({ busyIds: new Set(["saved"]) });
    expect(header("Grid Trader").dataset.attention).toBe("busy");
  });

  it("leaves it to the rows once opened", async () => {
    await render({ waitingIds: new Set(["saved"]) });
    await act(async () => header("Grid Trader").click());
    expect(header("Grid Trader").dataset.attention).toBe("");
  });
});

describe("a chat the server has not listed yet", () => {
  it("can be opened but not managed", async () => {
    await render({ liveDrafts: [conv("new_1")] });
    await act(async () => header("Grid Trader").click());

    const draft = container.querySelector('[data-chat="new_1"]')!;
    const saved = container.querySelector('[data-chat="saved"]')!;
    expect(draft).not.toBeNull();
    expect(draft.querySelector('[title="Delete"]')).toBeNull();
    expect(draft.querySelector('[title="Rename"]')).toBeNull();
    expect(saved.querySelector('[title="Delete"]')).not.toBeNull();
  });
});
