export interface PortableImportSourceRefusal {
  kind: "portable_import_source_changed";
  fingerprint: string;
}

export function isPortableImportSourceRefusal(
  value: unknown, fingerprint: unknown,
): value is PortableImportSourceRefusal {
  if (typeof fingerprint !== "string" || !/^[0-9a-f]{64}$/.test(fingerprint)
    || typeof value !== "object" || value === null || Array.isArray(value)) return false;

  const refusal = value as Record<string, unknown>;
  return Object.keys(refusal).length === 2 && Object.hasOwn(refusal, "kind")
    && Object.hasOwn(refusal, "fingerprint") && refusal.kind === "portable_import_source_changed"
    && refusal.fingerprint === fingerprint;
}
