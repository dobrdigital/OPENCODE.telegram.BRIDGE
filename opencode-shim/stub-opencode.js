#!/usr/bin/env node
'use strict';

/*
 * Test double standing in for the real OpenCode CLI.
 *
 * The shim launches OPENCODE_REAL_BIN directly (no shell), so the suite points
 * that variable at node.exe and passes this file as the leading argument.
 * The stub then reports exactly what it received: argv, cwd and PWD.
 */

const payload = {
  argv: process.argv.slice(2),
  cwd: process.cwd(),
  PWD: process.env.PWD || null,
};

process.stdout.write('__SHIMTEST__' + JSON.stringify(payload) + '\n');

const exitArg = process.argv.find((a) => a.startsWith('--exit-code='));
if (exitArg) {
  process.exit(parseInt(exitArg.slice('--exit-code='.length), 10) || 0);
}
process.exit(0);