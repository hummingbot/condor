import { describe, expect, it } from "vitest";

import {
  YAML_NOT_A_MAPPING,
  parseYamlMapping,
  validateYamlMapping,
} from "@/lib/configYaml";

describe("validateYamlMapping", () => {
  it("accepts a mapping", () => {
    expect(validateYamlMapping("id: my-config\nspread: 0.001\n")).toBeNull();
  });

  it("accepts an empty mapping written as {}", () => {
    expect(validateYamlMapping("{}")).toBeNull();
  });

  it("rejects a YAML array", () => {
    expect(validateYamlMapping("- one\n- two\n")).toBe(YAML_NOT_A_MAPPING);
  });

  it("rejects null", () => {
    expect(validateYamlMapping("null")).toBe(YAML_NOT_A_MAPPING);
  });

  it("rejects empty input, which parses to undefined", () => {
    expect(validateYamlMapping("")).toBe(YAML_NOT_A_MAPPING);
  });

  it("rejects a bare scalar", () => {
    expect(validateYamlMapping("42")).toBe(YAML_NOT_A_MAPPING);
    expect(validateYamlMapping("just a string")).toBe(YAML_NOT_A_MAPPING);
  });

  it("reports malformed YAML with the first line of the parser message", () => {
    const error = validateYamlMapping("id: [1, 2\n");
    expect(error).toBeTruthy();
    expect(error).not.toBe(YAML_NOT_A_MAPPING);
    expect(error).not.toContain("\n");
  });

  it("gives every editor the same message for the same input", () => {
    expect(validateYamlMapping("- one")).toBe(validateYamlMapping("null"));
  });
});

describe("parseYamlMapping", () => {
  it("hands back the parsed mapping on success", () => {
    const result = parseYamlMapping("id: my-config\nnested:\n  a: 1\n");
    expect(result).toEqual({ ok: true, value: { id: "my-config", nested: { a: 1 } } });
  });

  it("never throws on malformed input", () => {
    const result = parseYamlMapping("id: [1, 2\n");
    expect(result.ok).toBe(false);
  });

  it("agrees with validateYamlMapping", () => {
    for (const text of ["id: x", "- one", "null", "42", "id: [1, 2"]) {
      const result = parseYamlMapping(text);
      expect(validateYamlMapping(text)).toBe(result.ok ? null : result.error);
    }
  });
});
