/**
 * The header's two agent-wide writes say when they were refused.
 *
 * The server pin and the model pick both write AGENT.md front matter, and the
 * backend refuses them for real reasons: a server the caller has no access to
 * (403), a picker key that is not a model (400), a read-only file or a stopped
 * backend. Neither control renders optimistically — the menu closes and the
 * chip keeps reading what is stored — so before this a refusal looked exactly
 * like a click that did not register.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentDetail } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  CHAT_SLUG: "condor",
  api: {
    getServers: vi.fn(async () => [{ name: "brigado_2", online: true }]),
    updateAgentConfig: vi.fn(),
  },
}));

vi.mock("@/hooks/useChat", () => ({
  useSessionOptions: () => ({ agents: [], customProviders: [] }),
}));

// The real picker's model list and portalled panel are not what is under test:
// one button that picks a fixed key is enough to drive the write.
vi.mock("@/components/chat/BrainPicker", () => ({
  BrainPicker: ({
    selectedAgentKey,
    onSelect,
  }: {
    selectedAgentKey: string;
    onSelect: (sel: { agentKey?: string }) => void;
  }) => (
    <button data-brain-picker onClick={() => onSelect({ agentKey: "claude-fable-5" })}>
      {selectedAgentKey}
    </button>
  ),
}));

// Positioning needs layout jsdom does not have; render the panel inline.
vi.mock("@/components/ui/AnchoredMenu", () => ({
  AnchoredMenu: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? <div data-menu>{children}</div> : null,
}));

const { WorkspaceHeader } = await import("./WorkspaceHeader");
const { api } = await import("@/lib/api");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let host: HTMLDivElement;
let root: Root;

const AGENT = {
  slug: "brigado",
  name: "Brigado",
  agent_key: "claude-opus-5",
  server_name: "brigado_1",
  tools: [],
} as unknown as AgentDetail;

async function flush() {
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function render() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <WorkspaceHeader
            agent={AGENT}
            strategy={null}
            isRunning={false}
            onAskAgent={() => {}}
            onDelete={() => {}}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
}

const pinChip = () =>
  [...host.querySelectorAll<HTMLButtonElement>('button[aria-haspopup="listbox"]')][0];
const alertText = () =>
  [...host.querySelectorAll('[role="alert"]')].map((n) => n.textContent).join(" ");

async function pickServer(name: string) {
  await act(async () => pinChip().click());
  await flush();
  const option = [...host.querySelectorAll<HTMLButtonElement>("[data-menu] button")].find(
    (b) => b.textContent?.trim() === name,
  );
  expect(option).toBeDefined();
  await act(async () => option!.click());
  await flush();
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.mocked(api.updateAgentConfig).mockReset();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("the server pin", () => {
  it("says why a pin was refused, keeps the stored pin, and clears on the next pick", async () => {
    vi.mocked(api.updateAgentConfig).mockRejectedValueOnce(
      new Error("AGENT.md is read-only"),
    );
    await render();
    await pickServer("brigado_2");

    expect(api.updateAgentConfig).toHaveBeenCalledWith("brigado", { server_name: "brigado_2" });
    expect(alertText()).toContain("AGENT.md is read-only");
    expect(pinChip().textContent).toContain("brigado_1");

    vi.mocked(api.updateAgentConfig).mockResolvedValueOnce(
      undefined as unknown as Awaited<ReturnType<typeof api.updateAgentConfig>>,
    );
    await pickServer("brigado_2");
    expect(alertText()).not.toContain("AGENT.md is read-only");
  });
});

describe("the model pick", () => {
  it("says why a pick was refused and clears on the next pick", async () => {
    vi.mocked(api.updateAgentConfig).mockRejectedValueOnce(new Error("Unknown model"));
    await render();
    const picker = () => host.querySelector<HTMLButtonElement>("[data-brain-picker]")!;

    await act(async () => picker().click());
    await flush();
    expect(api.updateAgentConfig).toHaveBeenCalledWith("brigado", {
      agent_key: "claude-fable-5",
    });
    expect(alertText()).toContain("Unknown model");
    expect(picker().textContent).toBe("claude-opus-5");

    vi.mocked(api.updateAgentConfig).mockResolvedValueOnce(
      undefined as unknown as Awaited<ReturnType<typeof api.updateAgentConfig>>,
    );
    await act(async () => picker().click());
    await flush();
    expect(alertText()).not.toContain("Unknown model");
  });
});
