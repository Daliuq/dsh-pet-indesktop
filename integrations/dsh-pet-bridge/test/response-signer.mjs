// 跨语言认证联调用具：用**真实 Node 实现**的 canonical/HMAC 代码给桌宠端请求
// 签发响应，供 pytest 验证"Python 客户端能验通过 Node 桥接的签名"（反向亦然）。
//
// 为什么必须是真实实现而不是测试里重抄一份算法：both-sides-constant 断言只能
// 证明"常量没漂移"，只有把两侧**实际代码**连起来跑，才能证明握手真的成立。
//
// 用法：node response-signer.mjs <bridge_dir> <mode> [flags]
//   mode=sign   生成/读取密钥，按 canonical 协议签名并写 watchdog-response-<id>.json
//   mode=secret 仅确保密钥存在并打印（供 Python 侧对照）
// flags: --nonce=<hex> 回填请求 nonce；--secret=<s> 覆盖密钥（模拟错误密钥）
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

function arg(name) {
  const hit = process.argv.find((item) => item.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : "";
}

const bridgeDir = process.argv[2];
const mode = process.argv[3] || "sign";
if (!bridgeDir) {
  console.error("usage: node response-signer.mjs <bridge_dir> [sign|secret] [--nonce=…] [--secret=…]");
  process.exit(2);
}

process.env.DSH_PET_BRIDGE_DIR = bridgeDir;
const implUrl = pathToFileURL(path.resolve("impl", "0.3.0", "index.js")).href;
const impl = await import(implUrl);
const { canonicalResponse, computeResponseSignature } = impl.__controlTest;

const secret = arg("secret") || impl.__controlTest.ensureSecret();
if (mode === "secret") {
  process.stdout.write(secret);
  process.exit(0);
}

const deadline = Date.now() + 8000;
let request = null;
while (Date.now() < deadline) {
  let names = [];
  try {
    names = fs.readdirSync(bridgeDir).filter((n) => n.startsWith("watchdog-request-") && n.endsWith(".json"));
  } catch { /* 目录尚未建立 */ }
  if (names.length > 0) {
    request = JSON.parse(fs.readFileSync(path.join(bridgeDir, names[0]), "utf8"));
    break;
  }
  await new Promise((resolve) => setTimeout(resolve, 25));
}
if (!request) {
  console.error("no watchdog-request-*.json appeared");
  process.exit(3);
}

// 盘上响应 body：桥接侧 writeControlResponse 的同款字段集合。
const body = {
  id: String(request.id || ""),
  nonce: arg("nonce") || String(request.nonce || ""),
  ok: true,
  phase: "replanned",
  error: "",
  alreadyIdle: false,
  foundAgent: true,
  cancelInvoked: false,
  rootSessionId: String(request.sessionId || ""),
  plan: "跨语言联调：下一步补测试",
};
const signed = { ...body, sig: computeResponseSignature(secret, body) };
// 必须与 Node 侧 _writeJsonAtomic 一致的落盘形态（Python 会重新 canonical 化，
// 故 key 顺序不敏感，但内容必须完整）。
fs.writeFileSync(
  path.join(bridgeDir, `watchdog-response-${body.id}.json`),
  JSON.stringify(signed),
  "utf8",
);
process.stdout.write(`signed:${body.id}:${canonicalResponse(body).length}`);
