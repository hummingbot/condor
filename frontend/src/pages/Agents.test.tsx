/**
 * The home is the conversation, and every fleet address lands back on it.
 *
 * FEAT-104 mounted an overview under `/?view=fleet` and then made it what a
 * bare `/` means; FEAT-114 retired the page that replaced it, because what
 * every agent is doing belongs beside the conversation rather than a tab away
 * from it. So both spellings are now bookmarks pointing at a screen that is no
 * longer separate, and what this file pins is that neither 404s and neither
 * hijacks the home: `?view=` on `/` means nothing again — [[FEAT-117]] is about
 * to reuse it — and `/fleet` redirects into the panel with the desk named, so
 * the reader arrives at the fleet rather than at whatever their browser last
 * had open.
 *
 * The routes are wired here exactly as `App.tsx` wires them, since a redirect
 * is only true if something is listening at the other end. Both bodies are
 * stubbed: what is under test is the routing, not either screen.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  MemoryRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EXECUTION_PATH } from "@/components/chat/accountPanels";
import { Agents } from "./Agents";

vi.mock("@/pages/tabs/AgentChatTab", () => ({
  AgentChatTab: () => <div data-stub="chat" />,
}));

function Where() {
  const { pathname, search } = useLocation();
  return <div data-stub="home" data-at={pathname + search} />;
}

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

async function at(url: string) {
  // A fresh root per URL: `MemoryRouter` reads `initialEntries` once, so
  // re-rendering into the same root would keep answering for the first URL.
  await act(() => root.unmount());
  container.remove();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route
            path="/"
            element={
              <>
                <Agents />
                <Where />
              </>
            }
          />
          <Route path="/fleet" element={<Navigate to={EXECUTION_PATH} replace />} />
        </Routes>
      </MemoryRouter>,
    );
  });
  return container;
}

const stub = async (url: string) =>
  (await at(url)).querySelector("[data-stub='chat']") ? "chat" : null;
const landed = async (url: string) =>
  (await at(url)).querySelector("[data-stub='home']")?.getAttribute("data-at");

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

  it("no longer forwards the overview's old spelling anywhere", async () => {
    // The page it forwarded to is gone, and `?view=` on `/` is free again.
    // Staying put with the parameter untouched is what makes it reusable.
    expect(await stub("/?view=fleet")).toBe("chat");
    expect(await landed("/?view=fleet")).toBe("/?view=fleet");
    expect(await landed("/?view=fleet&server=brigado")).toBe(
      "/?view=fleet&server=brigado",
    );
  });
});

describe("/fleet", () => {
  it("lands on the home with the Execution panel open", async () => {
    expect(await stub("/fleet")).toBe("chat");
    // Both halves matter: `panel=desk` is what puts the desk in the pane, and
    // `desk=execution` is what makes it the fleet rather than the portfolio.
    const where = await landed("/fleet");
    expect(where).toContain("panel=desk");
    expect(where).toContain("desk=execution");
  });
});
