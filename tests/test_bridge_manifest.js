import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const bridgeDir = path.resolve(here, "../integrations/dsh-pet-bridge");

test("bridge loads with its dependency in a standalone-style package directory", async () => {
  const bridge = await import(pathToFileURL(path.join(bridgeDir, "index.js")));
  assert.equal(typeof bridge.apply, "function");
  // apiProxy is optional on released DSH builds and is resolved lazily. Making
  // it a hard injection dependency prevents the bridge from activating in
  // environments where that service is absent.
  assert.deepEqual(bridge.inject, ["llm", "agentDefaultModel"]);
});
