/**
 * @vitest-environment jsdom
 */
/**
 * The remembered section, which is what makes the agent panel re-open on the
 * one it was closed on rather than on Brain.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  isKnowledgeTab,
  lastKnowledgeTab,
  rememberKnowledgeTab,
} from "./knowledgeTabs";
import { KNOWLEDGE_TAB_KEY } from "@/lib/sessionState";

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("the remembered section", () => {
  it("is nothing for a browser that has never read one", () => {
    expect(lastKnowledgeTab()).toBeUndefined();
  });

  it("comes back as the section that was written", () => {
    rememberKnowledgeTab("strategies");
    expect(localStorage.getItem(KNOWLEDGE_TAB_KEY)).toBe("strategies");
    expect(lastKnowledgeTab()).toBe("strategies");
  });

  it("is the last one written, not the first", () => {
    rememberKnowledgeTab("strategies");
    rememberKnowledgeTab("memories");
    expect(lastKnowledgeTab()).toBe("memories");
  });

  it("ignores a value that names no section", () => {
    localStorage.setItem(KNOWLEDGE_TAB_KEY, "nonsense");
    expect(isKnowledgeTab("nonsense")).toBe(false);
    expect(lastKnowledgeTab()).toBeUndefined();
  });

  it("survives storage that will not be read or written", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    expect(() => rememberKnowledgeTab("tools")).not.toThrow();
    expect(lastKnowledgeTab()).toBeUndefined();
  });
});
