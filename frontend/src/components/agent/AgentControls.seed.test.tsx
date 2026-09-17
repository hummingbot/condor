/**
 * The Start dialog seeds from the config as it stands when opened (CORR-387).
 *
 * Every field of StartSessionDialog is a useState seed. While the dialog was
 * mounted unconditionally it seeded once, on AgentControls' first render, so a
 * restart_on_boot flipped by the RestartChip (or a server set from Telegram)
 * after the page loaded was overridden by Start with the stale first seed.
 * AgentControls now mounts the dialog per open; these cases pin that.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const startStrategy = vi.fn(async (...args: unknown[]) => ({ args }));

vi.mock("@/lib/api", () => ({
  api: {
    getAgent: () => Promise.resolve({}),
    getServers: () => Promise.resolve([{ name: "brigado_2", online: true }]),
    resumeStrategy: () => Promise.resolve({}),
    pauseStrategy: () => Promise.resolve({}),
    stopStrategy: () => Promise.resolve({}),
    startStrategy: (...a: unknown[]) => startStrategy(...a),
  },
}));

const { AgentControls } = await import("./AgentControls");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;

function render(agentConfig: Record<string, unknown>) {
  act(() => {
    root.render(
      <QueryClientProvider client={client}>
        <AgentControls
          slug="brigado"
          sslug="brl_mm"
          status="idle"
          defaultContext=""
          agentConfig={agentConfig}
        />
      </QueryClientProvider>,
    );
  });
}

function button(label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find(
    (b) => (b.textContent ?? "").trim() === label,
  );
  expect(found, `button ${label}`).toBeTruthy();
  return found as HTMLButtonElement;
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

/** Let the servers query resolve so the select can hold brigado_2. */
async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

function checkbox(): HTMLInputElement {
  const box = container.querySelector<HTMLInputElement>('input[type="checkbox"]');
  expect(box?.closest("label")?.textContent).toContain("Resume after Condor restarts");
  return box!;
}

function select(): HTMLSelectElement {
  return container.querySelector("select")!;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  startStrategy.mockClear();
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("StartSessionDialog seeding", () => {
  it("seeds from the config written after the controls first rendered", async () => {
    render({ restart_on_boot: false, server_name: "" });
    render({ restart_on_boot: true, server_name: "brigado_2" });

    await click(button("Start"));
    await settle();

    expect(checkbox().checked).toBe(true);
    expect(select().value).toBe("brigado_2");

    await click(button("Start Session"));
    await settle();

    expect(startStrategy).toHaveBeenCalledTimes(1);
    const config = startStrategy.mock.calls[0][2] as Record<string, unknown>;
    expect(config.restart_on_boot).toBe(true);
    expect(config.server_name).toBe("brigado_2");
  });

  it("drops a cancelled half-edit and reseeds from the new config on reopen", async () => {
    render({ restart_on_boot: false, server_name: "" });

    await click(button("Start"));
    await settle();
    expect(checkbox().checked).toBe(false);
    await click(checkbox());
    expect(checkbox().checked).toBe(true);
    await click(button("Cancel"));
    expect(container.querySelector('input[type="checkbox"]')).toBeNull();

    render({ restart_on_boot: false, server_name: "brigado_2" });
    await click(button("Start"));
    await settle();

    expect(checkbox().checked).toBe(false);
    expect(select().value).toBe("brigado_2");
  });
});
