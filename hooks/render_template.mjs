/**
 * Single pre-tool-use hook for Claude Code and Copilot CLI.
 *
 * Intercepts the reading of a `*.tpl.md` file, expands it with MiniJinja
 * (`template_engine.mjs`) and returns the rendered content to the agent inside
 * the denial reason. Non-template files pass through immediately (suffix
 * check, no I/O).
 *
 * The logic is one and the same: only the host *protocol* changes, described
 * in the PROTOCOLS table below -- what the input keys are called, what shape
 * the output JSON has, and whether the allow must be declared or silence is
 * enough.
 *
 * Which protocol to use is stated by the host invoking us, since the two
 * config files are separate anyway: `--protocol claude` from
 * `.claude/settings.json`, `--protocol copilot` from
 * `.github/hooks/render-template.json`. Failing that we try to infer it from
 * the payload shape, which is however ambiguous: Copilot also has a "VS Code
 * compatible" format with the same keys as Claude.
 *
 * On any unexpected error we exit 0 with no output. This isn't fussiness:
 * Copilot's `preToolUse` command hooks are *fail-closed*, a non-zero exit
 * would block every tool call in the session.
 *
 * Unlike the Python original there is no dependency header to keep in sync:
 * MiniJinja is vendored under `vendor/`, so this hook needs nothing but a
 * `node` on the PATH.
 */

import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { TemplateRenderError, isTemplate, render } from "../template_engine.mjs";

function claudeOutput(decision, reason) {
  const payload = { hookEventName: "PreToolUse", permissionDecision: decision };
  if (reason !== null) payload.permissionDecisionReason = reason;
  return JSON.stringify({ hookSpecificOutput: payload });
}

function copilotOutput(decision, reason) {
  if (decision === "allow") return null; // empty stdout = no decision, Copilot proceeds
  return JSON.stringify({ permissionDecision: decision, permissionDecisionReason: reason });
}

const PROTOCOLS = {
  claude: {
    // Keys of the incoming payload.
    toolNameKeys: ["tool_name"],
    toolArgsKeys: ["tool_input"],
    // File-reading tools to intercept (compared lowercased).
    readTools: new Set(["read"]),
    // Keys to look the path up under, inside the tool arguments.
    pathKeys: ["file_path"],
    // How the decision is written to stdout. null = print nothing.
    output: claudeOutput,
  },
  copilot: {
    toolNameKeys: ["toolName", "tool_name"],
    // `toolArgs` is a JSON *string* in the CLI command hooks, an object in the
    // SDK: parseToolArgs() handles both.
    toolArgsKeys: ["toolArgs", "tool_input", "tool_args"],
    // `view` is the native tool; the others are defensive aliases.
    readTools: new Set(["view", "read", "read_file", "str_replace_editor"]),
    // Copilot passes `path`; the others are defensive aliases.
    pathKeys: ["path", "file_path", "filePath", "absolute_path"],
    output: copilotOutput,
  },
};

/** --protocol from the command line, then env, then nothing. */
function pickProtocol(argv) {
  let name = process.env.LTE_HOOK_PROTOCOL;
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--protocol" && i + 1 < argv.length) name = argv[i + 1];
    else if (argv[i].startsWith("--protocol=")) name = argv[i].split("=", 2)[1];
  }
  if (!name) return null;
  const protocol = PROTOCOLS[name.toLowerCase()];
  if (protocol === undefined) {
    // typo in the config: say so, don't fall silently back to sniffing
    process.stderr.write(`unknown protocol '${name}', trying to infer it from the payload\n`);
    return null;
  }
  return protocol;
}

/**
 * Fallback: camelCase keys belong to Copilot only. snake_case ones are
 * ambiguous (Claude, but also Copilot in VS Code format) and we treat them as
 * Claude, the only one of the two that *demands* an explicit output.
 */
function sniffProtocol(payload) {
  if ("toolName" in payload || "toolArgs" in payload) return PROTOCOLS.copilot;
  if ("tool_name" in payload || "tool_input" in payload) return PROTOCOLS.claude;
  return null;
}

function firstValue(source, keys) {
  for (const key of keys) if (source[key]) return source[key];
  return null;
}

function parseToolArgs(payload, protocol) {
  let args = firstValue(payload, protocol.toolArgsKeys);
  if (typeof args === "string") {
    // Copilot command hook: JSON string
    try {
      args = JSON.parse(args);
    } catch {
      return {};
    }
  }
  return args !== null && typeof args === "object" ? args : {};
}

function readPayload() {
  // Strip a BOM: some shells (PowerShell) prepend one on stdin.
  let raw;
  try {
    raw = fs.readFileSync(0, "utf8");
  } catch {
    return {};
  }
  raw = raw.replace(/^﻿/, "").trim();
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function decide(payload, protocol) {
  const toolName = firstValue(payload, protocol.toolNameKeys) || "";
  if (!protocol.readTools.has(String(toolName).toLowerCase())) return ["allow", null];

  const rawPath = firstValue(parseToolArgs(payload, protocol), protocol.pathKeys);
  if (!rawPath) return ["allow", null];

  let filePath = String(rawPath);
  if (!isTemplate(filePath)) return ["allow", null];
  if (!path.isAbsolute(filePath)) {
    // Copilot may pass paths relative to the session cwd.
    filePath = path.resolve(payload.cwd || process.cwd(), filePath);
  }

  let rendered;
  try {
    rendered = render(filePath);
  } catch (err) {
    if (err instanceof TemplateRenderError) {
      return ["deny", `Error rendering '${path.basename(filePath)}': ${err.message}`];
    }
    throw err;
  }

  return [
    "deny",
    `'${path.basename(filePath)}' is a source template. ` +
      `Here is the content of the file: ${rendered}`,
  ];
}

function main() {
  const payload = readPayload();

  const protocol = pickProtocol(process.argv.slice(2)) ?? sniffProtocol(payload);
  if (protocol === null) return; // unknown host: empty stdout, which everywhere means "proceed"

  const line = protocol.output(...decide(payload, protocol));
  if (line !== null) process.stdout.write(line + "\n");
}

try {
  main();
} catch (err) {
  // never exit != 0: on Copilot that's a deny
  process.stderr.write(`render_template hook error: ${err?.message}\n${err?.stack}\n`);
}
process.exit(0);
