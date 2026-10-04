#!/usr/bin/env node
/**
 * Postinstall: print next steps. Heavy setup (pip, data dirs) belongs in the user's project or `paperfit doctor --target <host>`.
 */

const path = require('path');
const fs = require('fs');

const pkgRoot = path.join(__dirname, '..');
const insideNodeModules = pkgRoot.includes(`${path.sep}node_modules${path.sep}`);
const isGlobal = process.env.npm_config_global === 'true';

console.log('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
console.log('  PaperFit (paperfit-cli) installed');
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');

if (!insideNodeModules) {
  // Git checkout: optional lightweight data scaffold (no pip)
  const dataDirs = ['data/backups', 'data/benchmarks/case', 'data/pages', 'data/evidence'];
  for (const rel of dataDirs) {
    const p = path.join(pkgRoot, rel);
    if (!fs.existsSync(p)) {
      fs.mkdirSync(p, { recursive: true });
    }
  }
}

console.log('PaperFit is natural-language-first: after installing a host, open that host in your paper project and describe the goal directly.\n');

console.log('Install host assets (recommended):');
console.log('  paperfit-install --target claude');
console.log('  paperfit-install --target codex');
console.log('  paperfit-install --target cursor --project /path/to/paper');
console.log('  paperfit-install --target all --force\n');

console.log('Suggested self-check messages:');
console.log('  Claude Code: 用 PaperFit 分析这篇论文的排版问题');
console.log("  Codex: Use the `paperfit` agent to inspect this paper's layout and tell me the main visual defects");
console.log('  Cursor: Use PaperFit to repair this paper layout with minimal semantic change\n');

console.log('Only use CLI commands for setup, debugging, or recovery:');
console.log('  paperfit doctor --target claude');
console.log('  paperfit init\n');

if (isGlobal) {
  console.log('Tip: run `paperfit-install --target <host>` once, then go back to the host and start with natural language.\n');
}
