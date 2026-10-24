import { describe, expect, it } from "vitest";
import { isRecoveryOrigin, matchesRecoveryOrigin, recoveryOrigin } from "./recoveryOrigin";

const writer = "194d5b6b-6a44-4469-8d5a-9e6233357d0b";
describe("exact recovery-origin bytes", () => {
  it.each([
    ["", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"],
    ["abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"],
    ["abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq", "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"],
    ["a".repeat(1000000), "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"],
  ])("matches a published SHA-256 known-answer vector (%#)", (raw, expected) => {
    expect(recoveryOrigin(writer, raw)).toEqual({ writerId: writer, byteLength: raw.length, sha256: expected });
  });
  it.each([0, 1, 55, 56, 63, 64, 65, 119, 120, 127, 128, 129, 1024, 1048576])("matches Node native WebCrypto at %i bytes", async length => {
    const raw = "a".repeat(length), bytes = new TextEncoder().encode(raw);
    // Vitest's Node WebCrypto independently computes this digest, not the product helper.
    const native = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), b => b.toString(16).padStart(2, "0")).join("");
    expect(recoveryOrigin(writer, raw).sha256).toBe(native);
  });
  it.each(["mañana — 🎥", "\u0000\n\r\t", "\uFFFD", "é", "e\u0301", "𐀀".repeat(17)])("preserves exact Unicode and control bytes (%#)", async raw => {
    const bytes = new TextEncoder().encode(raw);
    const native = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), b => b.toString(16).padStart(2, "0")).join("");
    expect(recoveryOrigin(writer, raw)).toEqual({ writerId: writer, byteLength: bytes.length, sha256: native });
    expect(matchesRecoveryOrigin(recoveryOrigin(writer, raw), raw)).toBe(true);
  });
  it.each(["\uD800", "\uDC00", "x\uD800y", "\uDC00\uD800", "\uD800\uD800\uDC00"])("rejects unpaired UTF-16 instead of colliding with replacement bytes (%#)", raw => {
    expect(() => recoveryOrigin(writer, raw)).toThrow(/invalid/);
    expect(matchesRecoveryOrigin(recoveryOrigin(writer, "\uFFFD"), raw)).toBe(false);
  });
  it("does not normalize Unicode, whitespace, line endings or JSON representation", () => {
    for (const [a, b] of [["é", "e\u0301"], ["input", " input"], ["a\nb", "a\r\nb"], ['{"a":1}', '{ "a": 1 }']]) {
      expect(matchesRecoveryOrigin(recoveryOrigin(writer, a), b)).toBe(false);
    }
    const origin = recoveryOrigin(writer, '{"pending":"UNKNOWN"}');
    expect(matchesRecoveryOrigin(origin, null)).toBe(false);
    expect(matchesRecoveryOrigin({ ...origin, byteLength: origin.byteLength + 1 }, '{"pending":"UNKNOWN"}')).toBe(false);
  });
  it("rejects malformed and augmented anchor metadata", () => {
    const valid = recoveryOrigin(writer, "kept");
    expect(isRecoveryOrigin(valid)).toBe(true);
    for (const value of [null, [], {}, { ...valid, writerId: "foreign" }, { ...valid, byteLength: -1 },
      { ...valid, byteLength: 1.5 }, { ...valid, byteLength: Infinity }, { ...valid, sha256: valid.sha256.toUpperCase() },
      { ...valid, raw: "authority cannot come from an extra field" }, { writerId: writer, sha256: valid.sha256 }]) expect(isRecoveryOrigin(value)).toBe(false);
    expect(() => recoveryOrigin("foreign", "kept")).toThrow(/invalid/);
  });
});
