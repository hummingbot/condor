/**
 * @vitest-environment jsdom
 */
/**
 * The remembered section, which is what makes the agent panel re-open on the
 * one it was closed on rather than on Brain.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  KNOWLEDGE_TABS,
  isKnowledgeTab,
  lastKnowledgeTab,
  rememberKnowledgeTab,
  toKnowledgeTab,
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
    rememberKnowledgeTab("loops");
    expect(localStorage.getItem(KNOWLEDGE_TAB_KEY)).toBe("loops");
    expect(lastKnowledgeTab()).toBe("loops");
  });

  it("is the last one written, not the first", () => {
    rememberKnowledgeTab("loops");
    rememberKnowledgeTab("memories");
    expect(lastKnowledgeTab()).toBe("memories");
  });

  it("ignores a value that names no section", () => {
    localStorage.setItem(KNOWLEDGE_TAB_KEY, "nonsense");
    expect(isKnowledgeTab("nonsense")).toBe(false);
    expect(lastKnowledgeTab()).toBeUndefined();
  });

  it("knows Controllers, between Routines and Activity (FEAT-127)", () => {
    expect(isKnowledgeTab("controllers")).toBe(true);
    expect(KNOWLEDGE_TABS.indexOf("controllers")).toBe(
      KNOWLEDGE_TABS.indexOf("routines") + 1,
    );
    expect(KNOWLEDGE_TABS.indexOf("activity")).toBe(
      KNOWLEDGE_TABS.indexOf("controllers") + 1,
    );
  });

  it("reads a remembered Strategies from before the rename as Loops (FEAT-128)", () => {
    localStorage.setItem(KNOWLEDGE_TAB_KEY, "strategies");
    expect(lastKnowledgeTab()).toBe("loops");
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

describe("a section off a URL (FEAT-128)", () => {
  it("is a current id as spelled", () => {
    expect(toKnowledgeTab("loops")).toBe("loops");
    expect(toKnowledgeTab("brain")).toBe("brain");
  });

  it("reads the retired strategies id as loops", () => {
    expect(isKnowledgeTab("strategies")).toBe(false);
    expect(toKnowledgeTab("strategies")).toBe("loops");
  });

  it("is nothing for a value that names no section", () => {
    expect(toKnowledgeTab("nonsense")).toBeUndefined();
    expect(toKnowledgeTab("constructor")).toBeUndefined();
    expect(toKnowledgeTab(null)).toBeUndefined();
    expect(toKnowledgeTab("")).toBeUndefined();
  });
});
