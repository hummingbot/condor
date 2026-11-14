/**
 * The rail's chats filed by agent, with "Archived" holding only what its owner
 * archived and chats whose agent is no longer installed not listed at all. And
 * archiving moves a chat between its agent's group and the archive at once,
 * through the API.
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

function conv(id: string, over: Partial<ConversationMeta>): ConversationMeta {
  return {
    id,
    user_id: 1,
    surface: "web",
    title: id,
    agent_key: "",
    agent_slug: "",
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
let list: ConversationMeta[];

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  list = [
    conv("talk-grid", { agent_slug: "grid", updated_at: "2026-11-03T00:00:00Z" }),
    conv("put-away", { agent_slug: "grid", archived: true }),
    conv("ghost", { agent_slug: "quad_threat" }),
  ];
  vi.spyOn(api, "listConversations").mockImplementation(async () => list);
  vi.spyOn(api, "setConversationArchived").mockImplementation(async (id, archived) => {
    list = list.map((c) => (c.id === id ? { ...c, archived } : c));
    return list.find((c) => c.id === id)!;
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

/** Which chats sit under which header, in render order. */
function filed(): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  let current = "";
  for (const el of container.querySelectorAll("[data-group], [data-chat]")) {
    const group = el.getAttribute("data-group");
    if (group !== null) {
      current = group;
      out[current] = [];
    } else if (current) {
      out[current].push(el.getAttribute("data-chat")!);
    }
  }
  return out;
}

async function render() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <ConversationList
          agents={AGENTS}
          activeId={null}
          onOpen={() => {}}
          renderAgent={(h: AgentGroupHeader) => (
            // Every group open, so each chat is in the DOM under its header.
            <button data-group={h.name} onClick={h.expanded ? undefined : h.onToggle}>
              {h.name}
            </button>
          )}
        />
      </QueryClientProvider>,
    );
  });
  // Let the conversations query land before reading the groups.
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
  // Open the folded foot groups so their chats render too.
  for (const b of container.querySelectorAll<HTMLButtonElement>("[data-group]")) {
    await act(async () => b.click());
  }
}

describe("order", () => {
  it("keeps Condor first even when a specialist's chat is newer", async () => {
    list = [
      conv("grid-new", { agent_slug: "grid", updated_at: "2026-11-05T00:00:00Z" }),
      conv("condor-old", { agent_slug: "", updated_at: "2026-10-01T00:00:00Z" }),
    ];
    await render();
    const headers = [...container.querySelectorAll("[data-group]")].map((h) =>
      h.getAttribute("data-group"),
    );
    expect(headers[0]).toBe("Condor");
    expect(headers.indexOf("Grid Trader")).toBeGreaterThan(0);
  });

  it("keeps Condor first before it has any chat", async () => {
    list = [conv("grid-new", { agent_slug: "grid" })];
    await render();
    expect(container.querySelector("[data-group]")?.getAttribute("data-group")).toBe("Condor");
  });
});

describe("archived and deleted-agent chats", () => {
  it("files each chat in one place: its agent, or Archived; never a removed agent's", async () => {
    await render();
    const f = filed();
    expect(f["Grid Trader"]).toEqual(["talk-grid"]);
    expect(f["Archived"]).toEqual(["put-away"]);
    // A chat whose agent is not installed is not listed anywhere.
    expect(Object.values(f).flat()).not.toContain("ghost");
    expect(f["From deleted agents"]).toBeUndefined();
  });

  it("archives through the API and moves the chat at once", async () => {
    await render();
    const archive = container.querySelector<HTMLButtonElement>(
      '[data-chat="talk-grid"] button[title="Archive"]',
    )!;
    await act(async () => {
      archive.click();
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(api.setConversationArchived).toHaveBeenCalledWith("talk-grid", true);
    expect(filed()["Archived"]).toContain("talk-grid");
    expect(filed()["Grid Trader"] ?? []).not.toContain("talk-grid");
  });

  it("restores an archived chat to its agent", async () => {
    await render();
    const restore = container.querySelector<HTMLButtonElement>(
      '[data-chat="put-away"] button[title="Move back to your chats"]',
    )!;
    await act(async () => {
      restore.click();
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(api.setConversationArchived).toHaveBeenCalledWith("put-away", false);
    expect(filed()["Archived"] ?? []).not.toContain("put-away");
  });
});
