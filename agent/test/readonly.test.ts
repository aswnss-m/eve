import { describe, expect, it } from "vitest";
import { assertReadOnly, WriteQueryError } from "../src/graph.js";

describe("assertReadOnly", () => {
  it.each(["MATCH (n) RETURN n", "MATCH (n {status: 'SET'}) RETURN n.name", "CALL db.labels()"])("allows %s", (q) => {
    expect(() => assertReadOnly(q)).not.toThrow();
  });
  it.each([
    "CREATE (n)",
    "MATCH (n) SET n.x = 1",
    "match (n) detach delete n",
    "MERGE (a:A)",
    "MATCH (n) REMOVE n.x",
    "CALL db.idx.fulltext.createNodeIndex('A', 'x')",
  ])("rejects %s", (q) => {
    expect(() => assertReadOnly(q)).toThrow(WriteQueryError);
  });
});
