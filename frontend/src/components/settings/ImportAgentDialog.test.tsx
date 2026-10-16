/**
 * The review is the sandbox, so the dialog has to actually show it (FEAT-133).
 *
 * An imported routine runs inside the Condor process with no isolation. What
 * stands between a bundle and that is this dialog: it previews before it
 * writes, it names every executable file, and it refuses to let a collision
 * through. These tests assert the requests that cross the wire rather than the
 * arguments handed to `api.*` — the promise is about what the server is asked
 * to do, and a preview that quietly posted to the apply endpoint would read
 * identically at the call site.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { AgentImportPlan } from "@/lib/api";
import { ImportAgentDialog } from "./ImportAgentDialog";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const PLAN: AgentImportPlan = {
  slug: "traveller",
  source_slug: "traveller",
  name: "Traveller",
  description: "a traveller",
  when_to_consult: "when something must move",
  exported_at: "2026-10-15T09:00:00Z",
  schema_version: 1,
  files: [{ path: "AGENT.md", bytes: 240 }],
  executables: [
    { path: "routines/r.py", bytes: 120 },
    { path: "controllers/c/c.py", bytes: 60 },
  ],
  total_bytes: 420,
  stamps: { created_by: "7", server_name: "" },
  mutes: { routines: ["r"], skills: ["s"] },
  agent_key: "claude-code",
  agent_key_inherited: false,
  collision: "",
  suggested_slug: "",
  refusals: [],
};

let container: HTMLDivElement;
let root: Root;
let posts: { url: string; asSlug: string }[] = [];
let reply: AgentImportPlan = PLAN;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  posts = [];
  reply = PLAN;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const body = init?.body as FormData;
      posts.push({ url, asSlug: String(body.get("as_slug") ?? "") });
      return new Response(JSON.stringify(reply), {
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

async function render(onImported = () => {}) {
  await act(async () => {
    root.render(<ImportAgentDialog onImported={onImported} />);
  });
}

/** jsdom has no file picker, so the chosen file is placed on the input. */
async function choose() {
  const input = container.querySelector("input[type=file]") as HTMLInputElement;
  const file = new File(["{}"], "traveller.condor-agent.json", {
    type: "application/json",
  });
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  await act(async () => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function button(label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find((b) =>
    b.textContent?.includes(label),
  );
  if (!found) throw new Error(`no button matching ${label}`);
  return found as HTMLButtonElement;
}

it("previews before it writes, and names every executable", async () => {
  await render();
  await choose();

  expect(posts).toEqual([
    { url: "/api/v1/agents/import/preview", asSlug: "" },
  ]);
  const listed = [...container.querySelectorAll("[data-executable]")].map((n) =>
    n.textContent?.trim(),
  );
  expect(listed).toHaveLength(2);
  expect(listed[0]).toContain("routines/r.py");
  expect(listed[1]).toContain("controllers/c/c.py");
  expect(container.textContent).toContain("no sandbox");
});

it("only posts to the apply endpoint when the operator confirms", async () => {
  const imported = vi.fn();
  await render(imported);
  await choose();

  await act(async () => {
    button("Import traveller").click();
  });

  expect(posts.map((p) => p.url)).toEqual([
    "/api/v1/agents/import/preview",
    "/api/v1/agents/import",
  ]);
  expect(imported).toHaveBeenCalledOnce();
});

it("refuses to apply a plan the server already refused", async () => {
  reply = {
    ...PLAN,
    refusals: ["'routines/r.py' does not match its sha256"],
  };
  await render();
  await choose();

  expect(container.querySelector("[data-refusal]")?.textContent).toContain(
    "sha256",
  );
  expect(button("Import traveller").disabled).toBe(true);
});

it("offers a free name for a collision and re-previews under it", async () => {
  reply = { ...PLAN, collision: "traveller", suggested_slug: "traveller_2" };
  await render();
  await choose();

  expect(button("Import traveller").disabled).toBe(true);

  await act(async () => {
    button("Check name").click();
  });

  expect(posts.at(-1)).toEqual({
    url: "/api/v1/agents/import/preview",
    asSlug: "traveller_2",
  });
});
