#!/usr/bin/env node
'use strict';

/*
 * Automated test suite for opencode-shim.
 *
 * Run:
 *   node --test <USERPROFILE>\.cc-connect\opencode-shim\
 * or
 *   node <USERPROFILE>\.cc-connect\opencode-shim\test-opencode-shim.js
 *
 * Layers
 *   1. unit      - stripDirArg / resolveCwd argument rewriting
 *   2. e2e       - real process spawn through the shim, against a stub CLI
 *   3. contract  - the exact argv cc-connect v1.5.0 builds, against the REAL
 *                  OpenCode 2.0.28 binary. This is the layer that proves the
 *                  original bug and the fix.
 */

const test = require('node:test');
const assert = require('node:assert');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const SHIM_DIR = __dirname;
const SHIM = path.join(SHIM_DIR, 'opencode-shim.js');
const STUB = path.join(SHIM_DIR, 'stub-opencode.js');
const NODE = process.execPath;

/* unit: argv rewriting for `session list --json` */
const { normalizeSessionListArgs } = require('./opencode-shim.js');

const REAL_BIN =
  process.env.OPENCODE_REAL_BIN ||
  path.join(process.env.APPDATA || '', 'npm', 'node_modules', '@opencode', 'cli', 'bin', 'opencode.exe');

// Working directory for the contract tests. It must exist and be a real
// directory - the shim falls back to its own cwd when --dir is unusable, so a
// placeholder path would silently exercise a different code path than the one
// being tested. Default to a throwaway directory rather than a machine path,
// which is also what keeps this file publishable.
const CC_WORK_DIR = fs.existsSync(process.env.CC_WORK_DIR || '')
  ? process.env.CC_WORK_DIR
  : fs.mkdtempSync(path.join(os.tmpdir(), 'cc-shim-contract-'));

// Contract tests exercise the REAL OpenCode binary. CI has no OpenCode
// installed, so skip rather than fail: a red build on a missing dependency
// teaches people to ignore red builds.
const HAS_REAL_BIN = fs.existsSync(REAL_BIN);
const needsRealBin = {
  skip: HAS_REAL_BIN ? false : 'real OpenCode binary not installed (skipped in CI)',
};

const { stripDirArg, resolveCwd } = require(SHIM);

/** Run the shim with the stub standing in for OpenCode. */
function runShim(args, opts) {
  const options = opts || {};
  const res = spawnSync(process.execPath, [SHIM, STUB].concat(args), {
    encoding: 'utf8',
    env: Object.assign({}, process.env, {
      OPENCODE_REAL_BIN: NODE,
    }, options.env || {}),
    timeout: 30000,
  });
  if (res.status !== 0 && !res.stdout) {
    throw new Error('shim run failed: ' + res.stderr);
  }
  const line = (res.stdout || '')
    .split('\n')
    .find((l) => l.startsWith('__SHIMTEST__'));
  return {
    report: line ? JSON.parse(line.slice('__SHIMTEST__'.length)) : null,
    status: res.status,
    stdout: res.stdout,
    stderr: res.stderr,
  };
}

/* ------------------------------------------------------------------ */
/* 1. unit — argument rewriting                                       */
/* ------------------------------------------------------------------ */

test('unit: removes two-token --dir form', () => {
  const r = stripDirArg(['run', '--dir', 'E:/w', '--thinking', 'hi']);
  assert.deepStrictEqual(r.args, ['run', '--thinking', 'hi']);
  assert.strictEqual(r.dir, 'E:/w');
  assert.strictEqual(r.sawDir, true);
  assert.strictEqual(r.danglingDir, false);
});

test('unit: removes equals --dir= form', () => {
  const r = stripDirArg(['run', '--dir=E:/w', 'hi']);
  assert.deepStrictEqual(r.args, ['run', 'hi']);
  assert.strictEqual(r.dir, 'E:/w');
});

test('unit: leaves argv untouched when --dir absent', () => {
  const argv = ['run', '--format', 'json', '--thinking', 'hi'];
  const r = stripDirArg(argv);
  assert.deepStrictEqual(r.args, argv);
  assert.strictEqual(r.dir, null);
  assert.strictEqual(r.sawDir, false);
});

test('unit: preserves argument order and exotic content', () => {
  const argv = [
    'run',
    '--format', 'json',
    '--session', 'ses_abc123',
    '--model', 'opencode/space-bunny-free',
    '--dir', 'E:/path with spaces/work dir',
    '--thinking',
    '--dangerously-skip-permissions',
    'prompt with "quotes" & | < > ^ %VAR% \u00e9\u2013\ud83d\ude80',
  ];
  const r = stripDirArg(argv);
  assert.deepStrictEqual(r.args, [
    'run',
    '--format', 'json',
    '--session', 'ses_abc123',
    '--model', 'opencode/space-bunny-free',
    '--thinking',
    '--dangerously-skip-permissions',
    'prompt with "quotes" & | < > ^ %VAR% \u00e9\u2013\ud83d\ude80',
  ]);
});

test('unit: handles repeated --dir (last wins)', () => {
  const r = stripDirArg(['--dir', '/a', 'run', '--dir=/b', 'hi']);
  assert.deepStrictEqual(r.args, ['run', 'hi']);
  assert.strictEqual(r.dir, '/b');
});

test('unit: dangling --dir at end does not crash', () => {
  const r = stripDirArg(['run', 'hi', '--dir']);
  assert.deepStrictEqual(r.args, ['run', 'hi']);
  assert.strictEqual(r.dir, null);
  assert.strictEqual(r.danglingDir, true);
});

test('unit: does not confuse --dirpath or --directory with --dir', () => {
  const r = stripDirArg(['--dirpath', 'x', '--directory', 'y', 'run', 'hi']);
  assert.deepStrictEqual(r.args, ['--dirpath', 'x', '--directory', 'y', 'run', 'hi']);
  assert.strictEqual(r.sawDir, false);
});

test('unit: resolveCwd honours an existing directory', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'shimcwd-'));
  assert.strictEqual(resolveCwd(dir, 'FALLBACK'), fs.realpathSync.native(dir) === dir ? dir : path.resolve(dir));
});

test('unit: resolveCwd falls back when the path does not exist', () => {
  assert.strictEqual(resolveCwd(path.join(os.tmpdir(), 'nope-does-not-exist-xyz'), 'FALLBACK'), 'FALLBACK');
  assert.strictEqual(resolveCwd(null, 'FALLBACK'), 'FALLBACK');
});

test('unit: resolveCwd falls back when the path is a file, not a directory', () => {
  const file = path.join(os.tmpdir(), 'shim-file-' + process.pid + '.txt');
  fs.writeFileSync(file, 'x');
  assert.strictEqual(resolveCwd(file, 'FALLBACK'), 'FALLBACK');
  fs.unlinkSync(file);
});

/* ------------------------------------------------------------------ */
/* 2. e2e — real process spawn through the shim                       */
/* ------------------------------------------------------------------ */

test('e2e: --dir is stripped and working directory follows it', () => {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'shimwork-'));
  const { report } = runShim(['run', '--format', 'json', '--dir', work, '--thinking', 'hello']);
  assert.ok(report, 'stub did not report');
  assert.strictEqual(report.argv.includes('--dir'), false);
  assert.strictEqual(report.argv.includes(work), false);
  assert.deepStrictEqual(report.argv, ['run', '--format', 'json', '--thinking', 'hello']);
  assert.strictEqual(fs.realpathSync.native(report.cwd), fs.realpathSync.native(work));
  assert.strictEqual(fs.realpathSync.native(report.PWD), fs.realpathSync.native(work));
});

test('e2e: equals form is stripped too', () => {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'shimwork-'));
  const { report } = runShim(['run', '--dir=' + work, 'hi']);
  assert.deepStrictEqual(report.argv, ['run', 'hi']);
  assert.strictEqual(fs.realpathSync.native(report.cwd), fs.realpathSync.native(work));
});

test('e2e: transparent passthrough when no --dir is present', () => {
  const { report } = runShim(['run', '--format', 'json', 'hi']);
  assert.deepStrictEqual(report.argv, ['run', '--format', 'json', 'hi']);
  assert.strictEqual(fs.realpathSync.native(report.cwd), fs.realpathSync.native(process.cwd()));
});

test('e2e: exotic prompt survives the shim byte for byte', () => {
  const prompt = 'проверка "кавычки" & | < > ^ %PATH% \u2013 \ud83d\ude80';
  const { report } = runShim(['run', '--dir', os.tmpdir(), prompt]);
  assert.strictEqual(report.argv[report.argv.length - 1], prompt);
});

test('e2e: exit code is propagated', () => {
  const r = runShim(['run', '--exit-code=42', 'hi']);
  assert.strictEqual(r.status, 42);
});

/* ------------------------------------------------------------------ */
/* 3. contract — the real OpenCode 2.0.28 binary                      */
/* ------------------------------------------------------------------ */

test('unit: session list --json is rewritten to --format json', () => {
  assert.deepStrictEqual(
    normalizeSessionListArgs(['session', 'list', '--json']),
    ['session', 'list', '--format', 'json']
  );
});

test('unit: rewrite leaves an explicit --format alone', () => {
  assert.deepStrictEqual(
    normalizeSessionListArgs(['session', 'list', '--format', 'json']),
    ['session', 'list', '--format', 'json']
  );
});

test('unit: rewrite is scoped to session list', () => {
  // `run` already receives --format json from cc-connect; a global rewrite
  // would be a guess rather than a fix.
  assert.deepStrictEqual(
    normalizeSessionListArgs(['run', '--format', 'json', '--session', 'ses_x']),
    ['run', '--format', 'json', '--session', 'ses_x']
  );
  assert.deepStrictEqual(
    normalizeSessionListArgs(['session', 'delete', '--json']),
    ['session', 'delete', '--json']
  );
});

test('unit: rewrite survives a preceding --dir being stripped', () => {
  const { stripDirArg } = require('./opencode-shim.js');
  const parsed = stripDirArg(['session', 'list', '--dir', CC_WORK_DIR, '--json']);
  assert.deepStrictEqual(
    normalizeSessionListArgs(parsed.args),
    ['session', 'list', '--format', 'json']
  );
});

test('contract: real OpenCode rejects session list --json', needsRealBin, () => {
  const res = spawnSync(REAL_BIN, ['session', 'list', '--json'], {
    encoding: 'utf8',
    cwd: CC_WORK_DIR,
    timeout: 90000,
  });
  assert.strictEqual(
    res.status,
    1,
    'expected the bare --json form to fail; if this now passes, drop the rewrite'
  );
});

test('contract: through the shim, cc-connect argv for /list returns JSON', needsRealBin, () => {
  const res = spawnSync(NODE, [SHIM, 'session', 'list', '--json'], {
    encoding: 'utf8',
    cwd: CC_WORK_DIR,
    timeout: 90000,
  });
  const out = (res.stdout || '') + (res.stderr || '');
  assert.strictEqual(res.status, 0, 'exit code ' + res.status + '. Output: ' + out.slice(0, 400));
  let parsed;
  try {
    parsed = JSON.parse(res.stdout);
  } catch (_) {
    assert.fail('stdout is not JSON. Got: ' + out.slice(0, 400));
  }
  assert.ok(Array.isArray(parsed), 'expected a JSON array of sessions');
});

/* ------------------------------------------------------------------ */
/* 3. contract — the real OpenCode 2.0.28 binary                      */
/* ------------------------------------------------------------------ */

test('contract: real OpenCode binary is present', needsRealBin, () => {
  assert.ok(fs.existsSync(REAL_BIN), 'missing OpenCode binary: ' + REAL_BIN);
});

/* ------------------------------------------------------------------ */
/* 4. cleanup - the contract tests are not a sandbox                    */
/* ------------------------------------------------------------------ */

/*
 * `opencode run` writes a real session row into the shared production
 * database. Measured on this machine: one test run took opencode.db from 21
 * sessions to 22, and the leftovers accumulate as "Ответ одним словом"
 * entries that a person later has to recognise as rubbish by hand.
 *
 * XDG_DATA_HOME is not an escape. It relocates the client's log and repos
 * directories, but the CLI attaches to the running `serve --service`, and the
 * database belongs to that server - so the session lands in the real file
 * regardless.
 *
 * The session id is printed in the run output, so each test can delete
 * precisely what it created instead of guessing at a cleanup rule.
 */
function deleteSessionCreatedBy(stdout, cwd) {
  const m = /ses_[A-Za-z0-9]+/.exec(stdout || '');
  if (!m) return false;
  const r = spawnSync(REAL_BIN, ['session', 'delete', m[0]], {
    encoding: 'utf8',
    cwd: cwd || CC_WORK_DIR,
    timeout: 90000,
  });
  return r.status === 0;
}

test('contract: without the shim, OpenCode 2.0.28 rejects --dir', needsRealBin, () => {
  const res = spawnSync(REAL_BIN, ['run', '--dir', CC_WORK_DIR, 'say ok'], {
    encoding: 'utf8',
    timeout: 90000,
  });
  const out = (res.stdout || '') + (res.stderr || '');
  try {
    assert.match(
      out,
      /Unrecognized flag: --dir/,
      'expected OpenCode to reject --dir; got: ' + out.slice(0, 300)
    );
  } finally {
    deleteSessionCreatedBy(out);
  }
});

test('contract: through the shim, the cc-connect argv runs clean', { ...needsRealBin, timeout: 180000 }, () => {
  // Exactly what cc-connect v1.5.0 buildRunArgs() produces, including --dir.
  const ccArgs = [
    'run',
    '--format', 'json',
    '--model', 'opencode/space-bunny-free',
    '--dir', CC_WORK_DIR,
    '--thinking',
    '--dangerously-skip-permissions',
    'Ответь одним словом: работает',
  ];

  const res = spawnSync(NODE, [SHIM].concat(ccArgs), {
    encoding: 'utf8',
    env: Object.assign({}, process.env, { OPENCODE_REAL_BIN: REAL_BIN }),
    timeout: 180000,
    cwd: CC_WORK_DIR,
  });

  const out = (res.stdout || '') + (res.stderr || '');

  try {
    assert.ok(
      !/Unrecognized flag/.test(out),
      'shim failed to strip --dir. Output: ' + out.slice(0, 500)
    );
    assert.strictEqual(res.status, 0, 'exit code ' + res.status + '. Output: ' + out.slice(0, 500));

    // The agent must actually have produced a turn, not merely exited 0.
    assert.ok(
      /"type"\s*:\s*"(text|step_finish)"/.test(out),
      'no agent events on stdout. Output: ' + out.slice(0, 500)
    );
  } finally {
    // This test provably talks to the model and therefore provably leaves a
    // session behind. Cleaning up in `finally` means a failing assertion
    // does not leak one either - which is how five of them got there.
    deleteSessionCreatedBy(out, CC_WORK_DIR);
  }
});