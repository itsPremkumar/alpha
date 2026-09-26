import { readFileSync } from "node:fs";
import ts from "typescript";

const urls = new Map();
export function moduleUrl(name) {
  if (urls.has(name)) return urls.get(name);
  let source = readFileSync(new URL(`./${name}.ts`, import.meta.url), "utf8");
  source = source.replace(/from "\.\/(api-client|sse-reducer|http)"/g, (_, dependency) => `from "${moduleUrl(dependency)}"`);
  const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
  const url = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
  urls.set(name, url);
  return url;
}
