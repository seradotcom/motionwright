import { describe, expect, it, vi } from "vitest";
import { decodeGraphMutationError, graphMutationFailure, settleGraphMutation,
  GRAPH_MUTATION_REJECTED_MESSAGE, GRAPH_MUTATION_UNKNOWN_MESSAGE } from "./semanticGraphMutationError";

const key = "a1234567-1234-4123-8123-123456789abc";
const other = "b1234567-1234-4123-8123-123456789abc";
const privateText = "MW_PRIVATE_GRAPH_BACKEND_SENTINEL /private/project.sqlite3";
const error = (known: boolean) => ({ schema: "motionwright.graph-mutation-error/1", request_id: key,
  outcome_known: known, code: "graph_mutation_failed" });

describe("closed original Graph mutation errors", () => {
  it("preserves the exact key and boolean while returning only the four closed fields", () => {
    for (const known of [false, true]) {
      const input = error(known), decoded = decodeGraphMutationError(input, key);
      expect(decoded).toEqual(input); expect(decoded).not.toBe(input);
      input.outcome_known = !known;
      expect(decoded?.outcome_known).toBe(known);
    }
  });

  const cases: [string, () => unknown][] = [
    ["null", () => null], ["array", () => [error(true)]], ["string", () => privateText],
    ["JSON text", () => JSON.stringify(error(true))], ["Error.message", () => new Error(privateText)],
    ["Error with wire-like properties", () => Object.assign(new Error(privateText), error(true))],
    ["wrong schema", () => ({ ...error(true), schema: "motionwright.graph-mutation-error/2" })],
    ["wrong code", () => ({ ...error(true), code: "known_success" })],
    ["foreign request", () => ({ ...error(true), request_id: other })],
    ["changed UUID spelling", () => ({ ...error(true), request_id: key.toUpperCase() })],
    ["nil request", () => ({ ...error(true), request_id: "00000000-0000-0000-0000-000000000000" })],
    ["non-UUID request", () => ({ ...error(true), request_id: "current" })],
    ["string knownness", () => ({ ...error(true), outcome_known: "true" })],
    ["numeric knownness", () => ({ ...error(true), outcome_known: 1 })],
    ["null knownness", () => ({ ...error(true), outcome_known: null })],
    ["missing knownness", () => ({ schema: error(true).schema, request_id: key, code: error(true).code })],
    ["extra diagnostic", () => ({ ...error(true), diagnostic: privateText })],
    ["extra symbol", () => ({ ...error(true), [Symbol("private")]: privateText })],
    ["inherited fields", () => Object.create(error(true))],
    ["throwing getter", () => Object.defineProperty(error(true), "outcome_known", {
      enumerable: true, get() { throw new Error(privateText); },
    })],
  ];
  it.each(cases)("retains UNKNOWN for %s without exposing its diagnostics", (_name, make) => {
    const input = make();
    expect(decodeGraphMutationError(input, key)).toBeNull();
    const failure = graphMutationFailure(input, key);
    expect(failure).toEqual({ outcome: "UNKNOWN", diagnostic: GRAPH_MUTATION_UNKNOWN_MESSAGE });
    expect(JSON.stringify(failure)).not.toContain("MW_PRIVATE_GRAPH_BACKEND_SENTINEL");
    expect(JSON.stringify(failure)).not.toContain("/private/");
  });

  it("does not promote a good error for a different or invalid original key", () => {
    for (const expected of [other, key.toUpperCase(), "", "00000000-0000-0000-0000-000000000000"])
      expect(graphMutationFailure(error(true), expected).outcome).toBe("UNKNOWN");
  });

  it.each([key + "\n", key + "\r\n", " " + key, key + " "])("rejects a padded key even when both identities match: %j", padded => {
    const input = { ...error(true), request_id: padded };
    expect(decodeGraphMutationError(input, padded)).toBeNull();
    expect(graphMutationFailure(input, padded)).toEqual({ outcome: "UNKNOWN", diagnostic: GRAPH_MUTATION_UNKNOWN_MESSAGE });
  });

  it("separates proven rejection from explicit uncertainty using fixed messages", () => {
    expect(graphMutationFailure(error(true), key)).toEqual({ outcome: "REJECTED", diagnostic: GRAPH_MUTATION_REJECTED_MESSAGE });
    expect(graphMutationFailure(error(false), key)).toEqual({ outcome: "UNKNOWN", diagnostic: GRAPH_MUTATION_UNKNOWN_MESSAGE });
  });

  it("classifies only the original Promise rejection and never processes an ACK for it", async () => {
    for (const known of [false, true]) {
      const receive = vi.fn();
      const result = await settleGraphMutation(Promise.reject(error(known)), key, receive);
      expect(result).toEqual({ ok: false, phase: "commit", failure: graphMutationFailure(error(known), key) });
      expect(receive).not.toHaveBeenCalled();
    }
  });

  it("does not interpret a resolved value as a rejection even when it resembles the wire error", async () => {
    const value = error(true), receive = vi.fn();
    expect(await settleGraphMutation(Promise.resolve(value), key, receive)).toEqual({ ok: true });
    expect(receive).toHaveBeenCalledTimes(1); expect(receive).toHaveBeenCalledWith(value);
  });

  it("a known-shaped failure during ACK validation or custody remains UNKNOWN", async () => {
    const receipt = { revision: 7 }, original = Promise.resolve(receipt);
    const receive = vi.fn(async (value: typeof receipt) => {
      expect(value).toBe(receipt);
      throw { ...error(true), diagnostic: privateText };
    });
    const result = await settleGraphMutation(original, key, receive);
    expect(result).toEqual({ ok: false, phase: "reply", failure: {
      outcome: "UNKNOWN", diagnostic: GRAPH_MUTATION_UNKNOWN_MESSAGE,
    } });
    expect(receive).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(result)).not.toContain("MW_PRIVATE_GRAPH_BACKEND_SENTINEL");
    // Even an otherwise valid same-key object cannot become a known rejection
    // when thrown after the original Promise has returned its Project.
    expect(await settleGraphMutation(original, key, () => { throw error(true); })).toEqual(result);
  });
});
