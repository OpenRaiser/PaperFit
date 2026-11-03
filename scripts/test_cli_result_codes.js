'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { spawnSync } = require('child_process');
const root = path.resolve(__dirname, '..');

// A stub runner isolates CLI exit forwarding from Python packages and actual PDF checks.
for (const [code, status] of [[2, 'needs_review'], [3, 'blocked'], [1, 'checker_error']]) {
  test('completed child ' + status + ' forwards its result without an interpreter diagnosis', t => {
    const base = fs.mkdtempSync(path.join(os.tmpdir(), 'paperfit-cli-result-'));
    t.after(() => {
      const resolved = path.resolve(base);
      assert.ok(resolved.startsWith(path.resolve(os.tmpdir()) + path.sep));
      assert.ok(path.basename(resolved).startsWith('paperfit-cli-result-'));
      fs.rmSync(resolved, { recursive: true, force: true });
    });
    fs.mkdirSync(path.join(base, 'bin'));
    fs.mkdirSync(path.join(base, 'scripts'));
    fs.copyFileSync(path.join(root, 'bin/paperfit.js'), path.join(base, 'bin/paperfit.js'));
    fs.writeFileSync(path.join(base, 'scripts/result.py'),
      'console.log(' + JSON.stringify(JSON.stringify({status})) + '); process.exit(' + code + ');');
    const result = spawnSync(process.execPath, [path.join(base, 'bin/paperfit.js'),
      'run', 'scripts/result.py'], {
      cwd: base, env: {...process.env, PAPERFIT_PYTHON: process.execPath,
        NODE_PATH: path.join(root, 'node_modules')}, encoding: 'utf8', windowsHide: true
    });
    assert.equal(result.status, code, result.stderr);
    assert.equal(JSON.parse(result.stdout).status, status);
    assert.match(result.stderr, /Consult its output\/report/);
    assert.doesNotMatch(result.stderr, /failed|PAPERFIT_PYTHON|installed requirements/);
  });
}
