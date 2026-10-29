import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

const respond = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api", () => {
  it("sends the json option as a JSON body", async () => {
    const fetchMock = vi.fn(async () => respond(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api("/api/thing", { method: "POST", json: { name: "Food" } })).resolves.toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledWith("/api/thing", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Food" }),
    });
  });

  it("raises the server's error detail", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => respond(400, { detail: "That category already exists" })));

    await expect(api("/api/categories")).rejects.toThrow("That category already exists");
  });

  it("returns nothing for empty responses", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 204 })));

    await expect(api("/api/import/token/cancel", { method: "DELETE" })).resolves.toBeUndefined();
  });
});
