#!/usr/bin/env node
/**
 * Backward-compatible wrapper for the new target-aware installer.
 * Default target remains Claude Code.
 */

const path = require('path');
const { spawnSync } = require('child_process');

const script = path.join(__dirname, 'install-host-global.js');
const args = process.argv.slice(2);
const hasTarget = args.includes('--target');
const forwarded = hasTarget ? args : ['--target', 'claude', ...args];
const result = spawnSync(process.execPath, [script, ...forwarded], { stdio: 'inherit' });
process.exit(result.status ?? 1);
