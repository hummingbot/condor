/**
 * Which of the home's two views a URL lands on.
 *
 * The default is the point. FEAT-104 mounted a fleet overview under `/`, built
 * it, and in step 3 made it what a bare `/` means — the conversation had been
 * that since FEAT-077, and every link, notification and reflex in this product
 * meant it. Which is why the cases that matter here are the ones that did *not*
 * move: a URL carrying the chat's own parameters still lands on the chat.
 *
 * Both bodies are stubbed: what is under test is the switch, not either page.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Agents } from "./Agents";

vi.mock("@/pages/tabs/AgentChatTab", () => ({
  AgentChatTab: () => <div data-stub="chat" />,
}));

vi.mock("@/components/agent/workspace/FleetOverview", () => ({
  FleetOverview: () => <div data-stub="fleet" />,
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

async function at(url: string) {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[url]}>
        <Agents />
      </MemoryRouter>,
    );
  });
  return container.querySelector("[data-stub]")?.getAttribute("data-stub");
}

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

describe("the home", () => {
  it("is the overview with no parameters — the flip, and deliberately so", async () => {
    expect(await at("/")).toBe("fleet");
  });

  it("is the chat under the parameters the chat's own links carry", async () => {
    // `?agent=` and `?ask=` (FEAT-092) come from the agent workspace and from
    // notification payloads; `?conversation=` (FEAT-111) comes from its Runs
    // rail. None of them may land on anything but the chat, and the flip is
    // exactly what would have broken that.
    expect(await at("/?agent=brigado")).toBe("chat");
    expect(await at("/?agent=brigado&ask=how%20are%20we%20doing")).toBe("chat");
    expect(await at("/?conversation=7f3a")).toBe("chat");
  });

  it("is the chat when the chat is asked for by name", async () => {
    expect(await at("/?view=chat")).toBe("chat");
  });

  it("is the overview when the URL asks for it by name", async () => {
    expect(await at("/?view=fleet")).toBe("fleet");
  });

  it("is the default for a view it does not own", async () => {
    // `?view=now` is the agent workspace's grammar on the wrong path.
    expect(await at("/?view=now")).toBe("fleet");
  });
});
