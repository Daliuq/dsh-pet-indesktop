import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import {
  apply,
  BRIDGE_CAPABILITIES,
  BRIDGE_EVENT_INVENTORY,
  BRIDGE_PROTOCOL_VERSION,
  BRIDGE_VERSION,
  __bridgeTest,
} from "../index.js";

const source = fs.readFileSync(new URL("../index.js", import.meta.url), "utf8");
const packageJson = JSON.parse(fs.readFileSync(new URL("../package.json", import.meta.url), "utf8"));

function setValues(name) {
  const bodyMatch = source.match(new RegExp(`const ${name} = new Set\\(\\[([\\s\\S]*?)\\]\\);`));
  if (!bodyMatch) return [];
  // Restrict extraction to entries occupying their own source line, so quoted
  // examples in comments do not look like producer events.
  return [...bodyMatch[1].matchAll(/^\\s*"([^"\\n]+)"\\s*,?\\s*$/gm)].map((match) => match[1]);
}

test("the exported inventory covers every literal and dynamic producer event", () => {
  const inventory = new Set(BRIDGE_EVENT_INVENTORY);
  const literalEvents = [...source.matchAll(/\bevent:\s*"([^"]+)"/g)].map((match) => match[1]);
  for (const event of literalEvents) assert.ok(inventory.has(event), `missing literal event: ${event}`);
  for (const name of ["STATE_EVENT_TYPES", "WATCHDOG_EVENT_TYPES"]) {
    for (const event of setValues(name)) assert.ok(inventory.has(event), `missing ${name} event: ${event}`);
  }
  assert.equal(inventory.size, BRIDGE_EVENT_INVENTORY.length, "inventory must not contain duplicates");
  assert.ok(inventory.has("AgentStatus"), "default writeRecord event is part of the contract");
  assert.ok(inventory.has("bridge/hello"), "the handshake event is part of the contract");
});

test("the runtime bridge version follows package metadata", () => {
  assert.equal(BRIDGE_PROTOCOL_VERSION, 1);
  assert.equal(BRIDGE_VERSION, packageJson.version);
  assert.ok(BRIDGE_CAPABILITIES.length > 0);
  assert.equal(new Set(BRIDGE_CAPABILITIES).size, BRIDGE_CAPABILITIES.length);
});

test("every writeRecord output carries the protocol and package envelope", () => {
  const oldAppData = process.env.APPDATA;
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "dsh-bridge-contract-"));
  process.env.APPDATA = tempRoot;
  try {
    __bridgeTest.writeRecord({ event: "contract/test", bridgeProtocolVersion: 999, bridgeVersion: "stale" });
    __bridgeTest.flush();
    const file = path.join(tempRoot, "dsh-pet-bridge", __bridgeTest.instanceFile);
    const record = JSON.parse(fs.readFileSync(file, "utf8").trim());
    assert.equal(record.event, "contract/test");
    assert.equal(record.bridgeProtocolVersion, BRIDGE_PROTOCOL_VERSION);
    assert.equal(record.bridgeVersion, BRIDGE_VERSION);
  } finally {
    if (oldAppData === undefined) delete process.env.APPDATA;
    else process.env.APPDATA = oldAppData;
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("apply emits one bridge/hello record per application", () => {
  const oldAppData = process.env.APPDATA;
  const oldWebSocket = globalThis.WebSocket;
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "dsh-bridge-hello-"));
  process.env.APPDATA = tempRoot;
  // Avoid a real DSH mux connection in this producer-only contract test.
  globalThis.WebSocket = undefined;
  const ctx = {
    on() { return () => {}; },
    effect() {},
    get() { return undefined; },
  };
  try {
    apply(ctx);
    __bridgeTest.flush();
    const file = path.join(tempRoot, "dsh-pet-bridge", __bridgeTest.instanceFile);
    const records = fs.readFileSync(file, "utf8").trim().split(/\r?\n/).map(JSON.parse);
    const hellos = records.filter((record) => record.event === "bridge/hello");
    assert.equal(hellos.length, 1);
    assert.equal(hellos[0].bridgeProtocolVersion, BRIDGE_PROTOCOL_VERSION);
    assert.equal(hellos[0].bridgeVersion, BRIDGE_VERSION);
    assert.deepEqual(hellos[0].capabilities, [...BRIDGE_CAPABILITIES]);
    assert.deepEqual(hellos[0].emittedEvents, [...BRIDGE_EVENT_INVENTORY]);
  } finally {
    if (oldAppData === undefined) delete process.env.APPDATA;
    else process.env.APPDATA = oldAppData;
    if (oldWebSocket === undefined) delete globalThis.WebSocket;
    else globalThis.WebSocket = oldWebSocket;
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});
