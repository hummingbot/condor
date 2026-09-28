/**
 * The Controllers tab (FEAT-127): every controller an agent carries, and where
 * each stands on the chat's active server.
 *
 * What is pinned here: each verdict renders its own badge and an unanswered
 * server never reads as in sync; no server means no status fetch; Sync, the
 * drift review and the style upload call their endpoints and re-fetch the
 * status after; overwriting takes two clicks and surfaces the backup, with
 * no stale-backtests warning (each backtest loads the controller fresh); and
 * the panel only shows the tab when there is something in it.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentBrain, ControllerCard } from "@/lib/api";

const getAgentBrain = vi.fn();
const getAgentControllers = vi.fn();
const syncAgentController = vi.fn();
const uploadAgentControllerConfig = vi.fn();
const getAgentControllerSource = vi.fn();
const getAgentControllerSample = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getAgentBrain: (...a: unknown[]) => getAgentBrain(...a),
    getAgentControllers: (...a: unknown[]) => getAgentControllers(...a),
    syncAgentController: (...a: unknown[]) => syncAgentController(...a),
    uploadAgentControllerConfig: (...a: unknown[]) =>
      uploadAgentControllerConfig(...a),
    getAgentControllerSource: (...a: unknown[]) =>
      getAgentControllerSource(...a),
    getAgentControllerSample: (...a: unknown[]) =>
      getAgentControllerSample(...a),
  },
  CHAT_SLUG: "condor",
}));

const serverHolder: { current: string | null } = { current: "srv" };
vi.mock("@/hooks/useServer", () => ({
  useServer: () => ({ server: serverHolder.current, setServer: () => {} }),
}));

const { ControllersTab } = await import("./ControllersTab");
const { AgentKnowledge } = await import("../AgentKnowledge");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function card(name: string, extra: Partial<ControllerCard> = {}): ControllerCard {
  return {
    name,
    controller_type: "market_making",
    description: "",
    styles: [],
    shared: false,
    stock: false,
    type_error: "",
    ...extra,
  };
}

const CARDS = [
  card("alpha"),
  card("bravo"),
  card("charlie"),
  card("delta", { styles: ["tight"] }),
];

function statuses(verdicts: Record<string, string | undefined>) {
  return {
    agent: "brigado",
    server_name: "srv",
    controllers: Object.entries(verdicts).map(([name, verdict]) => ({
      name,
      controller_type: "market_making",
      styles: [],
      shared: false,
      digest: "d",
      ...(verdict ? { server: { verdict } } : {}),
    })),
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

async function click(el: Element) {
  await act(async () => {
    (el as HTMLElement).click();
  });
  await settle();
}

function client() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
}

async function renderTab(cards: ControllerCard[] = CARDS) {
  await act(async () => {
    root.render(
      <QueryClientProvider client={client()}>
        <ControllersTab slug="brigado" controllers={cards} />
      </QueryClientProvider>,
    );
  });
  await settle();
}

function itemFor(name: string): HTMLElement {
  const item = [
    ...container.querySelectorAll("div.rounded-md.border"),
  ].find((d) => d.querySelector("span.font-mono")?.textContent === name);
  if (!item) throw new Error(`No controller "${name}"`);
  return item as HTMLElement;
}

function badgeOf(name: string): string {
  return (
    itemFor(name).querySelector('[data-testid="controller-status"]')
      ?.textContent ?? ""
  );
}

function button(scope: HTMLElement, text: string): HTMLButtonElement {
  const found = [...scope.querySelectorAll("button")].find(
    (b) => b.textContent?.trim() === text,
  );
  if (!found) throw new Error(`No "${text}" button`);
  return found as HTMLButtonElement;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  serverHolder.current = "srv";
  for (const fn of [
    getAgentBrain,
    getAgentControllers,
    syncAgentController,
    uploadAgentControllerConfig,
    getAgentControllerSource,
    getAgentControllerSample,
  ]) {
    fn.mockReset();
  }
  getAgentControllers.mockResolvedValue(
    statuses({
      alpha: "in_sync",
      bravo: "missing",
      charlie: "drift",
      delta: "unreachable",
    }),
  );
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("status badges", () => {
  it("renders each verdict from the status listing", async () => {
    await renderTab();
    expect(getAgentControllers).toHaveBeenCalledWith("brigado", "srv");
    expect(badgeOf("alpha")).toBe("in sync");
    expect(badgeOf("bravo")).toBe("missing");
    expect(badgeOf("charlie")).toBe("drift");
    expect(badgeOf("delta")).toBe("unreachable");
  });

  it("never reads an unanswered server as in sync", async () => {
    getAgentControllers.mockResolvedValue(
      statuses({ alpha: undefined, bravo: "missing" }),
    );
    await renderTab();
    expect(badgeOf("alpha")).toBe("unreachable");
    // A controller the listing left out entirely is not in sync either.
    expect(badgeOf("charlie")).toBe("unreachable");
  });

  it("reads a failed status fetch as unreachable", async () => {
    getAgentControllers.mockRejectedValue(new Error("502 offline"));
    await renderTab();
    for (const name of ["alpha", "bravo", "charlie", "delta"]) {
      expect(badgeOf(name)).toBe("unreachable");
    }
  });

  it("says checking while the server has not answered yet", async () => {
    getAgentControllers.mockReturnValue(new Promise(() => {}));
    await renderTab();
    expect(badgeOf("alpha")).toBe("checking…");
  });

  it("asks nothing with no active server", async () => {
    serverHolder.current = null;
    await renderTab();
    expect(getAgentControllers).not.toHaveBeenCalled();
    expect(badgeOf("alpha")).toBe("no server");
    expect(() => button(itemFor("bravo"), "Sync")).toThrow();
  });

  it("flags a controller whose type cannot be resolved and offers no sync", async () => {
    await renderTab([
      card("bravo", {
        controller_type: null,
        type_error: "add `type:` to its CONTROLLER.md",
      }),
    ]);
    expect(itemFor("bravo").textContent).toContain("type unknown");
    expect(itemFor("bravo").textContent).toContain("CONTROLLER.md");
    expect(() => button(itemFor("bravo"), "Sync")).toThrow();
  });
});

describe("actions", () => {
  it("syncs a missing controller and re-fetches the status", async () => {
    syncAgentController.mockResolvedValue({
      name: "bravo",
      verdict: "missing",
      changed: true,
      message: "Created 'bravo' (market_making) on srv.",
    });
    await renderTab();
    expect(getAgentControllers).toHaveBeenCalledTimes(1);

    await click(button(itemFor("bravo"), "Sync"));

    expect(syncAgentController).toHaveBeenCalledWith(
      "brigado",
      "bravo",
      "srv",
      false,
    );
    expect(getAgentControllers).toHaveBeenCalledTimes(2);
    expect(itemFor("bravo").textContent).toContain("Created 'bravo'");
  });

  it("reviews a drift's diff, then overwrites only on the second click", async () => {
    syncAgentController.mockImplementation(
      async (_s: string, name: string, _srv: string, overwrite: boolean) =>
        overwrite
          ? {
              name,
              verdict: "drift",
              changed: true,
              overwritten: true,
              backup: "agents/brigado/controllers/.server_backups/charlie.py",
              message: "Replaced 'charlie' on srv.",
            }
          : {
              name,
              verdict: "drift",
              changed: false,
              refused: true,
              reason: "the server's 'charlie' differs from the folder.",
              diff: "--- srv\n+++ folder\n-old = 1\n+new = 2",
            },
    );
    await renderTab();

    await click(button(itemFor("charlie"), "Review diff"));
    expect(syncAgentController).toHaveBeenLastCalledWith(
      "brigado",
      "charlie",
      "srv",
      false,
    );
    expect(
      itemFor("charlie").querySelector('[data-testid="controller-diff"]')
        ?.textContent,
    ).toContain("+new = 2");

    await click(
      button(itemFor("charlie"), "Overwrite server copy (backup kept)"),
    );
    // Armed, not sent.
    expect(syncAgentController).toHaveBeenCalledTimes(1);

    await click(button(itemFor("charlie"), "Confirm"));
    expect(syncAgentController).toHaveBeenLastCalledWith(
      "brigado",
      "charlie",
      "srv",
      true,
    );
    expect(itemFor("charlie").textContent).toContain(".server_backups/charlie.py");
    // Backtests load the controller fresh on every run: nothing to warn about.
    expect(container.querySelector('[role="status"]')).toBeNull();
    expect(container.textContent).not.toMatch(/restart the api|api restarts/i);
    // Once for the tab, once after each of the two syncs.
    expect(getAgentControllers).toHaveBeenCalledTimes(3);
  });

  it("shows the impact before the overwrite and names the running bots on the second click", async () => {
    const impactText =
      "This replaces the SERVER copy of 'charlie' on srv with your FOLDER copy.\n" +
      "• Running bots using it: bot-a (1 config), bot-b (2 configs)";
    syncAgentController.mockResolvedValue({
      name: "charlie",
      verdict: "drift",
      refused: true,
      reason: "differs — show the user the diff and the impact",
      diff: "-a\n+b",
      impact_text: impactText,
      impact: {
        controller: "charlie",
        server_name: "srv",
        shared_owners: [],
        live_bots: [
          { bot_name: "bot-a", config_ids: ["c1"] },
          { bot_name: "bot-b", config_ids: ["c2", "c3"] },
        ],
        live_bots_error: "",
        server_had_copy: true,
      },
    });
    await renderTab();
    await click(button(itemFor("charlie"), "Review diff"));

    expect(
      itemFor("charlie").querySelector('[data-testid="controller-impact"]')
        ?.textContent,
    ).toBe(impactText);

    await click(
      button(itemFor("charlie"), "Overwrite server copy (backup kept)"),
    );
    expect(
      button(itemFor("charlie"), "Confirm — 2 running bots keep the old class"),
    ).toBeTruthy();
    expect(syncAgentController).toHaveBeenCalledTimes(1);
  });

  it("says running bots are unknown, not none, when they could not be checked", async () => {
    syncAgentController.mockResolvedValue({
      name: "charlie",
      verdict: "drift",
      refused: true,
      reason: "differs",
      diff: "-a\n+b",
      impact_text: "could not check running bots — treat as unknown, not as none",
      impact: {
        controller: "charlie",
        server_name: "srv",
        shared_owners: [],
        live_bots: null,
        live_bots_error: "listing bots failed",
        server_had_copy: true,
      },
    });
    await renderTab();
    await click(button(itemFor("charlie"), "Review diff"));
    await click(
      button(itemFor("charlie"), "Overwrite server copy (backup kept)"),
    );
    expect(
      button(itemFor("charlie"), "Confirm — running bots unknown"),
    ).toBeTruthy();
  });

  it("can back out of an armed overwrite", async () => {
    syncAgentController.mockResolvedValue({
      name: "charlie",
      verdict: "drift",
      refused: true,
      reason: "differs",
      diff: "-a\n+b",
    });
    await renderTab();
    await click(button(itemFor("charlie"), "Review diff"));
    await click(
      button(itemFor("charlie"), "Overwrite server copy (backup kept)"),
    );
    await click(button(itemFor("charlie"), "Cancel"));
    expect(syncAgentController).toHaveBeenCalledTimes(1);
    expect(
      button(itemFor("charlie"), "Overwrite server copy (backup kept)"),
    ).toBeTruthy();
  });
});

describe("reading and uploading styles", () => {
  it("shows the source, a style's YAML, and uploads it with a two-step overwrite", async () => {
    getAgentControllerSource.mockResolvedValue({
      name: "delta",
      controller_type: "market_making",
      description: "",
      shared: false,
      digest: "d",
      source: "class Delta: pass",
      styles: ["tight"],
    });
    getAgentControllerSample.mockResolvedValue({
      name: "delta",
      sample: "tight",
      yaml: "spread: 0.1",
    });
    uploadAgentControllerConfig
      .mockResolvedValueOnce({
        name: "delta",
        refused: true,
        config_name: "delta__tight",
        reason: "config 'delta__tight' exists on srv and differs",
        diff: "-spread: 0.5\n+spread: 0.1",
      })
      .mockResolvedValueOnce({
        name: "delta",
        changed: true,
        config_name: "delta__tight",
        message: "Replaced config 'delta__tight' on srv from style 'tight'.",
      });
    await renderTab();

    await click(itemFor("delta").querySelector("button[aria-expanded]")!);
    expect(itemFor("delta").textContent).toContain("class Delta: pass");

    await click(button(itemFor("delta"), "tight"));
    expect(getAgentControllerSample).toHaveBeenCalledWith(
      "brigado",
      "delta",
      "tight",
    );
    expect(itemFor("delta").textContent).toContain("spread: 0.1");

    await click(button(itemFor("delta"), "Upload as delta__tight"));
    expect(uploadAgentControllerConfig).toHaveBeenLastCalledWith(
      "brigado",
      "delta",
      "tight",
      "srv",
      false,
    );
    expect(itemFor("delta").textContent).toContain("exists on srv and differs");

    await click(button(itemFor("delta"), "Overwrite delta__tight"));
    expect(uploadAgentControllerConfig).toHaveBeenCalledTimes(1);
    await click(button(itemFor("delta"), "Confirm"));
    expect(uploadAgentControllerConfig).toHaveBeenLastCalledWith(
      "brigado",
      "delta",
      "tight",
      "srv",
      true,
    );
    expect(itemFor("delta").textContent).toContain("Replaced config");
    expect(getAgentControllers).toHaveBeenCalledTimes(3);
  });
});

describe("the panel's Controllers tab", () => {
  function brain(controllers?: ControllerCard[]): AgentBrain {
    return {
      slug: "brigado",
      name: "Brigado",
      description: "",
      agent_md: "# Brigado",
      agent_key: "claude-code",
      when_to_consult: "",
      server_required: false,
      server_name: "",
      tools: [],
      tools_unrestricted: true,
      skills: [],
      skill_proposal: null,
      memories: [],
      routines: [],
      strategies: [],
      ...(controllers ? { controllers } : {}),
    };
  }

  async function renderPanel() {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client()}>
          <AgentKnowledge slug="brigado" />
        </QueryClientProvider>,
      );
    });
    await settle();
  }

  const tabNames = () =>
    [...container.querySelectorAll('[role="tab"]')].map((t) =>
      t.getAttribute("aria-label"),
    );

  it("shows the tab with its count when the agent carries controllers", async () => {
    getAgentBrain.mockResolvedValue(brain([card("alpha"), card("bravo")]));
    await renderPanel();
    expect(tabNames()).toContain("Controllers (2)");
    // The brain alone paints the tab; the status is the tab's own fetch.
    expect(getAgentControllers).not.toHaveBeenCalled();
  });

  it("hides the tab for an agent without controllers", async () => {
    getAgentBrain.mockResolvedValue(brain([]));
    await renderPanel();
    expect(tabNames().some((n) => n?.startsWith("Controllers"))).toBe(false);
  });

  it("hides the tab when the server predates the field", async () => {
    getAgentBrain.mockResolvedValue(brain());
    await renderPanel();
    expect(tabNames().some((n) => n?.startsWith("Controllers"))).toBe(false);
  });
});
