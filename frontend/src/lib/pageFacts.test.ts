/**
 * The page-context baseline (FEAT-059): what each route says about itself,
 * how the block is rendered for the wire, and the registry semantics that
 * replaced `lib/viewContext.ts` — two overlapping contributors must both
 * speak, and an unmount must remove only its own entry (the old module was
 * one mutable slot, and the outer unmount wiped the inner contribution).
 *
 * The registry is exercised through the hook, so this file needs a DOM.
 *
 * @vitest-environment jsdom
 */

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { routeFacts } from "./pageFacts";
import {
  collectViewFacts,
  renderViewBlock,
  useViewFacts,
  VIEW_BLOCK_MAX_CHARS,
  type ViewFacts,
} from "./viewFacts";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

describe("routeFacts", () => {
  it("says nothing on the chat workspace", () => {
    expect(routeFacts("/", "")).toBeNull();
  });

  it("labels every plain page", () => {
    expect(routeFacts("/portfolio", "")?.label).toBe("Portfolio");
    expect(routeFacts("/bots", "")?.label).toBe("Bots");
    expect(routeFacts("/trade", "")?.label).toBe("Trade");
    expect(routeFacts("/dex", "")?.label).toBe("DEX pools");
    expect(routeFacts("/routines", "")?.label).toBe("Routines");
    expect(routeFacts("/settings", "")?.label).toBe("Settings");
  });

  it("derives the subject from the URL", () => {
    expect(routeFacts("/bots/42", "")).toEqual({
      label: "Bot detail",
      subject: "bot id 42",
    });
    expect(routeFacts("/dex/solana/7qbRF6", "")).toEqual({
      label: "DEX pool",
      subject: "pool 7qbRF6 on solana",
    });
    expect(routeFacts("/agents/orca-lp-expert", "")).toEqual({
      label: "Agent workspace",
      subject: 'agent "orca-lp-expert"',
    });
    // One route with nine views (FEAT-103): the strategy is a scope in the
    // query string, and the label follows the view rather than the path.
    expect(
      routeFacts("/agents/orca-lp-expert", "?view=playbook&strategy=sol-lp"),
    ).toEqual({
      label: "Strategy playbook",
      subject: 'strategy "sol-lp" of agent "orca-lp-expert"',
    });
  });

  it("decodes URL-encoded parts", () => {
    expect(routeFacts("/agents/my%20agent", "")?.subject).toBe('agent "my agent"');
  });

  it("reads the tab from the query string", () => {
    // `?tab=runs` and `?tab=archived` are both the browser's Terminated
    // population now (FEAT-086), and `Bots.tsx` redirects them into it — so a
    // stale link names the one screen it lands on rather than a tab nobody can
    // be on.
    expect(routeFacts("/routines", "?tab=reports")?.label).toBe("Routine reports");
  });

  it("says nothing for /executors, which is a scope of /bots now", () => {
    // The page is a `<Navigate>` (FEAT-086). A branch for it here would be a
    // screen the fact table claims the user can be on and the router disagrees.
    expect(routeFacts("/executors", "")).toBeNull();
  });

  it("labels /bots itself as the browser, tab or no tab", () => {
    // The tab bar is gone (FEAT-084): `/bots` is the controller browser, and a
    // stale link to a tab that no longer exists lands on it rather than being
    // announced as a screen nobody can be on.
    expect(routeFacts("/bots", "")?.label).toBe("Bots");
    expect(routeFacts("/bots", "?tab=editor")?.label).toBe("Bots");
    expect(routeFacts("/bots", "?tab=backtest")?.label).toBe("Bots");
    // Runs and Archived are the Terminated population now, and `Bots.tsx`
    // redirects both into it — so they name the browser too (FEAT-086).
    expect(routeFacts("/bots", "?tab=runs")?.label).toBe("Bots");
    expect(routeFacts("/bots", "?tab=archived")?.label).toBe("Bots");
    expect(routeFacts("/bots", "?scope=bot:mm-1")?.label).toBe("Bots");
  });

  it("says nothing on a route it does not know", () => {
    expect(routeFacts("/login", "")).toBeNull();
    expect(routeFacts("/no/such/page", "")).toBeNull();
  });
});

describe("renderViewBlock", () => {
  it("is empty with nothing to say", () => {
    expect(renderViewBlock([], "/bots/42")).toBe("");
  });

  it("renders the block the design promises", () => {
    const facts: ViewFacts[] = [
      {
        label: "Bot detail",
        subject: 'bot "backpack-mm-3" (id 42)',
        onScreen: { PNL: "$-412.30", controllers: 3, "active executors": 12 },
      },
    ];
    const block = renderViewBlock(facts, "/bots/42");
    expect(block).toContain("do not treat it as something the user said");
    expect(block).toContain("Screen: Bot detail");
    expect(block).toContain('About: bot "backpack-mm-3" (id 42)');
    expect(block).toContain(
      "On screen: PNL $-412.30 · controllers 3 · active executors 12",
    );
    expect(block).toContain("URL: /bots/42");
  });

  it("drops empty onScreen values rather than rendering blanks", () => {
    const block = renderViewBlock(
      [{ label: "Bots", onScreen: { total: 3, filter: null, q: undefined, s: "" } }],
      "/bots",
    );
    expect(block).toContain("On screen: total 3");
    expect(block).not.toContain("filter");
  });

  it("renders one section per screen, merging contributors that share a label", () => {
    // What /trade does: the route table names the screen, the form contributes
    // what the user typed. Two `Screen:` blocks would read as two pages.
    const block = renderViewBlock(
      [
        { label: "Trade", onScreen: { side: "buy", amount: 500 } },
        {
          label: "Trade",
          subject: "a Grid Executor on binance SOL-USDC",
          onScreen: { amount: 750, "blocked by": "Start must be < end" },
        },
      ],
      "/trade",
    );
    expect(block.match(/^Screen: /gm)).toHaveLength(1);
    expect(block).toContain("About: a Grid Executor on binance SOL-USDC");
    // Later contributors win the fields they both name.
    expect(block).toContain("side buy");
    expect(block).toContain("amount 750");
    expect(block).not.toContain("amount 500");
    expect(block).toContain("blocked by Start must be < end");
  });

  it("keeps genuinely different screens apart", () => {
    const block = renderViewBlock(
      [{ label: "Routine report" }, { label: "Controller" }],
      "/routines",
    );
    expect(block.match(/^Screen: /gm)).toHaveLength(2);
  });

  it("caps the block and marks the cut", () => {
    const block = renderViewBlock(
      [{ label: "Bots", subject: "x".repeat(5000) }],
      "/bots",
    );
    expect(block.length).toBe(VIEW_BLOCK_MAX_CHARS);
    expect(block.endsWith("…")).toBe(true);
  });
});

describe("useViewFacts registry", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function Contributor({ facts }: { facts: ViewFacts | null }) {
    useViewFacts(() => facts);
    return null;
  }

  it("two overlapping registrations both contribute, outermost first", () => {
    act(() => {
      root.render(
        createElement(
          "div",
          null,
          createElement(Contributor, { facts: { label: "Routine report" } }),
          createElement(Contributor, { facts: { label: "Controller" } }),
        ),
      );
    });
    expect(collectViewFacts().map((f) => f.label)).toEqual([
      "Routine report",
      "Controller",
    ]);
  });

  it("unmounting the outer contributor leaves the inner one intact", () => {
    // Keyed, so the re-render genuinely unmounts "outer" rather than React
    // reusing its instance for the remaining child.
    act(() => {
      root.render(
        createElement(
          "div",
          null,
          createElement(Contributor, { key: "outer", facts: { label: "Outer" } }),
          createElement(Contributor, { key: "inner", facts: { label: "Inner" } }),
        ),
      );
    });
    act(() => {
      root.render(
        createElement(
          "div",
          null,
          createElement(Contributor, { key: "inner", facts: { label: "Inner" } }),
        ),
      );
    });
    expect(collectViewFacts().map((f) => f.label)).toEqual(["Inner"]);
  });

  it("a getter that throws degrades to no context", () => {
    function Broken() {
      useViewFacts(() => {
        throw new Error("boom");
      });
      return null;
    }
    act(() => {
      root.render(
        createElement(
          "div",
          null,
          createElement(Broken),
          createElement(Contributor, { facts: { label: "Still here" } }),
        ),
      );
    });
    expect(collectViewFacts().map((f) => f.label)).toEqual(["Still here"]);
  });

  it("a null contribution is simply absent", () => {
    act(() => {
      root.render(createElement(Contributor, { facts: null }));
    });
    expect(collectViewFacts()).toEqual([]);
  });
});
