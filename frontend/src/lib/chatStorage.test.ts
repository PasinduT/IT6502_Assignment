import { beforeEach, describe, expect, it } from "vitest";
import { createSession, loadSessions, saveSessions, titleFromQuestion } from "./chatStorage";

describe("chat storage", () => {
  beforeEach(() => localStorage.clear());

  it("round trips sessions", () => {
    const session = createSession();
    saveSessions([session]);
    expect(loadSessions()).toEqual([session]);
  });

  it("recovers from malformed data", () => {
    localStorage.setItem("lk-tax-assistant:sessions:v1", "not-json");
    expect(loadSessions()).toEqual([]);
  });

  it("creates a bounded title", () => {
    expect(titleFromQuestion("x".repeat(60))).toHaveLength(43);
  });
});

