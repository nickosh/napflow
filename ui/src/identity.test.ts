import { describe, expect, it } from "vitest";

import {
  apiIdentityPath,
  encodeIdentityPath,
  flowPath,
  identityFromPath,
} from "./identity";

describe("flow identity URL transport", () => {
  it("round-trips nested identities and reserved characters per segment", () => {
    const identity = "flows/team space/hash#percent%query?/子";
    const encoded =
      "flows/team%20space/hash%23percent%25query%3F/%E5%AD%90";

    expect(encodeIdentityPath(identity)).toBe(encoded);
    expect(flowPath(identity)).toBe(`/flow/${encoded}`);
    expect(apiIdentityPath("/api/flows", identity)).toBe(
      `/api/flows/${encoded}`,
    );
    expect(identityFromPath(`/flow/${encoded}`)).toBe(identity);
  });

  it("decodes once, preserving percent-shaped filename text", () => {
    const identity = "flows/%2e%2e/value%23";
    const path = flowPath(identity);

    expect(path).toBe("/flow/flows/%252e%252e/value%2523");
    expect(identityFromPath(path)).toBe(identity);
  });

  it("namespaces identities that would collide with server routes", () => {
    for (const identity of ["api/workspace", "assets/index.js"]) {
      const path = flowPath(identity);
      expect(path).toBe(`/flow/${identity}`);
      expect(identityFromPath(path)).toBe(identity);
    }
    expect(identityFromPath("/api/workspace")).toBeNull();
    expect(identityFromPath("/assets/index.js")).toBeNull();
  });

  it("rejects empty, malformed, and encoded-separator browser routes", () => {
    expect(identityFromPath("/")).toBeNull();
    expect(identityFromPath("/flow")).toBeNull();
    expect(identityFromPath("/flow/")).toBeNull();
    expect(identityFromPath("/flows/child")).toBeNull();
    expect(identityFromPath("/flow/flows//child")).toBeNull();
    expect(identityFromPath("/flow/flows/bad%ZZ")).toBeNull();
    expect(identityFromPath("/flow/flows/a%2Fb")).toBeNull();
    expect(identityFromPath("/flow/flows/a%5Cb")).toBeNull();
  });
});
