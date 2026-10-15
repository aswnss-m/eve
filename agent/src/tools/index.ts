import { memoryTools } from "../memory.js";
import { brainTools } from "./brain.js";
import type { ToolContext } from "./common.js";
import { entityTools } from "./entities.js";
import { salesTools } from "./sales.js";
import { utilityTools } from "./utility.js";

export function buildTools(ctx: ToolContext) {
  return {
    ...entityTools(ctx),
    ...salesTools(ctx),
    ...brainTools(ctx),
    ...utilityTools(ctx),
    ...memoryTools(ctx),
  };
}
