export function joinOperatorNotices(parts: string[]): string | null {
  const unique = [...new Set(parts.filter((part) => part !== ""))];
  return unique.length > 0 ? unique.join(" · ") : null;
}
