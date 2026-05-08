#!/usr/bin/env node
/**
 * Install PaperFit assets into one or more host homes.
 * Supported targets: claude, codex, cursor, all
 */

const fs = require('fs');
const path = require('path');
const os = require('os');

const HOME = os.homedir();
const PKG_ROOT = path.join(__dirname, '..');
const MANIFEST_PATH = path.join(PKG_ROOT, 'config', 'install_targets.json');
const FRONTMATTER_HOSTS = ['.codex', '.cursor'];
const CODEX_MARKETPLACE_ENTRY = {
  name: 'paperfit',
  source: {
    source: 'local',
    path: './.codex/plugins/paperfit',
  },
  policy: {
    installation: 'AVAILABLE',
    authentication: 'ON_INSTALL',
  },
  category: 'Coding',
};

function parseArgs() {
  const argv = process.argv.slice(2);
  const targetIndex = argv.findIndex((item) => item === '--target');
  const projectIndex = argv.findIndex((item) => item === '--project');
  let target = 'claude';
  let project = null;
  if (targetIndex >= 0 && argv[targetIndex + 1]) {
    target = argv[targetIndex + 1];
  }
  if (projectIndex >= 0 && argv[projectIndex + 1]) {
    project = path.resolve(argv[projectIndex + 1]);
  }
  return {
    target,
    project,
    force: argv.includes('--force'),
    dryRun: argv.includes('--dry-run'),
    printManifest: argv.includes('--print-manifest'),
    help: argv.includes('--help') || argv.includes('-h'),
  };
}

function loadManifest() {
  return JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf-8'));
}

function ensureDir(p, dryRun) {
  if (dryRun) return;
  fs.mkdirSync(p, { recursive: true });
}

function needsSkillFrontmatter(dest, sourceContent) {
  if (!dest.endsWith(`${path.sep}SKILL.md`) && path.basename(dest) !== 'SKILL.md') {
    return false;
  }
  if (sourceContent.startsWith('---\n') || sourceContent.startsWith('---\r\n')) {
    return false;
  }
  if (dest.includes(`${path.sep}.agents${path.sep}skills${path.sep}`)) {
    return true;
  }
  return FRONTMATTER_HOSTS.some((segment) => dest.includes(`${path.sep}${segment}${path.sep}`));
}

function inferSkillMetadata(src, content) {
  const skillDir = path.basename(path.dirname(src));
  const titleMatch = content.match(/^#\s+(.+)$/m);
  const title = titleMatch ? titleMatch[1].trim() : `${skillDir} skill`;
  const bodyLines = content.split(/\r?\n/);
  let description = '';
  for (const line of bodyLines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#') || trimmed === '---') {
      continue;
    }
    description = trimmed.replace(/\s+/g, ' ');
    break;
  }
  if (!description) {
    description = `PaperFit skill: ${title}`;
  }
  if (description.length > 140) {
    description = `${description.slice(0, 137).trimEnd()}...`;
  }
  return {
    name: skillDir,
    description,
  };
}

function renderSkillFrontmatter(src, content) {
  const meta = inferSkillMetadata(src, content);
  return [
    '---',
    `name: ${meta.name}`,
    `description: ${meta.description}`,
    '---',
    '',
    content,
  ].join('\n');
}

function materializeFileContent(src, dest) {
  const content = fs.readFileSync(src, 'utf-8');
  if (needsSkillFrontmatter(dest, content)) {
    return renderSkillFrontmatter(src, content);
  }
  return content;
}

function copyFile(src, dest, { force, dryRun }) {
  if (dryRun) {
    console.log(`  [dry-run] ${src} -> ${dest}`);
    return;
  }
  if (fs.existsSync(dest) && !force) {
    console.log(`  skip (exists): ${path.basename(dest)}`);
    return;
  }
  ensureDir(path.dirname(dest), false);
  const content = materializeFileContent(src, dest);
  fs.writeFileSync(dest, content, 'utf-8');
  console.log(`  ok: ${path.basename(dest)}`);
}

function copyTree(srcDir, destDir, { force, dryRun }) {
  if (!fs.existsSync(srcDir)) return;
  const walk = (rel = '') => {
    const cur = path.join(srcDir, rel);
    const st = fs.lstatSync(cur);
    const baseName = path.basename(cur);
    if (baseName === '__pycache__' || baseName === '.DS_Store') {
      return;
    }
    if (st.isDirectory()) {
      for (const name of fs.readdirSync(cur)) {
        const sub = rel ? `${rel}/${name}` : name;
        walk(sub);
      }
      return;
    }
    const dest = path.join(destDir, rel);
    copyFile(cur, dest, { force, dryRun });
  };
  walk();
}

function resolveRoot(rootEnvVar, rootSuffix) {
  const base = process.env[rootEnvVar] || HOME;
  return path.join(base, rootSuffix);
}

function applyCopies(rootDir, copies, options) {
  for (const entry of copies) {
    const source = path.join(PKG_ROOT, entry.source);
    const target = path.join(rootDir, entry.target);
    if (entry.type === 'directory') {
      ensureDir(target, options.dryRun);
      copyTree(source, target, options);
    } else if (entry.type === 'file') {
      copyFile(source, target, options);
    } else {
      throw new Error(`Unsupported copy type: ${entry.type}`);
    }
  }
}

function upsertCodexMarketplace(marketplacePath, options) {
  const nextMarketplace = fs.existsSync(marketplacePath)
    ? JSON.parse(fs.readFileSync(marketplacePath, 'utf-8'))
    : {
        name: 'local-personal',
        interface: { displayName: 'Local Personal Plugins' },
        plugins: [],
      };
  if (!Array.isArray(nextMarketplace.plugins)) {
    nextMarketplace.plugins = [];
  }
  const pluginIndex = nextMarketplace.plugins.findIndex((plugin) => plugin.name === 'paperfit');
  if (pluginIndex >= 0) {
    nextMarketplace.plugins[pluginIndex] = {
      ...nextMarketplace.plugins[pluginIndex],
      ...CODEX_MARKETPLACE_ENTRY,
    };
  } else {
    nextMarketplace.plugins.push(CODEX_MARKETPLACE_ENTRY);
  }
  if (options.dryRun) {
    console.log(`  [dry-run] merge ${marketplacePath}`);
    return;
  }
  ensureDir(path.dirname(marketplacePath), false);
  fs.writeFileSync(marketplacePath, JSON.stringify(nextMarketplace, null, 2), 'utf-8');
  console.log(`  ok: ${path.basename(marketplacePath)}`);
}

function installCodexOfficialSkills(options) {
  const sourceDir = path.join(PKG_ROOT, 'skills');
  const targetDir = path.join(HOME, '.agents', 'skills');
  console.log(`  codex-skills -> ${targetDir}`);
  ensureDir(targetDir, options.dryRun);
  copyTree(sourceDir, targetDir, options);
}

function installCodexPersonalMarketplace(codexRoot, options) {
  const pluginDir = path.join(codexRoot, 'plugins', 'paperfit');
  const marketplacePath = path.join(HOME, '.agents', 'plugins', 'marketplace.json');
  console.log(`  codex-plugin -> ${pluginDir}`);
  ensureDir(pluginDir, options.dryRun);
  copyTree(path.join(PKG_ROOT, 'plugins', 'paperfit'), pluginDir, options);
  upsertCodexMarketplace(marketplacePath, options);
}

function writeInstallManifest(targets, sharedRoot, options) {
  const manifest = {
    name: 'paperfit',
    version: require(path.join(PKG_ROOT, 'package.json')).version,
    packageRoot: PKG_ROOT,
    installedAt: new Date().toISOString(),
    targets,
    sharedRoot,
    projectRoot: options.project || null,
  };
  const manifestPath = path.join(sharedRoot, 'install-manifest.json');
  if (options.dryRun) {
    console.log(`\n[dry-run] would write ${manifestPath}`);
    return;
  }
  ensureDir(sharedRoot, false);
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2), 'utf-8');
  console.log(`\nWrote ${manifestPath}`);
}

function showHelp() {
  console.log(`
paperfit-install — install PaperFit assets into host homes

Usage:
  paperfit-install --target claude
  paperfit-install --target codex
  paperfit-install --target cursor
  paperfit-install --target all
  paperfit-install --target claude --force
  paperfit-install --target codex --dry-run
  paperfit-install --target cursor --project /path/to/paper
  paperfit-install --print-manifest
`);
}

function selfCheckPrompt(target) {
  if (target === 'claude') {
    return '用 PaperFit 分析这篇论文的排版问题';
  }
  if (target === 'codex') {
    return "Use the `paperfit` agent to inspect this paper's layout and tell me the main visual defects";
  }
  if (target === 'cursor') {
    return 'Use PaperFit to repair this paper layout with minimal semantic change';
  }
  return 'Use PaperFit to analyze this paper layout';
}

function printFirstRunGuidance(targets, projectPath) {
  console.log('First run:');
  console.log('  1. Open your host in the paper project root.');
  console.log('  2. Describe the goal naturally. Do not start by typing internal PaperFit commands.');
  console.log('');
  console.log('Self-check messages:');
  for (const target of targets) {
    const scope = target === 'cursor' && projectPath ? ` (${projectPath})` : '';
    console.log(`  ${target}${scope}: ${selfCheckPrompt(target)}`);
  }
  console.log('');
  if (targets.includes('codex')) {
    console.log('Codex entry points:');
    console.log(`  agents:       ${path.join(HOME, '.codex', 'agents')}`);
    console.log(`  skills:       ${path.join(HOME, '.agents', 'skills')}`);
    console.log(`  plugin:       ${path.join(HOME, '.codex', 'plugins', 'paperfit')}`);
    console.log(`  marketplace:  ${path.join(HOME, '.agents', 'plugins', 'marketplace.json')}`);
    console.log('  Restart Codex after install, then ask Codex to use the `paperfit` agent.');
    console.log('  `/agent` switches to an active PaperFit thread after it has been spawned.');
    console.log('');
  }
  console.log('If something fails, run `paperfit doctor --target <host>` from the paper project root.');
  console.log('Only drop to CLI/runtime/script details for debugging, recovery, or host setup problems.');
}

function main() {
  const opts = parseArgs();
  if (opts.help) {
    showHelp();
    process.exit(0);
  }

  const manifest = loadManifest();
  if (opts.printManifest) {
    console.log(JSON.stringify(manifest, null, 2));
    process.exit(0);
  }

  const allTargets = Object.keys(manifest.targets);
  const resolvedTargets = opts.target === 'all' ? allTargets : [opts.target];
  for (const target of resolvedTargets) {
    if (!manifest.targets[target]) {
      console.error(`Unsupported target: ${target}`);
      process.exit(1);
    }
  }

  const sharedRoot = resolveRoot(
    manifest.shared_copy.root_env_var,
    manifest.shared_copy.root_suffix,
  );

  console.log('\nPaperFit host installer\n');
  console.log(`Package: ${PKG_ROOT}`);
  console.log(`Targets: ${resolvedTargets.join(', ')}`);
  console.log(`Shared:  ${sharedRoot}\n`);

  for (const target of resolvedTargets) {
    const targetSpec = manifest.targets[target];
    const rootDir = resolveRoot(targetSpec.home_env_var, targetSpec.root_suffix);
    console.log(`${target} -> ${rootDir}`);
    ensureDir(rootDir, opts.dryRun);
    applyCopies(rootDir, targetSpec.copies, opts);
    if (target === 'codex') {
      installCodexOfficialSkills(opts);
      installCodexPersonalMarketplace(rootDir, opts);
    }
    console.log('');
  }

  if (opts.project) {
    console.log(`project -> ${opts.project}`);
    for (const target of resolvedTargets) {
      const targetSpec = manifest.targets[target];
      if (!Array.isArray(targetSpec.project_copies) || targetSpec.project_copies.length === 0) {
        continue;
      }
      console.log(`  applying ${target} project assets`);
      applyCopies(opts.project, targetSpec.project_copies, opts);
    }
    console.log('');
  } else if (resolvedTargets.includes('cursor')) {
    console.log('Cursor note: no --project provided, so project-local rule was not installed.');
    console.log('             Use --project /path/to/paper to write .cursor/rules/paperfit.mdc.\n');
  }

  console.log(`shared -> ${sharedRoot}`);
  ensureDir(sharedRoot, opts.dryRun);
  applyCopies(sharedRoot, manifest.shared_copy.copies, opts);
  writeInstallManifest(resolvedTargets, sharedRoot, opts);

  console.log(`\nDone.${opts.dryRun ? ' (dry-run; no files written)' : ''}\n`);
  printFirstRunGuidance(resolvedTargets, opts.project);
  console.log(`
Dependencies if needed:
  pip3 install -r "${path.join(PKG_ROOT, 'requirements.txt')}"
  brew install poppler
`);
}

main();
