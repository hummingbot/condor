/**
 * The review is the sandbox, so the dialog has to actually show it (FEAT-133,
 * FEAT-144).
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

import type {
  AgentBundleFinding,
  AgentImportPlan,
  AgentUpdatePlan,
} from "@/lib/api";
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
  agent_key_wanted: "",
  agent_key_gap: "",
  collision: "",
  suggested_slug: "",
  collision_agent_uid: "",
  is_update: false,
  refusals: [],
  agent_uid: "9f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f",
  version: 3,
  notes: "",
  author: { name: "", handle: "" },
  requires: {
    connectors: ["bitget_perpetual"],
    tools: [],
    agent_key: "claude-code",
    skills: { carried: ["s"], inherited: [] },
  },
  unmet: [],
  findings: [],
};

/** One of every FEAT-144 category, in the order the server ranks them. */
const FINDINGS: AgentBundleFinding[] = [
  {
    category: "secret",
    label: "mnemonic",
    path: "skills/s/SKILL.md",
    line: 9,
    start: 140,
    end: 215,
    certain: true,
    context: "",
  },
  {
    category: "endpoint",
    label: "public",
    path: "routines/r.py",
    line: 4,
    start: 60,
    end: 80,
    certain: false,
    context: "",
  },
  {
    category: "code_run",
    label: "exec",
    path: "routines/r.py",
    line: 7,
    start: 150,
    end: 155,
    certain: false,
    context: "",
  },
  {
    category: "tool_steer",
    label: "create_grid_executor",
    path: "skills/s/SKILL.md",
    line: 3,
    start: 40,
    end: 60,
    certain: false,
    context: "When the band is breached, then call `create_grid_executor`.",
  },
  {
    category: "risk_limits",
    label: "max_position_size_quote",
    path: "loops/l/loop.md",
    line: 12,
    start: 180,
    end: 211,
    certain: false,
    context: "999999",
  },
];

/** The diff of a newer bundle of the agent already installed (FEAT-145). */
const UPDATE: AgentUpdatePlan = {
  slug: "traveller",
  agent_uid: "9f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f",
  installed_agent_uid: "9f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f",
  version: 3,
  installed_version: 2,
  exported_at: "2026-10-15T09:00:00Z",
  notes: "",
  author: { name: "", handle: "" },
  requires: {
    connectors: [],
    tools: [],
    agent_key: "claude-code",
    skills: { carried: [], inherited: [] },
  },
  unmet: [],
  findings: [],
  files: [
    { path: "AGENT.md", verdict: "unchanged", bytes: 240, executable: false, incoming: "" },
    { path: "routines/r.py", verdict: "changed", bytes: 130, executable: true, incoming: "" },
    { path: "routines/new.py", verdict: "added", bytes: 90, executable: true, incoming: "" },
    {
      path: "skills/s/SKILL.md",
      verdict: "conflict",
      bytes: 70,
      executable: false,
      incoming: "---\nname: s\n---\n\nUpstream's copy.\n",
    },
    { path: "shutdown.md", verdict: "removed", bytes: 0, executable: false, incoming: "" },
  ],
  counts: { unchanged: 1, changed: 1, added: 1, conflict: 1, removed: 1 },
  mutes: { routines: ["new"], skills: [] },
  blockers: [],
  warnings: [],
};

/** The models this install can start, as the chat's model picker lists them. */
const SESSION_OPTIONS = {
  agents: [
    { key: "claude-code", label: "Claude Code", ready: true },
    { key: "codex", label: "Codex", ready: true },
    { key: "copilot", label: "GitHub Copilot", ready: false },
    { key: "openrouter:", label: "OpenRouter…", picker: true },
  ],
  custom_providers: [],
  default_agent: "claude-code",
  servers: [],
  agent_bindings: [],
};

let container: HTMLDivElement;
let root: Root;
let posts: { url: string; asSlug: string }[] = [];
/** The `agent_key` each import post carried, when it carried one. */
let agentKeys: string[] = [];
let reply: AgentImportPlan = PLAN;
let updateReply: AgentUpdatePlan = UPDATE;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  posts = [];
  agentKeys = [];
  reply = PLAN;
  updateReply = UPDATE;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("/sessions/options")) {
        return new Response(JSON.stringify(SESSION_OPTIONS), {
          headers: { "Content-Type": "application/json" },
        });
      }
      const body = init?.body as FormData;
      if (body.get("agent_key")) agentKeys.push(String(body.get("agent_key")));
      posts.push({ url, asSlug: String(body.get("as_slug") ?? "") });
      // The update pair answers a different shape, and the dialog must not be
      // able to render one as the other.
      const payload = url.includes("/update") ? updateReply : reply;
      return new Response(JSON.stringify(payload), {
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

it("shows the review grouped by category, in the server's order", async () => {
  reply = { ...PLAN, findings: FINDINGS };
  await render();
  await choose();

  const groups = [...container.querySelectorAll("[data-finding-group]")].map((n) =>
    n.getAttribute("data-finding-group"),
  );
  expect(groups).toEqual([
    "secret",
    "endpoint",
    "code_run",
    "tool_steer",
    "risk_limits",
  ]);
  expect(container.querySelector("[data-findings]")?.textContent).toContain(
    "5 things to look at",
  );
  // Best-effort, and the copy says so rather than implying a guarantee.
  expect(container.textContent).toContain("It warns; it does not prevent.");
});

it("locates each finding and shows the context for the two that carry it", async () => {
  reply = { ...PLAN, findings: FINDINGS };
  await render();
  await choose();

  const rows = [...container.querySelectorAll("[data-finding]")].map((n) =>
    n.textContent?.trim(),
  );
  expect(rows[0]).toContain("mnemonic (certain)");
  expect(rows[0]).toContain("skills/s/SKILL.md:9");
  // `tool_steer` is context, not an accusation, so the sentence is on screen.
  expect(rows[3]).toContain(
    "When the band is breached, then call `create_grid_executor`.",
  );
  // A limit is a judgement the operator makes: the figure, and no verdict.
  expect(rows[4]).toContain("max_position_size_quote");
  expect(rows[4]).toContain("999999");
});

it("never blocks the import on a finding, not even a certain secret", async () => {
  reply = { ...PLAN, findings: FINDINGS };
  await render();
  await choose();

  expect(button("Import traveller").disabled).toBe(false);

  await act(async () => {
    button("Import traveller").click();
  });

  expect(posts.at(-1)?.url).toBe("/api/v1/agents/import");
});

it("says nothing about a review that found nothing", async () => {
  await render();
  await choose();

  expect(container.querySelector("[data-findings]")).toBeNull();
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

it("imports under the name that was reviewed, not the empty box", async () => {
  // Accepting the suggestion is pressing Check name with the box still empty:
  // the name came off the placeholder. Submitting the box sent "", so the
  // server planned the colliding name again and answered 409 while the button
  // read "Import traveller_2".
  reply = { ...PLAN, collision: "traveller", suggested_slug: "traveller_2" };
  await render();
  await choose();

  reply = { ...PLAN, slug: "traveller_2", collision: "", suggested_slug: "" };
  await act(async () => {
    button("Check name").click();
  });
  expect(button("Import traveller_2").disabled).toBe(false);

  await act(async () => {
    button("Import traveller_2").click();
  });

  expect(posts.at(-1)).toEqual({
    url: "/api/v1/agents/import",
    asSlug: "traveller_2",
  });
});

it("shows the newest bundle's plan when an older preview answers late", async () => {
  // Two bundles chosen in a row: the plan on screen must belong to the one
  // Import would write, or the dialog reviews A and lands B.
  const slow = { ...PLAN, slug: "first", name: "First" };
  const fast = { ...PLAN, slug: "second", name: "Second" };
  // Held on an object: a bare `let` assigned only inside the callback below is
  // narrowed to `never` by the time it is called.
  const gate: { release?: () => void } = {};
  let call = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const body = init?.body as FormData;
      posts.push({ url, asSlug: String(body.get("as_slug") ?? "") });
      const which = call++ === 0 ? slow : fast;
      if (which === slow) {
        await new Promise<void>((resolve) => {
          gate.release = resolve;
        });
      }
      return new Response(JSON.stringify(which), {
        headers: { "Content-Type": "application/json" },
      });
    }),
  );

  await render();
  const input = container.querySelector("input[type=file]") as HTMLInputElement;
  const place = (name: string) => {
    const file = new File(["{}"], name, { type: "application/json" });
    Object.defineProperty(input, "files", { value: [file], configurable: true });
  };

  // Dispatched outside `act` on purpose: the first request must still be in
  // flight when the second is made, and two overlapping `act` scopes
  // interleave.
  place("first.condor-agent.json");
  input.dispatchEvent(new Event("change", { bubbles: true }));
  place("second.condor-agent.json");
  await act(async () => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(button("Import second")).toBeTruthy();

  gate.release?.();
  await act(async () => {
    await Promise.resolve();
  });

  // The late answer for the first bundle is dropped, not rendered.
  expect(button("Import second")).toBeTruthy();
  expect(container.textContent).not.toContain("First");
});

it("names the identity and the version, which survive a rename", async () => {
  await render();
  await choose();

  expect(container.querySelector("[data-agent-uid]")?.textContent).toContain(
    "9f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f",
  );
  expect(container.textContent).toContain("Version 3");
});

it("labels the author as a claim nobody verified", async () => {
  // A handle rendered next to a download without this label reads as
  // provenance, and Condor verifies none of it.
  reply = { ...PLAN, author: { name: "Fede", handle: "@condor-official" } };
  await render();
  await choose();

  const block = container.querySelector("[data-bundle-author]")?.textContent;
  expect(block).toContain("unverified claim");
  expect(block).toContain("Fede");
  expect(block).toContain("@condor-official");
});

it("says nothing about an author when the bundle claims none", async () => {
  await render();
  await choose();

  expect(container.querySelector("[data-bundle-author]")).toBeNull();
});

it("lists what this install cannot give the agent, without blocking import", async () => {
  reply = {
    ...PLAN,
    notes: "Widened the grid.",
    unmet: [
      {
        kind: "connector",
        name: "bitget_perpetual",
        have: ["binance"],
        blocking: true,
      },
      { kind: "tool", name: "invented_tool", have: [], blocking: false },
    ],
  };
  await render();
  await choose();

  const rows = [...container.querySelectorAll("[data-unmet]")].map((n) =>
    n.textContent?.replace(/\s+/g, " ").trim(),
  );
  expect(rows).toEqual([
    "connector bitget_perpetual — you have binance",
    "tool invented_tool",
  ]);
  expect(container.querySelector("[data-bundle-notes]")?.textContent).toContain(
    "Widened the grid.",
  );
  expect(button("Import traveller").disabled).toBe(false);
});

// ── A collision that is really an update (FEAT-145) ──

/** The import preview of a newer bundle of the agent already installed. */
const SAME_AGENT: AgentImportPlan = {
  ...PLAN,
  collision: "traveller",
  suggested_slug: "traveller_2",
  collision_agent_uid: "9f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f",
  is_update: true,
};

it("offers the update verb when the collision is the same agent, newer", async () => {
  reply = SAME_AGENT;
  await render();
  await choose();

  const offer = container.querySelector("[data-update-offer]")?.textContent;
  expect(offer).toContain("newer version");
  expect(offer).toContain("keeps its memory");
  // The rename stays available underneath rather than being replaced by it.
  expect(button("Check name")).toBeTruthy();
});

it("offers only a rename when the collision is a different agent", async () => {
  reply = { ...SAME_AGENT, collision_agent_uid: "0".repeat(32), is_update: false };
  await render();
  await choose();

  expect(container.querySelector("[data-update-offer]")).toBeNull();
  expect(container.textContent).toContain("Import never replaces one");
});

it("previews the update before it writes, and names what keeps your copy", async () => {
  reply = SAME_AGENT;
  await render();
  await choose();

  await act(async () => {
    button("Review the changes").click();
  });

  expect(posts.at(-1)?.url).toBe("/api/v1/agents/traveller/update/preview");
  const rows = [...container.querySelectorAll("[data-update-file]")].map((n) => ({
    verdict: n.getAttribute("data-update-file"),
    text: n.textContent ?? "",
  }));
  // Conflicts first: they are the only rows the operator has to decide about.
  expect(rows[0].verdict).toBe("conflict");
  expect(rows[0].text).toContain("skills/s/SKILL.md");
  expect(rows[0].text).toContain("your copy kept");
  expect(rows.map((r) => r.verdict)).toEqual([
    "conflict",
    "changed",
    "added",
    "removed",
    "unchanged",
  ]);
  expect(container.querySelector("[data-update-plan]")?.textContent).toContain(
    "version 2 → 3",
  );
  expect(container.textContent).toContain("Muted on arrival: new");
});

it("only posts to the update endpoint when the operator confirms", async () => {
  reply = SAME_AGENT;
  const imported = vi.fn();
  await render(imported);
  await choose();

  await act(async () => {
    button("Review the changes").click();
  });
  expect(imported).not.toHaveBeenCalled();

  updateReply = { ...UPDATE, updated: true, next_step: "Nothing was started." };
  await act(async () => {
    button("Update traveller").click();
  });

  expect(posts.map((p) => p.url)).toEqual([
    "/api/v1/agents/import/preview",
    "/api/v1/agents/traveller/update/preview",
    "/api/v1/agents/traveller/update",
  ]);
  expect(imported).toHaveBeenCalledOnce();
  expect(container.querySelector("[data-update-done]")?.textContent).toContain(
    "Updated traveller to version 3",
  );
});

it("shows a blocker instead of an Update button", async () => {
  reply = SAME_AGENT;
  updateReply = {
    ...UPDATE,
    blockers: [
      { code: "live_loop", message: "'traveller' has a loop running right now." },
    ],
  };
  await render();
  await choose();

  await act(async () => {
    button("Review the changes").click();
  });

  expect(
    container.querySelector("[data-update-blocker=live_loop]")?.textContent,
  ).toContain("loop running right now");
  expect(container.querySelector("[data-update-file]")).toBeNull();
  expect(() => button("Update traveller")).toThrow();
});

it("shows a different build of the same version as a warning, with the diff", async () => {
  reply = SAME_AGENT;
  updateReply = {
    ...UPDATE,
    warnings: [
      {
        code: "same_version_new_build",
        message: "This bundle says version 3, the same as the one installed.",
      },
    ],
  };
  await render();
  await choose();

  await act(async () => {
    button("Review the changes").click();
  });

  expect(
    container.querySelector("[data-update-warning=same_version_new_build]")
      ?.textContent,
  ).toContain("same as the one installed");
  expect(container.querySelectorAll("[data-update-file]").length).toBe(5);
  expect(button("Update traveller").disabled).toBe(false);
});

it("says when the agent's own model can't run here, and installs on the one picked", async () => {
  reply = {
    ...PLAN,
    agent_key: "claude-code",
    agent_key_inherited: true,
    agent_key_wanted: "openrouter:deepseek/deepseek-v4.1-flash",
    agent_key_gap: "needs OPENROUTER_API_KEY in .env",
  };
  await render();
  await choose();

  const choice = container.querySelector("[data-model-choice]");
  expect(choice?.textContent).toContain("openrouter:deepseek/deepseek-v4.1-flash");
  expect(choice?.textContent).toContain("needs OPENROUTER_API_KEY in .env");
  const select = container.querySelector("select[aria-label=Model]") as HTMLSelectElement;
  // Only what this install can start: no unready bridge, no picker sentinel.
  expect([...select.options].map((o) => o.value)).toEqual(["claude-code", "codex"]);
  expect(select.value).toBe("claude-code");

  await act(async () => {
    select.value = "codex";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await act(async () => {
    button("Import traveller").click();
  });

  expect(agentKeys).toEqual(["codex"]);
});

it("sends no model when the agent's own runs here", async () => {
  await render();
  await choose();
  expect(container.querySelector("[data-model-choice]")).toBeNull();

  await act(async () => {
    button("Import traveller").click();
  });
  expect(agentKeys).toEqual([]);
});
