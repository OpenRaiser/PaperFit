'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { spawnSync } = require('child_process');
const { copyFile, materializeFileContent } = require('./install-host-global');
const root = path.resolve(__dirname, '..');
const installer = path.join(root, 'scripts/install-host-global.js');

function fixture(t) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'paperfit-host-'));
  t.after(() => {
    const resolved = path.resolve(base);
    assert.ok(resolved.startsWith(path.resolve(os.tmpdir()) + path.sep));
    assert.ok(path.basename(resolved).startsWith('paperfit-host-'));
    fs.rmSync(resolved, { recursive: true, force: true });
  });
  const home = path.join(base, 'host home');
  const project = path.join(base, 'paper project');
  fs.mkdirSync(home); fs.mkdirSync(project);
  return { base, home, project };
}
function run(args, home, cwd) {
  return spawnSync(process.execPath, [installer, ...args], {
    cwd, env: { ...process.env, HOME: home }, encoding: 'utf8', windowsHide: true, timeout: 30000
  });
}
function write(file, text) { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, text); }
function inventory(directory) {
  const result = {};
  function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const file = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(file);
      else result[path.relative(directory, file)] = fs.readFileSync(file).toString('hex');
    }
  }
  walk(directory); return result;
}

test('project scope installs native entries for all hosts and leaves home unchanged', t => {
  const { home, project } = fixture(t);
  for (const name of ['.codex/config.toml', '.codex/AGENTS.md', '.agents/plugins/marketplace.json', '.agents/skills/other/SKILL.md']) {
    write(path.join(home, name), 'keep existing user configuration: ' + name);
  }
  const before = inventory(home);
  const result = run(['--target', 'all', '--scope', 'project'], home, project);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(inventory(home), before);
  for (const host of ['.agents', '.claude', '.cursor']) {
    const text = fs.readFileSync(path.join(project, host, 'skills/paperfit/SKILL.md'), 'utf8');
    assert.match(text, /^---\r?\nname: paperfit\r?\ndescription:/);
  }
  assert.ok(fs.existsSync(path.join(project, '.cursor/rules/paperfit.mdc')));
  assert.equal(JSON.parse(fs.readFileSync(path.join(project, '.paperfit/install-manifest.json'))).packageRoot, root);
  assert.ok(!fs.existsSync(path.join(project, '.codex/config.toml')));
});

test('global Codex uses the explicit isolated home and never replaces Codex policy', t => {
  const { home, project } = fixture(t);
  const otherHome = path.join(project, 'explicit home');
  fs.mkdirSync(otherHome);
  write(path.join(otherHome, '.codex/config.toml'), 'model = "user-model"');
  write(path.join(otherHome, '.codex/AGENTS.md'), 'user policy');
  const beforeHome = inventory(home);
  const result = run(['--target', 'codex', '--home', otherHome, '--force'], home, project);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(inventory(home), beforeHome);
  assert.ok(fs.existsSync(path.join(otherHome, '.agents/skills/paperfit/SKILL.md')));
  assert.equal(fs.readFileSync(path.join(otherHome, '.codex/AGENTS.md'), 'utf8'), 'user policy');
  assert.equal(fs.readFileSync(path.join(otherHome, '.codex/config.toml'), 'utf8'), 'model = "user-model"');
  assert.ok(!fs.existsSync(path.join(otherHome, '.agents/plugins/marketplace.json')));
});

test('project install preserves existing skill until explicit force', t => {
  const { home, project } = fixture(t);
  const skill = path.join(project, '.agents/skills/paperfit/SKILL.md');
  write(skill, 'local author instructions');
  let result = run(['--target', 'codex', '--scope', 'project', '--project', project], home, home);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.readFileSync(skill, 'utf8'), 'local author instructions');
  result = run(['--target', 'codex', '--scope', 'project', '--project', project, '--force'], home, home);
  assert.equal(result.status, 0, result.stderr);
  assert.match(fs.readFileSync(skill, 'utf8'), /^---\r?\nname: paperfit/);
});

test('dry run and invalid scope do not write files', t => {
  const { home, project } = fixture(t);
  const args = ['--target', 'all', '--scope', 'project', '--project', project, '--dry-run'];
  assert.equal(run(args, home, home).status, 0);
  assert.deepEqual(inventory(home), {});
  assert.deepEqual(inventory(project), {});
  const invalid = run(['--scope', 'wrong'], home, project);
  assert.equal(invalid.status, 1);
  assert.match(invalid.stderr, /Unsupported scope/);
  assert.deepEqual(inventory(project), {});
});

test('binary package assets remain byte-identical', t => {
  const { base } = fixture(t);
  const source = path.join(base, 'image.png');
  const destination = path.join(base, 'copy.png');
  const bytes = Buffer.from([0, 255, 137, 80, 78, 71, 13, 10, 0, 128]);
  fs.writeFileSync(source, bytes);
  copyFile(source, destination, { force: false, dryRun: false });
  assert.deepEqual(fs.readFileSync(destination), bytes);
});

test('legacy Markdown gains valid scalar metadata; canonical skills are unchanged', t => {
  const { base } = fixture(t);
  const source = path.join(base, 'legacy/SKILL.md');
  write(source, '# Width: "special" skill\n\nInstructions\n');
  const rendered = materializeFileContent(source, path.join(base, '.claude/skills/legacy/SKILL.md'));
  const scalar = rendered.split('\n').find(line => line.startsWith('description: ')).slice(13);
  assert.equal(JSON.parse(scalar), 'PaperFit Width: "special" skill. Use when the paper needs this layout expertise.');
  const canonical = path.join(root, 'skills/paperfit/SKILL.md');
  assert.equal(materializeFileContent(canonical, path.join(base, '.agents/skills/paperfit/SKILL.md')),
    fs.readFileSync(canonical, 'utf8'));
});

test('native plugin and npm pack contain skill and referenced tool/docs', t => {
  const plugin = JSON.parse(fs.readFileSync(path.join(root, 'plugin.json')));
  assert.equal(plugin.name, 'paperfit');
  const canonicalSkill = fs.readFileSync(path.join(root, 'skills/paperfit/SKILL.md'), 'utf8');
  assert.equal(fs.readFileSync(path.join(root, 'plugins/paperfit/skills/paperfit/SKILL.md'), 'utf8'), canonicalSkill);
  assert.equal(JSON.parse(fs.readFileSync(path.join(root, 'plugins/paperfit/.codex-plugin/plugin.json'))).skills, './skills/');
  const marketplace = JSON.parse(fs.readFileSync(path.join(root, '.agents/plugins/marketplace.json')));
  const entry = marketplace.plugins.find(item => item.name === 'paperfit');
  assert.equal(path.resolve(root, entry.source.path), root);
  const npm = process.platform === 'win32' ? 'cmd.exe' : 'npm';
  const args = process.platform === 'win32' ? ['/d', '/s', '/c', 'npm pack --dry-run --json --ignore-scripts']
    : ['pack', '--dry-run', '--json', '--ignore-scripts'];
  const packed = spawnSync(npm, args, {
    cwd: root, encoding: 'utf8', windowsHide: true, timeout: 120000
  });
  assert.equal(packed.status, 0, packed.stderr);
  const entries = new Set(JSON.parse(packed.stdout)[0].files.map(file => file.path));
  for (const file of ['plugin.json', 'skills/paperfit/SKILL.md', 'scripts/host_context.py',
    'docs/HOST_NATIVE_WORKFLOW.md', 'docs/DEEPSEEK_VISION_LAYOUT.md', 'agents/layout-detective-agent.md']) {
    assert.ok(entries.has(file), 'Missing npm resource: ' + file);
  }
});

test('CLI forwards project scope and doctor checks the installed entry without writing', t => {
  const { home, project } = fixture(t);
  const cli = path.join(root, 'bin/paperfit.js');
  const env = { ...process.env, HOME: home };
  const installed = spawnSync(process.execPath, [cli, 'install-global', '--target', 'codex',
    '--scope', 'project', '--project', project], { cwd: home, env, encoding: 'utf8', windowsHide: true });
  assert.equal(installed.status, 0, installed.stderr);
  assert.deepEqual(inventory(home), {});
  const before = inventory(project);
  const doctor = spawnSync(process.execPath, [cli, 'doctor', '--target', 'codex', '--project', project],
    { cwd: home, env, encoding: 'utf8', windowsHide: true, timeout: 30000 });
  assert.match(doctor.stdout, /✅ codex discoverable paperfit skill:/);
  assert.doesNotMatch(doctor.stdout, /legacy|marketplace|space-util-fixer/);
  assert.deepEqual(inventory(project), before);
});

test('missing configured Python reports startup error and selected interpreter', t => {
  const { home, project } = fixture(t);
  const missing = path.join(home, 'missing-python.exe');
  const result = spawnSync(process.execPath, [path.join(root, 'bin/paperfit.js'), 'run',
    'scripts/config_validator.py'], {
    cwd: project, env: { ...process.env, PAPERFIT_PYTHON: missing }, encoding: 'utf8', windowsHide: true
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /failed to start/);
  assert.ok(result.stderr.includes(missing));
  assert.match(result.stderr, /PAPERFIT_PYTHON/);
});
