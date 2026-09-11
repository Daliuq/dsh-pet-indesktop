// F2b 回归：mux 的 question/requested、question/resolved 帧必须补上 callId。
//
// 背景：mux 帧只带 rpcId，而 callId 是 tool/call 兜底路径的登记身份（桥接内
// 复合键 sessionId|callId 存在 pendingQuestionCallIds）。桌宠端问题气泡在
// 升级重建后靠 callId 与兜底 resolved 配对；帧里缺 callId 时，mux 断线场景下
// 兜底发出的 question/resolved(callId) 就匹配不到，气泡永远关不掉。
// 测试直接驱动与生产 mux 分支相同的构造函数，不启动 DSH 宿主与 WebSocket。
import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const bridgeDir = path.resolve(here, "../integrations/dsh-pet-bridge");

let q;
test.before(async () => {
  const bridge = await import(pathToFileURL(path.join(bridgeDir, "index.js")));
  q = bridge.__questionTest;
  assert.ok(q, "bridge must export __questionTest");
});

test.beforeEach(() => {
  q.pendingQuestionCallIds.clear();
});

test("mux question/requested 帧按会话反查补写 callId", () => {
  q.pendingQuestionCallIds.add(q.questionCallIdentity("call-9", "sess-1"));
  const rec = q.muxQuestionRequestedRecord("rpc-7", {
    sessionId: "sess-1",
    questions: [{ id: "q1", question: "选择？" }],
  });
  assert.equal(rec.event, "question/requested");
  assert.equal(rec.rpcId, "rpc-7");
  assert.equal(rec.sessionId, "sess-1");
  assert.equal(
    rec.callId,
    "call-9",
    "帧必须带上同会话待答的 callId，否则 mux 断线时兜底 resolved 关不掉气泡",
  );
});

test("mux question/resolved 帧同样补写 callId", () => {
  q.pendingQuestionCallIds.add(q.questionCallIdentity("call-9", "sess-1"));
  const rec = q.muxQuestionResolvedRecord("rpc-7", { sessionId: "sess-1", outcome: "answered" });
  assert.equal(rec.event, "question/resolved");
  assert.equal(rec.rpcId, "rpc-7");
  assert.equal(rec.callId, "call-9");
});

test("反查限定同一会话，不把别的会话的 callId 串到本帧", () => {
  q.pendingQuestionCallIds.add(q.questionCallIdentity("call-9", "sess-1"));
  const rec = q.muxQuestionRequestedRecord("rpc-8", { sessionId: "sess-2", questions: [] });
  assert.equal(rec.callId, "", "不得跨会话挂 callId");
});

test("帧自带 callId 时原样保留", () => {
  const rec = q.muxQuestionRequestedRecord("rpc-7", {
    sessionId: "sess-1",
    callId: "call-frame",
    questions: [],
  });
  assert.equal(rec.callId, "call-frame");
});
