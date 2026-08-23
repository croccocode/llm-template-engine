/**
 * Rendering of *.tpl.md templates with MiniJinja, shared by the hooks.
 *
 * The hook (`hooks/render_template.mjs`) speaks two host protocols but uses
 * this single renderer.
 *
 * Any *.tpl.md anywhere in the repo can be rendered: includes resolve first
 * against the template's own directory (local partials, short names), then
 * fall back to the project root (e.g. `{% include "README.md" %}`). Path
 * traversal (`..`) outside those two roots is rejected by the loader.
 *
 * Besides includes, a template can interpolate the output of a bash script
 * with `{{ sh("scripts/now.sh") }}`: same path resolution as includes, the
 * script's stdout replacing the call. This is arbitrary code execution per
 * project: the template *is* code, just like a hook.
 */

import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Environment } from "./vendor/minijinja_js.js";

export const TEMPLATE_SUFFIX = ".tpl.md";
export const SCRIPT_TIMEOUT_SECONDS = 30;

// Matches every spelling of a raw-block terminator, including the whitespace
// control variants ({%- endraw -%}), so verbatim inlining cannot be escaped.
const RAW_TERMINATOR = /\{%[-+]?\s*endraw\s*[-+]?%\}/g;

export const PROJECT_ROOT = path.dirname(fileURLToPath(import.meta.url));

export class TemplateRenderError extends Error {}

export function isTemplate(filePath) {
  return path.basename(filePath).endsWith(TEMPLATE_SUFFIX);
}

/**
 * First existing file among `roots`, in order. Like the Python loader, it
 * discards candidates that (via `..`) escape the root they start from.
 */
function resolveInRoots(name, roots) {
  for (const root of roots) {
    const candidate = path.resolve(root, name);
    // Windows paths are case-insensitive, so compare case-folded. The sep
    // suffix stops `/foo-evil` from passing as inside `/foo`.
    const prefix = root.endsWith(path.sep) ? root : root + path.sep;
    const inside =
      process.platform === "win32"
        ? candidate.toLowerCase().startsWith(prefix.toLowerCase())
        : candidate.startsWith(prefix);
    if (!inside) continue;
    try {
      if (fs.statSync(candidate).isFile()) return candidate;
    } catch {
      // not there, or not readable: try the next root
    }
  }
  return null;
}

let bashIsWslCache;

/**
 * On Windows `bash` may be Git for Windows (native Windows paths work as they
 * are, mounted directly) or the WSL stub in System32 (it wants Linux paths
 * like /mnt/c/...; a path with backslashes arrives mangled and the file "does
 * not exist"). We tell them apart once by asking bash itself who it is.
 */
function bashIsWsl() {
  if (process.platform !== "win32") return false;
  if (bashIsWslCache !== undefined) return bashIsWslCache;
  try {
    const out = execFileSync("bash", ["-c", "uname -r"], {
      encoding: "utf8",
      timeout: SCRIPT_TIMEOUT_SECONDS * 1000,
      windowsHide: true,
    });
    bashIsWslCache = out.toLowerCase().includes("microsoft");
  } catch {
    bashIsWslCache = false; // not knowing isn't fatal, assume Git Bash
  }
  return bashIsWslCache;
}

/**
 * Converts a Windows path into the form the resolved `bash` expects as a
 * command line argument (the WSL stub wants /mnt/<drive>/..., Git for Windows
 * accepts the native path).
 */
function toBashPath(filePath) {
  if (!bashIsWsl()) return filePath;
  const { root } = path.parse(filePath);
  const drive = /^([a-z]):[\\/]/i.exec(root);
  if (!drive) return filePath;
  const tail = filePath.slice(root.length).split(path.sep).join("/");
  return `/mnt/${drive[1].toLowerCase()}/${tail}`;
}

/** Runs a resolved script with bash and returns its stdout, sans trailing newline. */
function runScript(script, scriptPath, args) {
  let result;
  try {
    // No shell: arguments stay arguments, no requoting. cwd is pinned so the
    // same template renders identically no matter which directory the agent
    // was launched from.
    result = execFileSync("bash", [toBashPath(scriptPath), ...args.map(String)], {
      cwd: PROJECT_ROOT,
      encoding: "utf8",
      timeout: SCRIPT_TIMEOUT_SECONDS * 1000,
      maxBuffer: 32 * 1024 * 1024,
      // Capture stderr instead of letting execFileSync forward it to ours, and
      // keep the script away from our stdin (it carries the hook payload).
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
  } catch (err) {
    if (err.code === "ENOENT") {
      throw new TemplateRenderError(`'${script}': bash not found on the PATH`);
    }
    if (err.code === "ETIMEDOUT" || err.signal) {
      throw new TemplateRenderError(
        `script '${script}' did not finish within ${SCRIPT_TIMEOUT_SECONDS}s`,
      );
    }
    const stderr = String(err.stderr ?? "").trim();
    throw new TemplateRenderError(
      `script '${script}' exited with code ${err.status}: ${stderr}`,
    );
  }
  return String(result).replace(/\r?\n$/, "");
}

export function render(filePath) {
  const resolved = path.resolve(filePath);
  const roots = [path.dirname(resolved), PROJECT_ROOT];
  const env = new Environment();
  env.debug = true;

  // A callback that throws would trap the WASM module: the real message is
  // lost ("RuntimeError: unreachable") and, once trapped, later error paths
  // in this process degrade too. So callbacks NEVER throw -- they park the
  // failure here and we raise it after the render returns.
  let failure = null;

  env.setLoader((name) => {
    if (failure) return null;
    const candidate = resolveInRoots(name, roots);
    if (candidate === null) return null;
    let source;
    try {
      source = fs.readFileSync(candidate, "utf8");
    } catch (err) {
      failure ??= `could not read '${name}': ${err.message}`;
      return null;
    }
    // Only *.tpl.md files are templates. Everything else is inlined
    // verbatim: a plain .md must not have its {{ }} / {% %} evaluated.
    // Otherwise including any markdown file would be arbitrary code
    // execution, since `sh()` is in scope during the render.
    if (isTemplate(name)) return source;
    // A literal raw-terminator in the text would close our wrapper early and
    // leave the tail to be parsed. So each one is stepped around: close the
    // raw block, emit the terminator as a plain string expression, reopen.
    // This keeps documents that *talk about* the engine inlinable -- this
    // project's own README is one of them.
    const escaped = source.replace(
      RAW_TERMINATOR,
      (match) => `{% endraw %}{{ ${JSON.stringify(match)} }}{% raw %}`,
    );
    return `{% raw %}${escaped}{% endraw %}`;
  });

  env.addGlobal("sh", (script, ...args) => {
    if (failure) return "";
    const scriptPath = resolveInRoots(script, roots);
    if (scriptPath === null) {
      failure ??= `script '${script}' not found in ${roots.join(", ")}`;
      return "";
    }
    try {
      return runScript(script, scriptPath, args);
    } catch (err) {
      failure ??= err.message;
      return "";
    }
  });

  let rendered;
  try {
    rendered = env.renderTemplate(path.basename(resolved), {});
  } catch (err) {
    // A parked callback failure is the more useful diagnosis: the engine's own
    // error is usually just a downstream symptom of it.
    throw new TemplateRenderError(failure ?? err.message);
  }
  if (failure) throw new TemplateRenderError(failure);
  return rendered;
}
