import { describe, expect, it } from "vitest";
import { sign } from "../src/auth.js";

// Same vector as eve_agent/tests/test_agent_api.py: both sides must sign runtime_config requests identically.
describe("sign", () => {
  it("matches the Python implementation", () => {
    expect(sign("secret", "1700000000", "eve.local")).toBe(
      "9243f6aeff033821d4f244ec4da7a8d6efbfda85bb878d2ff2bc772618aaa655",
    );
  });
});
