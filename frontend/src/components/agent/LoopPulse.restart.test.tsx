/**
 * "Does this loop come back after Condor restarts?" — asked and answered on the
 * loop's own spine.
 *
 * The reader this exists for restarted Condor, came back, and found a strategy
 * that had ticked fourteen times reading STOPPED. The machinery to resume it
 * was already in the supervisor; the product had no way to say yes. So the
 * chip has to do two things, and both are tested here: state the answer *in
 * words* (a glyph you must hover to decode is the same failure in a smaller
 * font), and let you change it in one click from wherever the loop is on
 * screen.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LoopPulse } from "./LoopPulse";

let host: HTMLDivElement;
let root: Root;

async function render(node: React.ReactNode) {
  await act(async () => {
    root.render(node);
  });
}

/** The chip, found by the role it claims rather than by its position. */
function chip(): HTMLButtonElement | null {
  return host.querySelector<HTMLButtonElement>('button[role="switch"]');
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("the restart chip", () => {
  it("says what happens on a restart, in words", async () => {
    await render(
      <LoopPulse
        instance={null}
        status="idle"
        config={{}}
        onSetRestartOnBoot={() => {}}
      />,
    );
    // Off is the default, and the reader is told what off *means* rather than
    // being shown an unlit icon to interpret.
    expect(host.textContent).toContain("stops on restart");
    expect(chip()?.getAttribute("aria-checked")).toBe("false");
  });

  it("says the other thing when the loop is armed", async () => {
    await render(
      <LoopPulse
        instance={null}
        status="idle"
        config={{ restart_on_boot: true }}
        onSetRestartOnBoot={() => {}}
      />,
    );
    expect(host.textContent).toContain("resumes on restart");
    expect(chip()?.getAttribute("aria-checked")).toBe("true");
  });

  it("flips to the opposite of what is stored", async () => {
    const calls: boolean[] = [];
    await render(
      <LoopPulse
        instance={null}
        status="idle"
        config={{ restart_on_boot: false }}
        onSetRestartOnBoot={(enabled) => calls.push(enabled)}
      />,
    );
    await act(async () => {
      chip()?.click();
    });
    expect(calls).toEqual([true]);
  });

  it("is a plain label, not a button, for a host that cannot write", async () => {
    // A read-only host still gets the fact. Offering a control that silently
    // does nothing would be worse than stating the answer.
    await render(
      <LoopPulse instance={null} status="idle" config={{ restart_on_boot: true }} />,
    );
    expect(host.textContent).toContain("resumes on restart");
    expect(chip()).toBeNull();
  });

  it("says it is saving rather than showing the old answer", async () => {
    // The write is a round trip to disk plus every live engine's status file.
    // Until it lands, claiming either state would be a guess.
    await render(
      <LoopPulse
        instance={null}
        status="idle"
        config={{ restart_on_boot: false }}
        onSetRestartOnBoot={() => {}}
        settingRestartOnBoot
      />,
    );
    expect(host.textContent).toContain("saving…");
    expect(chip()?.disabled).toBe(true);
  });

  it("does not swallow clicks while the write is in flight", async () => {
    const calls: boolean[] = [];
    await render(
      <LoopPulse
        instance={null}
        status="idle"
        config={{ restart_on_boot: false }}
        onSetRestartOnBoot={(enabled) => calls.push(enabled)}
        settingRestartOnBoot
      />,
    );
    await act(async () => {
      chip()?.click();
    });
    // Disabled means the second click is not a second write racing the first.
    expect(calls).toEqual([]);
  });
});

describe("the rest of the pulse", () => {
  it("still reads the same with the chip beside it", async () => {
    // The chip joined a row that is scanned far more often than it is — the
    // cadence and the tick count keep their place at the front of it.
    await render(
      <LoopPulse
        instance={null}
        status="idle"
        config={{ frequency_sec: 3600 }}
        onSetRestartOnBoot={() => {}}
      />,
    );
    expect(host.textContent).toContain("every 1h 00m");
    expect(host.textContent).toContain("no ticks yet");
  });
});

describe("what a screen reader is told", () => {
  it("is a switch with a state, not an unlabelled button", async () => {
    await render(
      <LoopPulse
        instance={null}
        status="idle"
        config={{ restart_on_boot: true }}
        onSetRestartOnBoot={() => {}}
      />,
    );
    const button = chip();
    expect(button?.getAttribute("role")).toBe("switch");
    expect(button?.getAttribute("aria-checked")).toBe("true");
    expect(button?.getAttribute("title")).toContain("fresh session");
  });
});

// A guard against the regression this whole file is about: the old code read
// the flag from nowhere, so no config could turn it on.
it("reads the answer off the strategy's stored config", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  for (const [stored, expected] of [
    [true, "resumes on restart"],
    [false, "stops on restart"],
    [undefined, "stops on restart"],
  ] as const) {
    await render(
      <LoopPulse
        instance={null}
        status="idle"
        config={stored === undefined ? {} : { restart_on_boot: stored }}
        onSetRestartOnBoot={() => {}}
      />,
    );
    expect(host.textContent).toContain(expected);
  }
});
