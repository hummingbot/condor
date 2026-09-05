/**
 * The openers on an empty chat, once an agent has learned something.
 *
 * The rule this pins is the whole of the user-visible feature: a learned row
 * outranks the static list, and a user with nothing learned sees precisely
 * what they saw before the feature existed. Both halves matter — the second is
 * what makes shipping this safe for everyone who never talks to an agent twice.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Wallet } from "lucide-react";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Starter } from "@/components/chat/Starters";

const getAgentStarters = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getAgentStarters: (...args: unknown[]) => getAgentStarters(...args),
  },
  CHAT_SLUG: "condor",
}));

const { useStarters, starterFromRow, mergeStarters } =
  await import("./useStarters");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const STATIC: Starter[] = [
  { icon: Wallet, title: "How is my portfolio doing?", hint: "Balances" },
  { icon: Wallet, title: "What are my bots doing?", hint: "Controllers" },
  { icon: Wallet, title: "Any positions at risk?", hint: "Exposure" },
];

function row(title: string, extra: Record<string, string> = {}) {
  return {
    title,
    hint: `hint for ${title}`,
    prompt: title,
    icon: "",
    skill: "",
    ...extra,
  };
}

let container: HTMLDivElement;
let root: Root;
let seen: Starter[] = [];

function Probe({ slug }: { slug: string }) {
  const starters = useStarters(slug, STATIC);
  // Published from an effect rather than during render: assigning to an outer
  // binding while rendering is a side effect, and the lint gate says so.
  useEffect(() => {
    seen = starters;
  }, [starters]);
  return null;
}

async function render(slug = "market_making_expert") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <Probe slug={slug} />
      </QueryClientProvider>,
    );
  });
  // Let the query settle: react-query resolves off the microtask queue and
  // then re-renders, so one flush is not reliably enough.
  for (let i = 0; i < 10; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  seen = [];
  getAgentStarters.mockReset();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("mergeStarters", () => {
  it("puts learned rows above the defaults and caps the row at three", () => {
    const learned = [starterFromRow(row("Rebalance my SOL-USDC range"))];

    const merged = mergeStarters(learned, STATIC);

    expect(merged.map((s) => s.title)).toEqual([
      "Rebalance my SOL-USDC range",
      "How is my portfolio doing?",
      "What are my bots doing?",
    ]);
  });

  it("drops a default a learned row already covers", () => {
    const learned = [starterFromRow(row("how is my PORTFOLIO doing?"))];

    expect(mergeStarters(learned, STATIC).map((s) => s.title)).toEqual([
      "how is my PORTFOLIO doing?",
      "What are my bots doing?",
      "Any positions at risk?",
    ]);
  });

  it("leaves the defaults untouched when nothing was learned", () => {
    expect(mergeStarters([], STATIC)).toEqual(STATIC);
  });

  it("retires the defaults once three things are learned", () => {
    const learned = ["a", "b", "c"].map((t) => starterFromRow(row(t)));

    expect(mergeStarters(learned, STATIC).map((s) => s.title)).toEqual([
      "a",
      "b",
      "c",
    ]);
  });
});

describe("starterFromRow", () => {
  it("sends the prompt verbatim on click", () => {
    const starter = starterFromRow(row("Rebalance my range"));

    expect(starter.prompt).toBe("Rebalance my range");
    expect(starter.hint).toBe("hint for Rebalance my range");
  });

  it("falls back to the title when the row carries no prompt", () => {
    expect(starterFromRow(row("Rebalance", { prompt: "" })).prompt).toBe(
      "Rebalance",
    );
  });

  it("gives an unknown icon keyword a glyph rather than nothing", () => {
    expect(starterFromRow(row("x", { icon: "unicorn" })).icon).toBeTruthy();
    expect(starterFromRow(row("x", { icon: "lp" })).icon).not.toBe(
      starterFromRow(row("x", { icon: "unicorn" })).icon,
    );
  });
});

describe("useStarters", () => {
  it("shows the learned opener first", async () => {
    getAgentStarters.mockResolvedValue({
      starters: [row("Rebalance my SOL-USDC range", { icon: "lp" })],
    });

    await render();

    expect(getAgentStarters).toHaveBeenCalledWith("market_making_expert");
    expect(seen[0].title).toBe("Rebalance my SOL-USDC range");
    expect(seen).toHaveLength(3);
  });

  it("leaves today's chips exactly as they are when nothing is learned", async () => {
    getAgentStarters.mockResolvedValue({ starters: [] });

    await render();

    expect(seen).toEqual(STATIC);
  });

  it("falls back to the defaults when the fetch fails", async () => {
    getAgentStarters.mockRejectedValue(new Error("offline"));

    await render();

    expect(seen).toEqual(STATIC);
  });

  it("asks for the default agent when nothing is bound", async () => {
    getAgentStarters.mockResolvedValue({ starters: [] });

    await render("");

    expect(getAgentStarters).toHaveBeenCalledWith("condor");
  });
});
