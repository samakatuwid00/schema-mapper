import { describe, expect, it } from "vitest";
import { AGENT_COMMANDS, filterCommands, PLACEHOLDER_RE } from "../agentCommands";

describe("agentCommands", () => {
  it("returns the full palette for an empty query", () => {
    expect(filterCommands("")).toHaveLength(AGENT_COMMANDS.length);
    expect(filterCommands("   ")).toHaveLength(AGENT_COMMANDS.length);
  });

  it("matches on template, hint, and keywords, case-insensitively", () => {
    expect(filterCommands("INSPECT").map((c) => c.template)).toContain(
      "inspect job <job_id>");
    // hint match: "queue" is only in check status' hint/keywords
    expect(filterCommands("queue").map((c) => c.template)).toContain(
      "check status");
    // keyword match: "unique" only appears as a keyword
    expect(filterCommands("unique").map((c) => c.template)).toContain(
      "diagnose duplicate key for <entity>: <error text>");
  });

  it("returns nothing for a non-matching query", () => {
    expect(filterCommands("zzzznope")).toHaveLength(0);
  });

  it("every command's first placeholder is detectable for auto-select", () => {
    const withPlaceholders = AGENT_COMMANDS.filter((c) =>
      PLACEHOLDER_RE.test(c.template));
    // sanity: at least the id-bearing commands carry a <placeholder>
    expect(withPlaceholders.length).toBeGreaterThan(5);
    for (const cmd of withPlaceholders) {
      expect(cmd.template.match(PLACEHOLDER_RE)?.index).toBeGreaterThanOrEqual(0);
    }
  });
});
