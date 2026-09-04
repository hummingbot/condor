/**
 * FEAT-111: a link can open one conversation.
 *
 * The Runs rail lists an agent's chats beside its loop runs, and a chat row has
 * to go somewhere — the chat is the surface for a conversation. Navigation is
 * the only channel a different page has, so the id travels as `?conversation=`.
 * What that handover has to guarantee is pinned here: the record's brain and
 * server travel with it, the parameter is consumed exactly once so a reload is
 * not a second resume, and an id nothing on disk answers for is still consumed
 * rather than retried forever.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listConversations = vi.fn<(...a: unknown[]) => Promise<unknown[]>>();

vi.mock("@/lib/api", () => ({
  api: { listConversations: (...a: unknown[]) => listConversations(...a) },
}));

const { useConversationParam } = await import("./useConversationParam");

const meta = (over: Record<string, unknown> = {}) => ({
  id: "7f3a",
  user_id: 1,
  surface: "web",
  title: "What is the fleet doing?",
  agent_key: "claude-code",
  agent_slug: "brigado",
  server_name: "brigado_2",
  turn_count: 4,
  last_snippet: "",
  ...over,
});

let container: HTMLDivElement;
let root: Root;
const resume = vi.fn();
let search = "";

function Probe() {
  useConversationParam(resume);
  // Published from an effect, never during render: `act` flushes effects, so
  // the probe is current by the time a test reads it.
  const current = useLocation().search;
  useEffect(() => {
    search = current;
  }, [current]);
  return null;
}

async function render(initial: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[initial]}>
          <Probe />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  // A few turns for the query to settle and for the effect to run on it. The
  // handover waits for the list either way, so it lands a tick after the fetch.
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
    true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  resume.mockClear();
  listConversations.mockReset();
  listConversations.mockResolvedValue([meta()]);
  search = "";
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("?conversation=", () => {
  it("resumes the conversation, carrying the brain that was answering", async () => {
    await render("/?conversation=7f3a");
    expect(resume).toHaveBeenCalledWith("7f3a", {
      agent_key: "claude-code",
      server_name: "brigado_2",
      agent_slug: "brigado",
    });
  });

  it("strips the parameter, so a reload is not a second resume", async () => {
    await render("/?conversation=7f3a");
    expect(search).toBe("");
    expect(resume).toHaveBeenCalledTimes(1);
  });

  it("leaves every other parameter where it was", async () => {
    await render("/?conversation=7f3a&panel=desk");
    expect(search).toBe("?panel=desk");
  });

  it("consumes an id nothing answers for, rather than retrying forever", async () => {
    // A deleted conversation would otherwise be resumed once per frame, and
    // every resume is a spawn against the session budget.
    listConversations.mockResolvedValue([]);
    await render("/?conversation=gone");
    expect(resume).toHaveBeenCalledWith("gone", undefined);
    expect(search).toBe("");
  });

  it("opens the link even when the conversation list cannot be read", async () => {
    listConversations.mockRejectedValue(new Error("offline"));
    await render("/?conversation=7f3a");
    expect(resume).toHaveBeenCalledWith("7f3a", undefined);
  });

  it("does nothing, and asks for nothing, without the parameter", async () => {
    await render("/?panel=desk");
    expect(resume).not.toHaveBeenCalled();
    expect(listConversations).not.toHaveBeenCalled();
    expect(search).toBe("?panel=desk");
  });
});
