/**
 * The home is the conversation, and the fleet overview is a page.
 *
 * FEAT-104 mounted an overview under `/?view=fleet` and then made it what a
 * bare `/` means. Both are undone: `/` is the chat it has been since FEAT-077,
 * `/fleet` is the overview, and the only thing left of the query parameter is a
 * redirect for the URLs already in bookmarks and notification payloads. That
 * redirect is the case worth a test — it has to keep the *rest* of the query
 * string, because a URL that reached the old overview could carry anything
 * beside `view`, and dropping it would land somebody on a plausible wrong page.
 *
 * The two routes are wired here exactly as `App.tsx` wires them, since the
 * forwarding is only true if something is listening at the other end. Both
 * bodies are stubbed: what is under test is the routing, not either page.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Agents } from "./Agents";

vi.mock("@/pages/tabs/AgentChatTab", () => ({
  AgentChatTab: () => <div data-stub="chat" />,
}));

function FleetStub() {
  const { pathname, search } = useLocation();
  return <div data-stub="fleet" data-at={pathname + search} />;
}

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

async function at(url: string) {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/" element={<Agents />} />
          <Route path="/fleet" element={<FleetStub />} />
        </Routes>
      </MemoryRouter>,
    );
  });
  return container.querySelector("[data-stub]");
}

const stub = async (url: string) => (await at(url))?.getAttribute("data-stub");

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
  it("is the conversation, with no parameters and with any", async () => {
    expect(await stub("/")).toBe("chat");
    expect(await stub("/?view=chat")).toBe("chat");
    // `?agent=` and `?ask=` (FEAT-092) come from the agent workspace and from
    // notification payloads; `?conversation=` (FEAT-111) comes from its Runs
    // rail. Every one of them lands where it always meant to.
    expect(await stub("/?agent=brigado")).toBe("chat");
    expect(await stub("/?agent=brigado&ask=how%20are%20we%20doing")).toBe(
      "chat",
    );
    expect(await stub("/?conversation=7f3a")).toBe("chat");
    // `?view=now` is the agent workspace's grammar on the wrong path. Landing
    // on the home beats landing on an error page.
    expect(await stub("/?view=now")).toBe("chat");
  });

  it("forwards the overview's old address to its page", async () => {
    const el = await at("/?view=fleet");
    expect(el?.getAttribute("data-stub")).toBe("fleet");
    expect(el?.getAttribute("data-at")).toBe("/fleet");
  });

  it("forwards it with everything that was riding along", async () => {
    const el = await at("/?view=fleet&server=brigado&agent=quiet");
    expect(el?.getAttribute("data-stub")).toBe("fleet");
    expect(el?.getAttribute("data-at")).toBe("/fleet?server=brigado&agent=quiet");
  });
});

describe("the fleet overview", () => {
  it("has an address of its own", async () => {
    expect(await stub("/fleet")).toBe("fleet");
  });
});
