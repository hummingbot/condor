import { describe, expect, it } from "vitest";

import { pickFor, sessionAgentKey } from "./sessionAgentKey";

const DEFAULT = "claude-default";

describe("sessionAgentKey", () => {
  it("keeps a pick made on Condor's hero out of a specialist spawn", () => {
    const pending = { slug: "", key: "gpt-picked" };
    expect(sessionAgentKey(pending, "brigado", DEFAULT)).toBe("");
    expect(sessionAgentKey(pending, "", DEFAULT)).toBe("gpt-picked");
  });

  it("sends a specialist's pick only to that specialist", () => {
    const pending = { slug: "brigado", key: "gpt-picked" };
    expect(sessionAgentKey(pending, "brigado", DEFAULT)).toBe("gpt-picked");
    expect(sessionAgentKey(pending, "", DEFAULT)).toBe(DEFAULT);
    expect(sessionAgentKey(pending, "orca", DEFAULT)).toBe("");
  });

  it("falls back to the default for Condor and to the agent's own model otherwise", () => {
    expect(sessionAgentKey(null, "", DEFAULT)).toBe(DEFAULT);
    expect(sessionAgentKey(null, "brigado", DEFAULT)).toBe("");
  });

  it("is not consumed: a second Condor spawn still carries the pick", () => {
    const pending = { slug: "", key: "gpt-picked" };
    expect(sessionAgentKey(pending, "", DEFAULT)).toBe("gpt-picked");
    expect(sessionAgentKey(pending, "", DEFAULT)).toBe("gpt-picked");
  });

  it("pickFor shows a pick only on the agent it was made for", () => {
    const pending = { slug: "brigado", key: "gpt-picked" };
    expect(pickFor(pending, "brigado")).toBe("gpt-picked");
    expect(pickFor(pending, "")).toBeNull();
    expect(pickFor(null, "brigado")).toBeNull();
  });
});
