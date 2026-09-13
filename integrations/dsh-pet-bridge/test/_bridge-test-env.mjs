import fs from "node:fs";
import os from "node:os";
import path from "node:path";

/**
 * 把"桥目录的数据根"重定向到临时目录，返回还原函数。
 *
 * 为什么两个环境变量都要设：impl 的 bridgeDir() 是平台相关的——
 *   win32  → %APPDATA%/dsh-pet-bridge
 *   darwin → ~/Library/Application Support/dsh-pet-bridge
 *   其他   → ~/.config/dsh-pet-bridge
 * 而 POSIX 上的 "~" 来自 os.homedir()（Node 在 POSIX 上优先读 $HOME）。
 * 所以**只设 APPDATA 的写法在 POSIX 上是空操作**：记录会写到 runner 的真实家
 * 目录，断言却去读 <tempRoot>/dsh-pet-bridge → ENOENT。本用例集登记进 CI 门禁
 * 之后，这个平台假设在 ubuntu/macos 上立刻暴露（在此之前门禁从未执行过它）。
 * 两个变量一起设，三个平台才都真正隔离。
 *
 * @param {string} root 临时数据根（调用方负责 rmSync 清理）
 * @returns {() => void} 还原函数（放在 finally 里调用）
 */
export function redirectBridgeDataRoot(root) {
  const previous = { APPDATA: process.env.APPDATA, HOME: process.env.HOME };
  process.env.APPDATA = root;
  process.env.HOME = root;
  return () => {
    for (const key of ["APPDATA", "HOME"]) {
      if (previous[key] === undefined) delete process.env[key];
      else process.env[key] = previous[key];
    }
  };
}

/** 一次性的临时数据根（mkdtemp，前缀用于区分用例家族）。 */
export function makeBridgeDataRoot(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

/**
 * 断言桥目录确实落在临时数据根内。
 *
 * 重定向一旦失效（例如某平台用第三个环境变量决定家目录），测试会**静默**读写
 * runner 的真实家目录：断言可能照样通过，却在别人的机器上留下垃圾、也可能
 * 读到上一次运行残留的记录。这里把它变成显式失败，同时把真实路径打进消息里，
 * 便于定位。
 */
export function assertBridgeDirIsolated(bridgeDirPath, root) {
  const relative = path.relative(root, bridgeDirPath);
  if (relative === "" || relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(
      `桥目录未落在临时数据根内（测试会污染真实家目录）: ${bridgeDirPath} ⊄ ${root}`,
    );
  }
}
