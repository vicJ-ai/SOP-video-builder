// Copies the Remocn components this project uses from the public registry into src/.
// Remocn is shadcn-style (copy-paste, MIT): the files become part of this repo and are
// committed, so renders never depend on the registry being reachable.
//
//   node scripts/vendor-remocn.mjs            # fetch missing components
//   node scripts/vendor-remocn.mjs --force    # refresh all
import { mkdir, writeFile, access, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REGISTRY = "https://remocn.dev/r";
const COMPONENTS = [
  "caption-subtitle", // burned-in narration captions (pulls caption-core)
  "progress", // SOP progress bar (pulls remocn-ui)
  "stepper", // step indicator for the intro card
  "soft-blur-in", // process title
  "fade-through", // current-action heading changes
  "micro-scale-fade", // view labels
  "backdrop", // intro background
  "toast", // "SOP issue" / result callouts
  "icon-check", // completed-step tick (pulls icons-core)
];

const force = process.argv.includes("--force");
const seen = new Set();

async function exists(p) {
  try {
    await access(p);
    return true;
  } catch {
    return false;
  }
}

async function vendor(name) {
  if (seen.has(name)) return;
  seen.add(name);
  const res = await fetch(`${REGISTRY}/${name}.json`);
  if (!res.ok) throw new Error(`remocn: ${name} -> HTTP ${res.status}`);
  const item = await res.json();
  for (const dep of item.registryDependencies ?? []) {
    await vendor(dep.replace(/^@remocn\//, ""));
  }
  for (const file of item.files) {
    const target = path.join(ROOT, "src", file.target ?? file.path);
    if (!force && (await exists(target))) continue;
    if (force && (await exists(target)) && (await readFile(target, "utf8")).includes("local change (SOP builder)")) {
      console.log(`kept locally modified src/${file.target ?? file.path}`);
      continue;
    }
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, file.content);
    console.log(`vendored ${name} -> src/${file.target ?? file.path}`);
  }
}

for (const name of COMPONENTS) await vendor(name);
console.log("remocn components up to date");
