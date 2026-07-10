import { describe, expect, it } from "vitest";
import { NAV_GROUPS } from "../App";

/**
 * App.tsx needs the auth + query providers to render its Shell, so per the
 * task we assert on the exported NAV_GROUPS data structure instead of mounting
 * the whole tree.
 */
describe("sidebar navigation", () => {
  const titles = NAV_GROUPS.map((g) => g.title);
  const labels = NAV_GROUPS.flatMap((g) => g.items.map((i) => i.label));

  it("has the three manager-facing groups", () => {
    expect(titles).toEqual(["Monitor", "Set up", "Maintain"]);
  });

  it("labels the migrations entry in manager vocabulary", () => {
    expect(labels).toContain("Database Updates (SQL)");
    // The bare internal word must not surface as a nav label.
    expect(labels).not.toContain("Migrations");
  });

  it("keeps every nav item pointing at a route with an icon", () => {
    for (const group of NAV_GROUPS) {
      for (const item of group.items) {
        expect(item.to.startsWith("/")).toBe(true);
        expect(item.icon).toBeTruthy();
      }
    }
  });
});
