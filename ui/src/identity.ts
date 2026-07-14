/**
 * Flow identities are workspace-relative POSIX paths, while URLs have a
 * different set of delimiters. Encode each path segment exactly once so `/`
 * keeps its nesting meaning and reserved characters stay data.
 */
export function encodeIdentityPath(identity: string): string {
  return identity.split("/").map(encodeURIComponent).join("/");
}

const FLOW_ROUTE_PREFIX = "/flow/";

/** Decode a namespaced browser pathname into one raw flow identity. */
export function identityFromPath(pathname: string): string | null {
  if (!pathname.startsWith(FLOW_ROUTE_PREFIX)) return null;
  const encoded = pathname.slice(FLOW_ROUTE_PREFIX.length);
  if (encoded.length === 0) return null;

  try {
    const segments = encoded.split("/");
    if (segments.some((segment) => segment.length === 0)) return null;
    const decoded = segments.map(decodeURIComponent);
    // Encoded separators create multiple spellings for one identity. The
    // resolver owns lexical validation, but the browser route stays canonical.
    if (decoded.some((segment) => segment.includes("/") || segment.includes("\\"))) {
      return null;
    }
    return decoded.join("/");
  } catch {
    return null; // malformed percent escape
  }
}

export function flowPath(identity: string): string {
  return `${FLOW_ROUTE_PREFIX}${encodeIdentityPath(identity)}`;
}

export function apiIdentityPath(prefix: string, identity: string): string {
  return `${prefix.replace(/\/$/, "")}/${encodeIdentityPath(identity)}`;
}
