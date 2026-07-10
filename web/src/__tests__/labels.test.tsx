import { describe, expect, it } from "vitest";
import { DESCRIPTIONS, LABELS, label } from "../labels";

describe("labels glossary", () => {
  it("translates internal terms to manager vocabulary", () => {
    expect(label("migrations")).toBe("Database Updates (SQL)");
    expect(label("backfill")).toBe("Copy existing rows");
    expect(label("deploy")).toBe("Start syncing");
    expect(label("proposal")).toBe("Column match");
    expect(label("entity")).toBe("Table");
  });

  it("falls back to the input unchanged for unknown terms", () => {
    expect(label("unknown_thing")).toBe("unknown_thing");
    expect(label("")).toBe("");
  });

  it("provides a plain-English description for every labelled term", () => {
    for (const key of Object.keys(LABELS)) {
      expect(DESCRIPTIONS[key], `missing DESCRIPTIONS entry for "${key}"`).toBeTruthy();
    }
  });

  it("has the specific descriptions called out in the spec", () => {
    expect(DESCRIPTIONS.migrations).toBe(
      "Applies SQL files that change the database structure. This does not move any row data.",
    );
    expect(DESCRIPTIONS.backfill).toBe(
      "Copies rows that already exist in the source into the sync queue.",
    );
  });
});
