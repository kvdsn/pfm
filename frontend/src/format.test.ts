import { describe, expect, it } from "vitest";
import { shortDate } from "./format";

describe("shortDate", () => {
  it("shows day and month", () => expect(shortDate("2026-06-12")).toBe("12/06"));
  it("adds the two-digit year when asked", () => expect(shortDate("2026-06-12", true)).toBe("12/06/26"));
  it("leaves text that is not an ISO date alone", () => expect(shortDate("soon")).toBe("soon"));
});
