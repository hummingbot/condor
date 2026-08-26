/**
 * What a blank credential field means when editing a server (SEC-229).
 *
 * The edit form opens with username and password blank and both placeholders
 * promising "(unchanged)", so the user is told not to retype them. The backend
 * can only honour that through *absence*: `modify_server` applies each field
 * under `if x is not None` (config_manager.py:342), and `""` is not None — so a
 * form that posts itself whole writes empty basic-auth credentials into
 * config.yml on a host-only edit, for the owner and for every trader the server
 * is shared with, and the next Hummingbot API call 401s.
 *
 * These tests therefore assert the literal JSON body of the PUT rather than the
 * arguments handed to `api.updateServer`: the defect lived in what crossed the
 * wire, and a key that is present-but-empty is indistinguishable from a key
 * that is absent unless you look at the request itself. The api layer is real
 * here for the same reason; only `fetch` is stubbed.
 *
 * The other half of the promise is that nothing has to come *back* for the
 * secret to survive — the fixture serves no credentials at all, and a passing
 * edit proves the browser never needed them.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ServerInfo } from "@/lib/api";
import { ServersSettings } from "./ServersSettings";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

interface Captured {
  url: string;
  method: string;
  body: Record<string, unknown>;
}

let container: HTMLDivElement;
let root: Root;
let sent: Captured[] = [];

/** The list endpoint never returns username/password — nor does this fixture. */
const SERVERS: ServerInfo[] = [
  {
    name: "prod",
    host: "192.168.1.100",
    port: 8000,
    online: true,
    permission: "owner",
    is_default: true,
  },
];

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  sent = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (method !== "GET") {
        sent.push({
          url: path,
          method,
          body: JSON.parse((init?.body as string) ?? "{}"),
        });
        return new Response(JSON.stringify({ updated: true, added: true }), {
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify(SERVERS), {
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

async function render() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <ServersSettings />
      </QueryClientProvider>,
    );
  });
  // The list query resolves a tick after mount; until then the component is a
  // spinner with no buttons to click.
  await waitFor(() => container.querySelector("form, button") !== null);
}

async function waitFor(done: () => boolean) {
  for (let i = 0; i < 50 && !done(); i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
  if (!done()) throw new Error("condition never became true");
}

function type(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )!.set!;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function button(title: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find(
    (b) => b.getAttribute("title") === title || b.textContent?.trim() === title,
  );
  if (!found) throw new Error(`no button ${title}`);
  return found as HTMLButtonElement;
}

/** In edit mode the name field is hidden, so the form's inputs are 4. */
function formInputs(): HTMLInputElement[] {
  return [...container.querySelectorAll("form input")] as HTMLInputElement[];
}

async function openEdit() {
  await render();
  await act(async () => button("Edit").click());
}

/** The submit button reads "Save" when editing and "Add" when adding. */
async function submit() {
  const btn = container.querySelector("form button[type=submit]") as HTMLButtonElement;
  await act(async () => btn.click());
  await waitFor(() => sent.length > 0);
}

describe("editing a server", () => {
  it("omits username and password entirely when they are left blank", async () => {
    await openEdit();
    const [host] = formInputs();
    await act(async () => type(host, "10.0.0.5"));
    await submit();

    expect(sent).toHaveLength(1);
    const [put] = sent;
    expect(put.method).toBe("PUT");
    expect(put.url).toBe("/api/v1/settings/servers/prod");
    // Absence, not emptiness: `"username" in body` is the whole defect.
    expect(Object.keys(put.body).sort()).toEqual(["host", "port"]);
    expect(put.body.host).toBe("10.0.0.5");
    expect(put.body.port).toBe(8000);
  });

  it("sends a retyped password, and still no username", async () => {
    await openEdit();
    const inputs = formInputs();
    await act(async () => type(inputs[3], "s3cret"));
    await submit();

    expect(sent).toHaveLength(1);
    expect(sent[0].body.password).toBe("s3cret");
    expect("username" in sent[0].body).toBe(false);
  });

  it("sends a retyped username on its own", async () => {
    await openEdit();
    const inputs = formInputs();
    await act(async () => type(inputs[2], "operator"));
    await submit();

    expect(sent[0].body.username).toBe("operator");
    expect("password" in sent[0].body).toBe(false);
  });

  it("tells the user both credentials are unchanged, so blank is deliberate", async () => {
    await openEdit();
    const inputs = formInputs();
    expect(inputs[2].placeholder).toBe("(unchanged)");
    expect(inputs[3].placeholder).toBe("(unchanged)");
    expect(inputs[2].value).toBe("");
    expect(inputs[3].value).toBe("");
  });
});

describe("adding a server", () => {
  it("still posts all five fields, blank credentials included", async () => {
    await render();
    await act(async () => button("Add Server").click());
    const inputs = formInputs();
    await act(async () => type(inputs[0], "staging"));
    await act(async () => type(inputs[1], "10.0.0.9"));
    await submit();

    // The add form has nothing stored to preserve, so the partial-update
    // contract does not apply — POST stays whole.
    expect(sent).toHaveLength(1);
    expect(sent[0].method).toBe("POST");
    expect(Object.keys(sent[0].body).sort()).toEqual([
      "host",
      "name",
      "password",
      "port",
      "username",
    ]);
  });
});
