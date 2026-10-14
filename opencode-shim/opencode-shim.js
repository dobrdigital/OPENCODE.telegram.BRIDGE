#!/usr/bin/env node
'use strict';

/*
 * opencode-shim — compatibility shim for cc-connect v1.5.0 + OpenCode >= 2.0.12.
 *
 * Why this exists
 * ---------------
 * cc-connect v1.5.0 builds its OpenCode invocation as:
 *
 *     opencode run --format json --session <id> --model <m> --dir <workDir> ...
 *
 * OpenCode removed the `--dir` flag from `opencode run`. The CLI rejects the
 * whole invocation before doing any work:
 *
 *     ERROR  Unrecognized flag: --dir in command opencode run
 *
 * Upstream cc-connect tracks this in:
 *   - issue chenhg5/cc-connect#1892
 *   - PR    chenhg5/cc-connect#1896  (merged: false as of 2026-10-14)
 *
 * The upstream fix simply deletes `--dir <workDir>` from buildRunArgs, because
 * cc-connect already sets the subprocess working directory through `cmd.Dir`.
 * This shim reproduces exactly that behaviour on the client side, without
 * waiting for the merge and without touching the npm-managed shims.
 *
 * It strips both spellings of the flag:
 *     --dir <path>          (two argv entries)
 *     --dir=<path>          (single argv entry)
 *
 * and leaves every other argument untouched, in order.
 *
 * Behaviour when no `--dir` is present: fully transparent passthrough.
 *
 * Environment
 * -----------
 *   OPENCODE_REAL_BIN   Override the real CLI path (used by the test suite).
 */

const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const DEFAULT_REAL_BIN = path.join(
  process.env.APPDATA || '',
  'npm',
  'node_modules',
  '@opencode',
  'cli',
  'bin',
  'opencode.exe'
);

const DIR_FLAG = '--dir';
const DIR_PREFIX = '--dir=';

/**
 * Remove `--dir` from an argument vector.
 *
 * @param {string[]} argv
 * @returns {{args: string[], dir: string|null, sawDir: boolean, danglingDir: boolean}}
 */
function stripDirArg(argv) {
  const args = [];
  let dir = null;
  let sawDir = false;
  let danglingDir = false;

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];

    if (arg === DIR_FLAG) {
      sawDir = true;
      if (i + 1 < argv.length) {
        dir = argv[i + 1];
        i++;
      } else {
        danglingDir = true;
      }
      continue;
    }

    if (typeof arg === 'string' && arg.startsWith(DIR_PREFIX)) {
      sawDir = true;
      dir = arg.slice(DIR_PREFIX.length);
      continue;
    }

    args.push(arg);
  }

  return { args, dir, sawDir, danglingDir };
}

/**
 * Rewrite `--json` to `--format json` for the `session list` subcommand.
 *
 * Why this exists
 * ---------------
 * cc-connect v1.5.0 invokes `opencode session list --json`. OpenCode 2.0.28
 * does not accept `--json` on that subcommand — it accepts `--format json`.
 * The mismatch is silent from OpenCode's side: it prints its help text and
 * exits 1, which cc-connect reports as
 *
 *     Failed to list sessions: opencode: session list: exit status 1
 *
 * Scoped to `session list` deliberately. cc-connect already passes
 * `--format json` to `opencode run`, and other subcommands may legitimately
 * accept `--json`. A global rewrite would be a guess; this is evidence-based.
 *
 * @param {string[]} args
 * @returns {string[]}
 */
function normalizeSessionListArgs(args) {
  const sessionAt = args.indexOf('session');
  const isSessionList = sessionAt >= 0 && args[sessionAt + 1] === 'list';
  if (!isSessionList) return args;

  const out = [];
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--json') {
      out.push('--format', 'json');
      continue;
    }
    out.push(args[i]);
  }
  return out;
}

/**
 * Decide which directory the real CLI should run in.
 * Falls back to the inherited cwd when --dir is absent or unusable.
 */
function resolveCwd(dir, fallback) {
  if (!dir) return fallback;
  const resolved = path.resolve(dir);
  try {
    if (fs.statSync(resolved).isDirectory()) return resolved;
  } catch (_) {
    /* not accessible - fall through */
  }
  return fallback;
}

function main() {
  const real = process.env.OPENCODE_REAL_BIN || DEFAULT_REAL_BIN;
  const parsed = stripDirArg(process.argv.slice(2));

  if (!fs.existsSync(real)) {
    process.stderr.write(
      'opencode-shim: real OpenCode binary not found: ' + real + '\n'
    );
    process.exit(127);
  }

  const cwd = resolveCwd(parsed.dir, process.cwd());
  const args = normalizeSessionListArgs(parsed.args);
  const child = spawn(real, args, {
    stdio: 'inherit',
    cwd,
    env: Object.assign({}, process.env, { PWD: cwd }),
    windowsHide: true,
  });

  child.on('error', (err) => {
    process.stderr.write(
      'opencode-shim: failed to launch OpenCode: ' + err.message + '\n'
    );
    process.exit(127);
  });

  child.on('exit', (code, signal) => {
    if (signal) {
      process.stderr.write('opencode-shim: child killed by ' + signal + '\n');
      process.exit(1);
    }
    process.exit(code === null ? 1 : code);
  });
}

module.exports = {
  stripDirArg,
  normalizeSessionListArgs,
  resolveCwd,
  DEFAULT_REAL_BIN,
  DIR_FLAG,
  DIR_PREFIX,
};

if (require.main === module) {
  main();
}