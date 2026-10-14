import assert from "node:assert/strict";
import test from "node:test";

import { PHASE_DEVELOPMENT_SERVER } from "next/constants.js";
import nextConfig from "../../next.config.mjs";

test("Next dev watcher ignores Windows volume-root system files", () => {
  const config = nextConfig(PHASE_DEVELOPMENT_SERVER);
  const watchConfig = config.webpack({ watchOptions: { ignored: ["**/node_modules/**", "**/.next/**"] } });
  const ignored = watchConfig.watchOptions.ignored;
  assert.ok(ignored instanceof RegExp);
  const paths = [
    "C:\\DumpStack.log.tmp",
    "C:\\pagefile.sys",
    "C:\\swapfile.sys",
    "C:\\hiberfil.sys",
    "C:\\System Volume Information",
    "C:\\System Volume Information\\tracking.log",
    "C:\\$RECYCLE.BIN\\desktop.ini",
  ];

  assert.ok(paths.every((path) => ignored.test(path)));
  assert.ok(ignored.test("C:\\Users\\user\\alpha\\frontend\\node_modules\\next\\dist\\server.js"));
  assert.ok(ignored.test("C:\\Users\\user\\alpha\\frontend\\.next\\dev\\server.js"));
  assert.ok(!ignored.test("C:\\Users\\user\\alpha\\frontend\\src\\app\\page.tsx"));
});
