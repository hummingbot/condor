/**
 * The Tools tab is the seat's real mounted surface, with a switch (FEAT-091).
 *
 * It used to echo the AGENT.md allowlist, which only binds pydantic-ai model
 * keys — an ACP bridge runs unrestricted — so for most agents here the tab was
 * telling the reader something untrue about what the model can reach. What is
 * pinned here is the replacement: rows grouped by MCP server, the allowlist
 * shown as one flag among the rows rather than as the whole list, the switch
 * reaching the endpoint with `kind: "tool"`, and the muted row still visible so
 * the curation can be undone.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentBrain } from "@/lib/api";

const getAgentBrain = vi.fn();
const setAgentMute = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getAgentBrain: (...args: unknown[]) => getAgentBrain(...args),
    setAgentMute: (...args: unknown[]) => setAgentMute(...args),
  },
  CHAT_SLUG: "condor",
}));

const { AgentKnowledge } = await import("./AgentKnowledge");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function brain(
  opts: { mutedClmm?: boolean; allowlist?: string[] } = {},
): AgentBrain {
  const allowlist = opts.allowlist ?? [];
  const tool = (name: string, server: string, description: string) => ({
    name,
    server,
    description,
    muted: name === "manage_clmm" ? (opts.mutedClmm ?? false) : false,
    allowlisted: allowlist.includes(name),
  });
  return {
    slug: "brigado",
    name: "Brigado",
    description: "BRL market making",
    agent_md: "# Brigado",
    agent_key: "claude-code",
    when_to_consult: "",
    server_required: false,
    server_name: "",
    tools: [
      tool("consult", "condor", "Ask a specialist agent a question"),
      tool("run_code", "condor", "Run a Python snippet inside Condor"),
      tool("get_candles", "hummingbot", "OHLCV candles for a pair"),
      tool("manage_clmm", "hummingbot", "Direct CLMM position operations"),
    ],
    tools_unrestricted: allowlist.length === 0,
    skills: [],
    skill_proposal: null,
    memories: [],
    routines: [],
    strategies: [],
  };
}

let container: HTMLDivElement;
let root: Root;

async function settle() {
  for (let i = 0; i < 10; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}


function rowFor(title: string): HTMLElement {
  const row = [...container.querySelectorAll("div.group")].find((d) =>
    d.textContent?.includes(title),
  );
  if (!row) throw new Error(`No row for "${title}"`);
  return row as HTMLElement;
}

/** The row's switch, addressed by the label it exposes to a screen reader. */
function switchFor(title: string): HTMLButtonElement {
  const found = rowFor(title).querySelector('button[role="switch"]');
  if (!found) throw new Error(`No switch on the "${title}" row`);
  return found as HTMLButtonElement;
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.click();
  });
  await settle();
}

async function openTools() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <AgentKnowledge slug="brigado" tab="tools" />
      </QueryClientProvider>,
    );
  });
  await settle();
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getAgentBrain.mockReset().mockResolvedValue(brain());
  setAgentMute.mockReset().mockResolvedValue({
    kind: "tool",
    name: "manage_clmm",
    muted: true,
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("the tools tab", () => {
  it("lists every mounted tool, grouped by its server", async () => {
    await openTools();

    for (const name of ["consult", "run_code", "get_candles", "manage_clmm"]) {
      expect(rowFor(name)).toBeTruthy();
    }
    expect(container.textContent).toContain("Condor");
    expect(container.textContent).toContain("Hummingbot");
    // The one-liner the panel renders under each name.
    expect(container.textContent).toContain("OHLCV candles for a pair");
  });

  it("says when the change lands", async () => {
    await openTools();
    // Not "the next session this agent starts" any more: a chat that is
    // already open reloads itself on the next message (FEAT-093), so the copy
    // names something the reader can actually do.
    expect(container.textContent).toContain(
      "from the next tick, or from your next message in an open chat",
    );
  });

  it("keeps the AGENT.md allowlist visible as its own statement", async () => {
    getAgentBrain.mockResolvedValue(brain({ allowlist: ["get_candles"] }));
    await openTools();

    expect(rowFor("get_candles").textContent).toContain("allowlisted");
    expect(rowFor("consult").textContent).not.toContain("allowlisted");
    // …and the tab still lists the whole surface, not just the allowlist.
    expect(rowFor("manage_clmm")).toBeTruthy();
    expect(container.textContent).toContain("Edit it in the Brain tab");
  });

  it("points at the Brain tab when no allowlist is written", async () => {
    await openTools();
    expect(container.textContent).toContain("AGENT.md names no allowlist");
  });
});

describe("muting a tool", () => {
  it("switches it off through the endpoint, by name", async () => {
    await openTools();

    const toggle = switchFor("manage_clmm");
    expect(toggle.getAttribute("aria-checked")).toBe("true");

    await click(toggle);

    expect(setAgentMute).toHaveBeenCalledWith("brigado", {
      kind: "tool",
      name: "manage_clmm",
      muted: true,
    });
  });

  it("keeps the muted row listed, flagged and switchable back on", async () => {
    getAgentBrain.mockResolvedValue(brain({ mutedClmm: true }));
    await openTools();

    expect(rowFor("manage_clmm").textContent).toContain("muted");
    const toggle = switchFor("manage_clmm");
    expect(toggle.getAttribute("aria-checked")).toBe("false");

    await click(toggle);

    expect(setAgentMute).toHaveBeenCalledWith("brigado", {
      kind: "tool",
      name: "manage_clmm",
      muted: false,
    });
  });
});
