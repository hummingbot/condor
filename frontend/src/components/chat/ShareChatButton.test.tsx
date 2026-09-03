/**
 * The open chat has a visible way to be shared.
 *
 * The gesture existed only on the rail's conversation row, under a
 * `group-hover` cluster in a column most readers keep collapsed — so sharing
 * the chat you are reading had nothing on screen until the pointer was already
 * on top of the right row. These cases pin what the bar's button promises: it
 * is rendered without a hover, it says which of the two states it is in, and it
 * stays out of the bar entirely when there is nothing to share yet.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConversationMeta } from "@/lib/api";

import { ShareChatButton } from "./ShareChatButton";

const listConversations = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    listConversations: (...args: unknown[]) => listConversations(...args),
  },
}));

// The dialog is the rail's, already covered by its own tests; what matters here
// is only that the button is the thing that opens it.
vi.mock("@/components/chat/ShareConversation", () => ({
  ShareConversation: ({ conversationId }: { conversationId: string }) => (
    <div data-testid="share-dialog">{conversationId}</div>
  ),
}));

let container: HTMLDivElement;
let root: Root;

function meta(over: Partial<ConversationMeta>): ConversationMeta {
  return {
    id: "c1",
    user_id: 1,
    surface: "web",
    title: "A chat",
    agent_key: "claude_acp",
    agent_slug: "",
    server_name: null,
    created_at: "",
    updated_at: "",
    turn_count: 2,
    last_snippet: "",
    ...over,
  };
}

async function render(
  conversationId: string | null,
  rows?: ConversationMeta[],
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  // The cache is seeded rather than fetched: the component's own contract is
  // what it renders *for* a given set of rows, and a real round trip would only
  // add a settle-and-hope to every case, including the two that assert an
  // absence and so have nothing to wait for.
  if (rows) client.setQueryData(["conversations"], rows);
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <ShareChatButton conversationId={conversationId} />
      </QueryClientProvider>,
    );
  });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  listConversations.mockReset();
  listConversations.mockResolvedValue([meta({})]);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("ShareChatButton", () => {
  it("is on screen without a hover", async () => {
    await render("c1", [meta({})]);

    const button = container.querySelector("button");
    expect(button).not.toBeNull();
    // The rail's copy lives inside an `opacity-0 group-hover:opacity-100`
    // cluster. This one is in the bar precisely because it must not.
    expect(button?.className).not.toContain("opacity-0");
    expect(button?.getAttribute("title")).toContain("Share this chat");
  });

  it("says so when this chat has already been shared", async () => {
    await render("c1", [meta({ share_id: "abc" })]);

    const button = container.querySelector("button");
    expect(button?.getAttribute("title")).toContain("review or unshare");
    expect(button?.className).toContain("--color-primary");
  });

  it("renders nothing for a session with no conversation behind it yet", async () => {
    await render(null);

    expect(container.querySelector("button")).toBeNull();
    expect(listConversations).not.toHaveBeenCalled();
  });

  it("renders nothing while the open chat has no row on the server", async () => {
    await render("c1", [meta({ id: "someone-else" })]);

    expect(container.querySelector("button")).toBeNull();
  });

  it("opens the dialog for the chat that is open", async () => {
    await render("c1", [meta({})]);

    await act(async () => {
      container
        .querySelector("button")!
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(
      container.querySelector("[data-testid=share-dialog]")?.textContent,
    ).toBe("c1");
  });
});
