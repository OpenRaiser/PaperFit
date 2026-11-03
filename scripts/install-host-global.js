#!/usr/bin/env node
/**
 * Install host-native PaperFit skills. Project scope never writes host homes.
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const PKG_ROOT = path.resolve(__dirname, '..');
const MANIFEST_PATH = path.join(PKG_ROOT, 'config', 'install_targets.json');

function parseArgs(argv = process.argv.slice(2)) {
  const options = { target: 'claude', scope: 'global', project: null, home: process.env.HOME || os.homedir(),
    force: false, dryRun: false, printManifest: false, help: false };
  const values = { '--target': 'target', '--scope': 'scope', '--project': 'project', '--home': 'home' };
  for (let index = 0; index < argv.length; index++) {
    const arg = argv[index];
    if (values[arg]) {
      if (!argv[index + 1] || argv[index + 1].startsWith('--')) throw new Error('Missing value for ' + arg);
      options[values[arg]] = argv[++index];
    } else if (arg === '--force') options.force = true;
    else if (arg === '--dry-run') options.dryRun = true;
    else if (arg === '--print-manifest') options.printManifest = true;
    else if (arg === '--help' || arg === '-h') options.help = true;
    else throw new Error('Unknown option: ' + arg);
  }
  if (!['global', 'project'].includes(options.scope)) throw new Error('Unsupported scope: ' + options.scope);
  options.home = path.resolve(options.home);
  options.project = options.project ? path.resolve(options.project)
    : options.scope === 'project' ? process.cwd() : null;
  return options;
}

function ensureDir(destination, dryRun) {
  if (!dryRun) fs.mkdirSync(destination, { recursive: true });
}

function materializeFileContent(source, destination) {
  const content = fs.readFileSync(source, 'utf8');
  if (/^---\r?\n/.test(content)) return content;
  const isSkill = path.basename(destination) === 'SKILL.md';
  const isAgent = path.extname(destination) === '.md' && path.basename(path.dirname(destination)) === 'agents';
  if (!isSkill && !isAgent) return content;
  const name = isSkill ? path.basename(path.dirname(source)) : path.basename(source, '.md');
  const title = (content.match(/^#\s+(.+)$/m) || [null, name])[1];
  // JSON strings are valid YAML scalars, including colons, quotes, and Unicode.
  const description = 'PaperFit ' + title + '. Use when the paper needs this layout expertise.';
  return '---\nname: ' + name + '\ndescription: ' + JSON.stringify(description) + '\n---\n\n' + content;
}

function copyFile(source, destination, options) {
  if (!fs.existsSync(source)) throw new Error('Missing package asset: ' + source);
  if (fs.existsSync(destination) && !options.force) {
    console.log('  skip (exists): ' + destination);
    return;
  }
  if (options.dryRun) {
    console.log('  [dry-run] ' + source + ' -> ' + destination);
    return;
  }
  ensureDir(path.dirname(destination), false);
  // Markdown metadata is materialized; images and other binary assets stay byte-identical.
  if (path.extname(source).toLowerCase() === '.md') {
    fs.writeFileSync(destination, materializeFileContent(source, destination), 'utf8');
  } else {
    fs.copyFileSync(source, destination);
  }
  console.log('  ok: ' + destination);
}

function copyTree(source, destination, options) {
  if (!fs.existsSync(source)) throw new Error('Missing package asset: ' + source);
  for (const entry of fs.readdirSync(source, { withFileTypes: true })) {
    if (['__pycache__', '.DS_Store'].includes(entry.name)) continue;
    const from = path.join(source, entry.name);
    const to = path.join(destination, entry.name);
    if (entry.isSymbolicLink()) throw new Error('Unexpected package symlink: ' + from);
    if (entry.isDirectory()) copyTree(from, to, options);
    else copyFile(from, to, options);
  }
}

function applyCopies(root, entries, options) {
  for (const entry of entries || []) {
    const source = path.join(PKG_ROOT, entry.source);
    const destination = path.resolve(root, entry.target);
    if (!destination.startsWith(path.resolve(root) + path.sep)) throw new Error('Install target escapes root: ' + destination);
    if (entry.type === 'directory') copyTree(source, destination, options);
    else if (entry.type === 'file') copyFile(source, destination, options);
    else throw new Error('Unsupported copy type: ' + entry.type);
  }
}

function main() {
  const options = parseArgs();
  if (options.help) {
    console.log('Usage: paperfit-install --target claude|codex|cursor|all [--scope global|project] [--project PATH] [--home PATH] [--dry-run] [--force]');
    console.log('Project scope installs only project skills/rules and a receipt. Existing files are preserved unless --force is specified.');
    return;
  }
  const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
  if (options.printManifest) { console.log(JSON.stringify(manifest, null, 2)); return; }
  const targets = options.target === 'all' ? Object.keys(manifest.targets) : [options.target];
  for (const target of targets) if (!manifest.targets[target]) throw new Error('Unsupported target: ' + target);
  console.log('PaperFit host installer: ' + options.scope + ' scope');
  const roots = {};
  for (const target of targets) {
    const spec = manifest.targets[target];
    if (options.scope === 'global') {
      const root = path.join(options.home, spec.root_suffix);
      roots[target] = root;
      applyCopies(root, spec.copies, options);
    }
    if (options.project) {
      applyCopies(options.project, spec.project_copies, options);
      roots[target + ':project'] = options.project;
    }
  }
  const sharedRoot = options.scope === 'project'
    ? path.join(options.project, '.paperfit') : path.join(options.home, manifest.shared_copy.root_suffix);
  if (options.scope === 'global') applyCopies(sharedRoot, manifest.shared_copy.copies, options);
  const receiptPath = path.join(sharedRoot, 'install-manifest.json');
  const previous = fs.existsSync(receiptPath) ? JSON.parse(fs.readFileSync(receiptPath, 'utf8')) : {};
  const recordedTargets = [...new Set([...(previous.targets || []), ...targets])];
  const receipt = { name: 'paperfit', version: require(path.join(PKG_ROOT, 'package.json')).version,
    packageRoot: PKG_ROOT, installedAt: new Date().toISOString(), targets: recordedTargets, scope: options.scope,
    sharedRoot, projectRoot: options.project, roots: { ...(previous.roots || {}), ...roots } };
  if (options.dryRun) console.log('[dry-run] receipt: ' + sharedRoot);
  else {
    ensureDir(sharedRoot, false);
    fs.writeFileSync(path.join(sharedRoot, 'install-manifest.json'), JSON.stringify(receipt, null, 2), 'utf8');
  }
  console.log('Open a new host session in the paper project to discover the paperfit skill.');
  console.log('Codex: $paperfit Inspect this paper layout / $paperfit Repair this paper layout');
  console.log('Claude Code: /paperfit or ask to use PaperFit. Cursor: ask to use PaperFit.');
  console.log('The host uses its own model and editing tools; no custom agent registration or provider change is required.');
  console.log('Environment check: paperfit doctor --target ' + options.target + (options.project ? ' --project "' + options.project + '"' : ''));
}

if (require.main === module) {
  try { main(); } catch (error) { console.error('PaperFit install: ' + error.message); process.exitCode = 1; }
}
module.exports = { parseArgs, materializeFileContent, copyFile, copyTree, applyCopies };
