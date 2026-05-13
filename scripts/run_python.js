#!/usr/bin/env node

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const repoRoot = path.resolve(__dirname, '..');
const venvPython = path.join(repoRoot, '.venv', 'bin', 'python');
const python = fs.existsSync(venvPython) ? venvPython : 'python3';
const args = process.argv.slice(2);

if (args.length === 0) {
  console.error('Usage: node scripts/run_python.js <script.py> [args...]');
  process.exit(1);
}

const result = spawnSync(python, args, {
  stdio: 'inherit',
  cwd: repoRoot,
  env: process.env,
});

process.exit(result.status === null ? 1 : (result.status ?? 1));
