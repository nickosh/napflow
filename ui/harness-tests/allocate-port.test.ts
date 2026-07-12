import { createServer } from "node:net";

import { describe, expect, it } from "vitest";

import { allocateLoopbackPort } from "../e2e/allocate-port";

describe("Playwright port allocation", () => {
  it("preserves an explicit debugging override", () => {
    expect(allocateLoopbackPort("49123")).toBe("49123");
  });

  it("returns an OS-selected port that is immediately bindable", async () => {
    const port = Number(allocateLoopbackPort());
    expect(port).toBeGreaterThan(0);
    expect(port).toBeLessThanOrEqual(65_535);

    const server = createServer();
    await new Promise<void>((resolve, reject) => {
      server.once("error", reject);
      server.listen({ host: "127.0.0.1", port, exclusive: true }, resolve);
    });
    await new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
  });
});
