/**
 * The strategy-config editor says what the save reached, and refuses what the
 * route would silently not do (FEAT-142).
 *
 * Three ways an editor over `PUT /{slug}/strategies/{sslug}/config` misleads an
 * operator, and this file holds each one shut:
 *
 * 1. Reporting `updated: true` and nothing else. The route merges into the
 *    *stored* config and reaches no running engine, so a save on a live loop
 *    changes nothing the agent is currently trading under. The sentence that
 *    says so is the route's, and it is printed verbatim.
 * 2. Accepting a deleted line as a removed setting. The merge keeps a key the
 *    body omits, so the one edit the editor cannot make must be refused rather
 *    than performed-and-lost.
 * 3. Swallowing a gate's 403 — `server_name` and the risk limits decide what
 *    the next session may do with money, and a Save that appears to do nothing
 *    reads as a click that did not register.
 *
 * `CodeEditor` is substituted by a textarea: CodeMirror is a real editor widget
 * needing layout jsdom does not have, and nothing here is about CodeMirror.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { configToYaml, parseYamlMapping } from "@/lib/configYaml";

vi.mock("@/lib/api", () => ({
  api: { updateStrategyConfig: vi.fn() },
}));

vi.mock("@/components/editor/CodeEditor", () => ({
  CodeEditor: ({
    value,
    onChange,
  }: {
    value: string;
    onChange?: (next: string) => void;
  }) => (
    <textarea
      data-yaml
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
}));

const { StrategyConfigEditor } = await import("./StrategyConfigEditor");
const { api } = await import("@/lib/api");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

/** A config with the nested risk block and a key no build of the card names. */
const CONFIG: Record<string, unknown> = {
  execution_mode: "loop",
  frequency_sec: 3600,
  server_name: "brigado_2",
  bot_name: "",
  total_amount_quote: 100,
  risk_limits: {
    max_position_size_quote: 500,
    max_open_executors: 5,
    max_drawdown_pct: -1,
  },
  sizing_hint: "aggressive",
};

const SAVED = {
  updated: true,
  config: CONFIG,
  applies_to_running: false,
  live_engines: 2,
  message: "Saved; restart 2 sessions to apply.",
};

/** One client per test, so a re-render is a re-render and not a remount. */
let client: QueryClient;

function render(config: Record<string, unknown> = CONFIG) {
  act(() => {
    root.render(
      <QueryClientProvider client={client}>
        <StrategyConfigEditor slug="brigado" sslug="pmm_king" config={config} />
      </QueryClientProvider>,
    );
  });
}

const yamlBox = () => container.querySelector<HTMLTextAreaElement>("[data-yaml]")!;
const saveButton = () =>
  [...container.querySelectorAll("button")].find((b) =>
    /^Sav/.test(b.textContent?.trim() ?? ""),
  )!;

/** Replace the buffer the way typing into the editor does. */
function type(text: string) {
  const box = yamlBox();
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    "value",
  )!.set!;
  act(() => {
    setter.call(box, text);
    box.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

/** Let the mutation settle. */
async function settle() {
  for (let i = 0; i < 5; i++) {
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
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  vi.mocked(api.updateStrategyConfig).mockReset();
  vi.mocked(api.updateStrategyConfig).mockResolvedValue(SAVED);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("StrategyConfigEditor", () => {
  it("seeds the buffer with a round-trip of the stored config", () => {
    render();
    const parsed = parseYamlMapping(yamlBox().value);
    expect(parsed.ok && parsed.value).toEqual(CONFIG);
  });

  it("offers nothing to save until the buffer differs from the file", () => {
    render();
    expect(saveButton().disabled).toBe(true);
  });

  it("posts the parsed mapping and prints the route's own sentence verbatim", async () => {
    render();
    type(configToYaml({ ...CONFIG, frequency_sec: 60 }));
    expect(saveButton().disabled).toBe(false);

    await act(async () => saveButton().click());
    await settle();

    expect(api.updateStrategyConfig).toHaveBeenCalledWith("brigado", "pmm_king", {
      ...CONFIG,
      frequency_sec: 60,
    });
    // Not a message of this component's own: only the route knows how many
    // running sessions still hold the config this just replaced.
    expect(container.querySelector("[data-config-saved]")?.textContent).toBe(
      "Saved; restart 2 sessions to apply.",
    );
  });

  it("prints the no-session wording just as literally", async () => {
    vi.mocked(api.updateStrategyConfig).mockResolvedValue({
      ...SAVED,
      live_engines: 0,
      message: "Saved. No session is running, so the next one starts on it.",
    });
    render();
    type(configToYaml({ ...CONFIG, frequency_sec: 60 }));
    await act(async () => saveButton().click());
    await settle();

    expect(container.querySelector("[data-config-saved]")?.textContent).toBe(
      "Saved. No session is running, so the next one starts on it.",
    );
  });

  it("will not send an unparseable buffer, and says why", () => {
    render();
    type("frequency_sec: 60\n  bad: indent\n");

    expect(container.querySelector("[data-config-blocked]")?.textContent).toContain(
      "bad indentation",
    );
    expect(saveButton().disabled).toBe(true);
    act(() => saveButton().click());
    expect(api.updateStrategyConfig).not.toHaveBeenCalled();
  });

  it("will not send a mapping-less buffer either", () => {
    render();
    type("- just\n- a list\n");

    expect(container.querySelector("[data-config-blocked]")?.textContent).toContain(
      "YAML must be a mapping",
    );
    expect(saveButton().disabled).toBe(true);
  });

  it("refuses a buffer that dropped keys, naming them, before any request", () => {
    render();
    const rest = { ...CONFIG };
    delete rest.server_name;
    delete rest.risk_limits;
    type(configToYaml(rest));

    const refusal = container.querySelector("[data-config-blocked]")?.textContent ?? "";
    expect(refusal).toContain("server_name");
    expect(refusal).toContain("risk_limits");
    // The reason, not just the complaint: the merge would keep them, so the
    // operator has to write the off value rather than delete the line.
    expect(refusal).toContain("the save merges");
    expect(saveButton().disabled).toBe(true);
    act(() => saveButton().click());
    expect(api.updateStrategyConfig).not.toHaveBeenCalled();
  });

  it("lets a key be emptied, which is the edit the refusal points at", async () => {
    render();
    type(configToYaml({ ...CONFIG, server_name: "" }));

    expect(container.querySelector("[data-config-blocked]")).toBeNull();
    await act(async () => saveButton().click());
    await settle();
    expect(api.updateStrategyConfig).toHaveBeenCalledWith("brigado", "pmm_king", {
      ...CONFIG,
      server_name: "",
    });
  });

  it("prints a gate's refusal instead of a save that looks like it worked", async () => {
    vi.mocked(api.updateStrategyConfig).mockRejectedValue(
      new Error("You have no access to server brigado_9"),
    );
    render();
    type(configToYaml({ ...CONFIG, server_name: "brigado_9" }));
    await act(async () => saveButton().click());
    await settle();

    expect(container.querySelector("[data-config-error]")?.textContent).toContain(
      "You have no access to server brigado_9",
    );
    expect(container.querySelector("[data-config-saved]")).toBeNull();
  });

  it("keeps the verdict through the very refetch the save asked for", async () => {
    render();
    const next = { ...CONFIG, frequency_sec: 60 };
    type(configToYaml(next));
    await act(async () => saveButton().click());
    await settle();

    // The save invalidates `["strategy", slug, sslug]`, so a fresh `config`
    // lands moments later and the buffer re-seeds from it. The sentence the
    // operator needs must not vanish on the refetch it asked for.
    render(next);
    expect(container.querySelector("[data-config-saved]")?.textContent).toBe(
      "Saved; restart 2 sessions to apply.",
    );
  });

  it("retires the verdict once the buffer is edited again", async () => {
    render();
    type(configToYaml({ ...CONFIG, frequency_sec: 60 }));
    await act(async () => saveButton().click());
    await settle();
    expect(container.querySelector("[data-config-saved]")).not.toBeNull();

    type(configToYaml({ ...CONFIG, frequency_sec: 120 }));
    expect(container.querySelector("[data-config-saved]")).toBeNull();
  });

  it("keeps unsaved edits across a refetch that changed nothing", () => {
    render();
    type(configToYaml({ ...CONFIG, frequency_sec: 60 }));
    // The strategy query re-creates `config` on every poll. Keyed on the dumped
    // string rather than the object, so an identical config is not a re-seed.
    render({ ...CONFIG });
    expect(yamlBox().value).toContain("frequency_sec: 60");
  });

  it("re-seeds when the stored config actually changed", () => {
    render();
    type(configToYaml({ ...CONFIG, frequency_sec: 60 }));
    render({ ...CONFIG, frequency_sec: 900 });
    expect(yamlBox().value).toContain("frequency_sec: 900");
    expect(saveButton().disabled).toBe(true);
  });
});
