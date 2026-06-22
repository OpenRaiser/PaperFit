#!/usr/bin/env node

/**
 * PaperFit CLI - Visual Typesetting Optimization Agent System
 *
 * Usage:
 *   paperfit init          Initialize PaperFit in current directory
 *   paperfit install       Install components interactively
 *   paperfit status        Show current status
 *   paperfit doctor        Check installation health
 *   /paperfit（Claude）    见 .claude/commands/paperfit.md：问卷 + paperfit_portrait.py 扫描画像
 *   paperfit（Codex agent）通过 ~/.codex/agents/paperfit.toml 作为自定义 agent 入口
 *   paperfit wizard        终端 TUI：会刊/模板、页数口径等 → data/paperfit-project.yaml + state
 *   paperfit upgrade       一键：npm 全局最新 paperfit-cli + install-global（支持 --target）
 *   paperfit render <pdf>  Run bundled render_pages.py (from your project cwd)
 *   paperfit run scripts/… Run bundled .py/.sh under package scripts/ (cwd = project)
 *   paperfit runtime …     Run executable orchestrator state transitions
 *   e.g. scripts/detect_column_void.py — OpenCV 双栏列内竖向空洞（A5 辅助）
 */

const { program } = require('commander');
const { execSync, spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');

let version = '0.0.0';
try {
  version = require(path.join(__dirname, '..', 'package.json')).version;
} catch (_) {
  /* ignore */
}

/** npm 包根目录（全局安装时为 .../node_modules/paperfit-cli） */
function packageRoot() {
  return path.resolve(path.join(__dirname, '..'));
}

function pythonRunner() {
  const venvPython = path.join(packageRoot(), '.venv', 'bin', 'python');
  return fs.existsSync(venvPython) ? venvPython : 'python3';
}

function installTargetsManifestPath() {
  return path.join(packageRoot(), 'config', 'install_targets.json');
}

function loadInstallTargetsManifest() {
  return JSON.parse(fs.readFileSync(installTargetsManifestPath(), 'utf-8'));
}

function resolveHostRoot(rootEnvVar, rootSuffix) {
  const base = process.env[rootEnvVar] || process.env.HOME || os.homedir();
  return path.join(base, rootSuffix);
}

function isIgnoredInstallEntry(name) {
  return name === '__pycache__' || name === '.DS_Store';
}

function directoryHasVisibleContent(dir) {
  if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) {
    return false;
  }
  const queue = [dir];
  while (queue.length > 0) {
    const current = queue.pop();
    const entries = fs.readdirSync(current, { withFileTypes: true });
    for (const entry of entries) {
      if (isIgnoredInstallEntry(entry.name)) {
        continue;
      }
      const full = path.join(current, entry.name);
      if (entry.isFile()) {
        return true;
      }
      if (entry.isDirectory()) {
        queue.push(full);
      }
    }
  }
  return false;
}

function safeReadJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf-8'));
  } catch (_) {
    return null;
  }
}

function resolveProjectRoot(projectOption) {
  return projectOption ? path.resolve(projectOption) : null;
}

function parseCodexConfigSummary(configPath) {
  if (!fs.existsSync(configPath) || !fs.statSync(configPath).isFile()) {
    return null;
  }
  const content = fs.readFileSync(configPath, 'utf-8');
  const providerMatch = content.match(/^\s*model_provider\s*=\s*"([^"]+)"/m);
  const modelMatch = content.match(/^\s*model\s*=\s*"([^"]+)"/m);
  const baseUrlMatch = content.match(/^\s*base_url\s*=\s*"([^"]+)"/m);
  return {
    modelProvider: providerMatch ? providerMatch[1] : null,
    model: modelMatch ? modelMatch[1] : null,
    baseUrl: baseUrlMatch ? baseUrlMatch[1] : null,
  };
}

function codexLegacySkillDir(codexRoot) {
  return path.join(codexRoot, 'skills', 'paperfit');
}

function codexOfficialSkillsDir() {
  return path.join(os.homedir(), '.agents', 'skills');
}

function codexPersonalMarketplacePath() {
  return path.join(os.homedir(), '.agents', 'plugins', 'marketplace.json');
}

function codexPluginDir(codexRoot) {
  return path.join(codexRoot, 'plugins', 'paperfit');
}

function forwardBundledPythonScript(commandName, scriptRelativePath) {
  const script = path.join(packageRoot(), scriptRelativePath);
  if (!fs.existsSync(script)) {
    console.error('PaperFit: 找不到包内脚本', script);
    process.exit(1);
  }
  const argv = process.argv;
  const i = argv.indexOf(commandName);
  const forward = i >= 0 ? argv.slice(i + 1) : [];
  const r = spawnSync(pythonRunner(), [script, commandName, ...forward], {
    stdio: 'inherit',
    cwd: process.cwd(),
    env: process.env,
  });
  process.exit(r.status === null ? 1 : (r.status ?? 1));
}

function addWorkflowOptions(command, { includeSaveAs = false } = {}) {
  command
    .option('--main <path>', '主 .tex 文件')
    .option('--template <template>', '模板键，如 NeurIPS2025')
    .option('--target-pages <n>', '目标页数')
    .option('--page-budget <scope>', '页数口径: main_body | with_refs | with_appendix')
    .option('--strict', '严格模式')
    .option('--max-rounds <n>', '最大轮次')
    .option('--apply', '允许 typed fix-layout 执行源码修改；默认只 dry-run');
  if (includeSaveAs) {
    command.option('--save-as <dir>', '另存为目录');
  }
  return command;
}

function runtimeStatusArgs(options = {}) {
  const args = ['--state', options.state || 'data/state.json', 'status-view'];
  if (options.runResult) {
    args.push('--run-result', options.runResult);
  }
  return args;
}

function loadRuntimeStatusView(options = {}) {
  const script = path.join(packageRoot(), 'scripts', 'orchestrator_runtime.py');
  if (!fs.existsSync(script)) {
    console.error('未找到脚本:', script);
    process.exit(1);
  }
  const r = spawnSync(pythonRunner(), [script, ...runtimeStatusArgs(options)], {
    cwd: process.cwd(),
    env: process.env,
    encoding: 'utf-8',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  if (r.status !== 0) {
    if (r.stderr) process.stderr.write(r.stderr);
    if (r.stdout) process.stdout.write(r.stdout);
    process.exit(r.status === null ? 1 : r.status ?? 1);
  }
  try {
    return JSON.parse(r.stdout || '{}');
  } catch (error) {
    console.error('PaperFit: failed to parse runtime status JSON');
    if (r.stdout) process.stderr.write(r.stdout);
    process.exit(1);
  }
}

function printRuntimeStatusSummary(status) {
  const runtime = status.runtime || {};
  const artifacts = status.artifacts || {};
  const repair = status.repair || {};
  const approval = status.approval || {};
  const repairLoop = status.repair_loop_policy || {};
  const roundArtifactLineage = Array.isArray(status.round_artifact_lineage) ? status.round_artifact_lineage : [];
  const freshness = status.artifact_freshness || {};
  const defects = status.defect_summary || {};

  console.log('📊 PaperFit Status\n');
  if (status.main_tex) console.log(`  Main TeX: ${status.main_tex}`);
  if (status.task_type) console.log(`  Task: ${status.task_type}`);
  console.log(`  Status: ${status.status || 'UNKNOWN'}`);
  if (status.gatekeeper_decision) console.log(`  Gatekeeper: ${status.gatekeeper_decision}`);
  if (status.run_result_path) console.log(`  RunResult: ${status.run_result_path}`);
  if (freshness.status) console.log(`  Artifact Freshness: ${freshness.status}`);

  if (
    defects.initial_total != null ||
    defects.resolved != null ||
    defects.remaining != null
  ) {
    console.log('\n  缺陷摘要');
    console.log(`    Initial: ${defects.initial_total ?? 0}`);
    console.log(`    Resolved: ${defects.resolved ?? 0}`);
    console.log(`    Remaining: ${defects.remaining ?? 0}`);
  }

  if (runtime.run_id || runtime.event_count || runtime.event_log) {
    console.log('\n  Runtime');
    if (runtime.run_id) console.log(`    Run ID: ${runtime.run_id}`);
    if (runtime.event_log) console.log(`    Event Log: ${runtime.event_log}`);
    if (runtime.event_count != null) console.log(`    Events: ${runtime.event_count}`);
    if (runtime.last_runtime_state) console.log(`    Last State: ${runtime.last_runtime_state}`);
  }

  if (
    repair.plan_candidates ||
    repair.execution_status ||
    repair.skipped ||
    repair.requires_approval != null
  ) {
    console.log('\n  修复计划');
    if (repair.plan_candidates != null) console.log(`    Candidates: ${repair.plan_candidates}`);
    if (repair.execution_status) console.log(`    Execution: ${repair.execution_status}`);
    if (repair.applied_count != null) console.log(`    Applied: ${repair.applied_count}`);
    if (repair.skipped) console.log(`    Skipped: ${repair.skip_reason || true}`);
    if (repair.risk_level) console.log(`    Risk: ${repair.risk_level}`);
    if (repair.requires_approval != null) console.log(`    Requires Approval: ${repair.requires_approval}`);
  }

  if (approval.status && approval.status !== 'not_applicable') {
    console.log('\n  Approval');
    console.log(`    Status: ${approval.status}`);
    if (approval.reason) console.log(`    Reason: ${approval.reason}`);
    if (approval.risk_level) console.log(`    Risk: ${approval.risk_level}`);
    if (approval.approval_granted != null) console.log(`    Granted: ${approval.approval_granted}`);
    if (Array.isArray(approval.approval_mechanisms) && approval.approval_mechanisms.length > 0) {
      console.log(`    Mechanisms: ${approval.approval_mechanisms.join(', ')}`);
    }
  }

  if (repairLoop.schema_version) {
    console.log('\n  Repair Loop Policy');
    if (repairLoop.execution_mode) console.log(`    Mode: ${repairLoop.execution_mode}`);
    if (repairLoop.round_limit != null) console.log(`    Round Limit: ${repairLoop.round_limit}`);
    if (repairLoop.candidate_batch_limit != null) console.log(`    Candidate Batch Limit: ${repairLoop.candidate_batch_limit}`);
    if (repairLoop.stop_condition) console.log(`    Stop: ${repairLoop.stop_condition}`);
    if (repairLoop.next_round_allowed != null) console.log(`    Next Round Allowed: ${repairLoop.next_round_allowed}`);
    if (repairLoop.next_round_reason) console.log(`    Reason: ${repairLoop.next_round_reason}`);
    if (repairLoop.approval_scope_carry_forward?.status) {
      console.log(`    Approval Carry-forward: ${repairLoop.approval_scope_carry_forward.status}`);
    }
  }

  if (roundArtifactLineage.length > 0) {
    const actionCount = roundArtifactLineage.reduce((total, item) => {
      const actions = item?.actions || {};
      return total + Object.keys(actions).length;
    }, 0);
    console.log('\n  Artifact Lineage');
    console.log(`    Rounds: ${roundArtifactLineage.length}`);
    console.log(`    Actions: ${actionCount}`);
  }

  if (status.terminal_success_guard) {
    console.log('\n  Terminal Guard');
    console.log(`    Status: ${status.terminal_success_guard.status || 'blocked'}`);
    if (status.terminal_success_guard.failure_type) {
      console.log(`    Failure: ${status.terminal_success_guard.failure_type}`);
    }
  }

  const reportEntries = [
    ['Task Spec', artifacts.task_spec],
    ['Pages', artifacts.page_images_dir],
    ['Visual Signals', artifacts.visual_signal_report],
    ['Defect Report', artifacts.defect_report],
    ['Repair Plan', artifacts.repair_plan],
    ['Repair Execution', artifacts.repair_execution_report],
    ['Rollback Report', artifacts.rollback_report],
    ['Source Mutation', artifacts.source_mutation_report],
  ].filter(([, value]) => Boolean(value));
  if (reportEntries.length > 0) {
    console.log('\n  Reports');
    reportEntries.forEach(([label, value]) => {
      console.log(`    ${label}: ${value}`);
    });
  }

  if (Array.isArray(status.next_actions) && status.next_actions.length > 0) {
    console.log('\n  Next Actions');
    status.next_actions.forEach((action, index) => {
      console.log(`    ${index + 1}. ${action}`);
    });
  }
}

program
  .name('paperfit')
  .version(version)
  .description('Visual Typesetting Optimization Agent System for LaTeX papers');

// render — 始终调用包内 scripts/render_pages.py，勿在用户项目里假设存在 scripts/
program
  .command('render')
  .description('将 PDF 渲染为逐页 PNG（调用包内 render_pages.py；请在论文目录下执行）')
  .allowUnknownOption(true)
  .action(() => {
    const script = path.join(packageRoot(), 'scripts', 'render_pages.py');
    if (!fs.existsSync(script)) {
      console.error('PaperFit: 找不到包内脚本', script);
      process.exit(1);
    }
    const argv = process.argv;
    const i = argv.indexOf('render');
    const forward = i >= 0 ? argv.slice(i + 1) : [];
    const r = spawnSync(pythonRunner(), [script, ...forward], {
      stdio: 'inherit',
      cwd: process.cwd(),
    });
    process.exit(r.status === null ? 1 : r.status);
  });

program
  .command('root')
  .description('打印 PaperFit 包根目录（供调试）')
  .action(() => {
    console.log(packageRoot());
  });

program
  .command('runtime')
  .description('运行包内 orchestrator_runtime.py（用于关键状态跃迁）')
  .allowUnknownOption(true)
  .action(() => {
    const script = path.join(packageRoot(), 'scripts', 'orchestrator_runtime.py');
    if (!fs.existsSync(script)) {
      console.error('未找到脚本:', script);
      process.exit(1);
    }
    const argv = process.argv;
    const i = argv.indexOf('runtime');
    const forward = i >= 0 ? argv.slice(i + 1) : [];
    const r = spawnSync(pythonRunner(), [script, ...forward], {
      stdio: 'inherit',
      cwd: process.cwd(),
      env: process.env,
    });
    process.exit(r.status === null ? 1 : r.status ?? 1);
  });

program
  .command('status-view')
  .description('Print compact Agent V1 runtime status JSON')
  .option('--state <path>', 'State file path', 'data/state.json')
  .option('--run-result <path>', 'RunResult JSON path to summarize')
  .action((options) => {
    console.log(JSON.stringify(loadRuntimeStatusView(options), null, 2));
  });

addWorkflowOptions(
  program
    .command('slash')
    .description('执行可复用的 PaperFit slash 语义（如 Claude `/paperfit`；也可用于 legacy prompt 兼容）')
    .argument('<request...>')
    .allowUnknownOption(true)
    .allowExcessArguments(true),
  { includeSaveAs: true },
)
  .action(() => {
    forwardBundledPythonScript('slash', path.join('scripts', 'paperfit_command.py'));
  });

addWorkflowOptions(
  program
    .command('run-agent')
    .description('执行 PaperFit Agent V1 自然语言主流程')
    .argument('<request...>')
    .allowUnknownOption(true)
    .allowExcessArguments(true),
  { includeSaveAs: true },
)
  .action(() => {
    forwardBundledPythonScript('run-agent', path.join('scripts', 'paperfit_command.py'));
  });

addWorkflowOptions(
  program
    .command('fix-layout')
    .description('执行可执行版 /fix-layout 完整闭环')
    .allowUnknownOption(true)
    .allowExcessArguments(true),
)
  .action(() => {
    forwardBundledPythonScript('fix-layout', path.join('scripts', 'paperfit_command.py'));
  });

addWorkflowOptions(
  program
    .command('check-visual')
    .description('执行可执行版 /check-visual 视觉检测')
    .allowUnknownOption(true)
    .allowExcessArguments(true),
)
  .action(() => {
    forwardBundledPythonScript('check-visual', path.join('scripts', 'paperfit_command.py'));
  });

addWorkflowOptions(
  program
    .command('migrate-template')
    .description('执行可执行版模板迁移，并可继续进入修复闭环')
    .argument('<target_template>')
    .allowUnknownOption(true)
    .allowExcessArguments(true),
  { includeSaveAs: true },
)
  .action(() => {
    forwardBundledPythonScript('migrate-template', path.join('scripts', 'paperfit_command.py'));
  });

// run — 包内 scripts/ 下的工具；工作目录为当前目录（论文项目根）
program
  .command('run')
  .description('运行包内 scripts/ 下的脚本（cwd 为当前目录；路径相对于包根）')
  .allowUnknownOption(true)
  .action(() => {
    const argv = process.argv;
    const i = argv.indexOf('run');
    const rest = i >= 0 ? argv.slice(i + 1) : [];
    const sepIndex = rest.indexOf('--');
    const beforeSep = sepIndex >= 0 ? rest.slice(0, sepIndex) : rest;
    const afterSep = sepIndex >= 0 ? rest.slice(sepIndex + 1) : [];
    let relScript;
    let forward;
    if (sepIndex >= 0) {
      if (beforeSep.length !== 1) {
        console.error('paperfit run: 使用 -- 时格式为: paperfit run scripts/foo.py -- arg1 arg2');
        process.exit(1);
      }
      relScript = beforeSep[0];
      forward = afterSep;
    } else {
      if (rest.length === 0) {
        console.error('用法: paperfit run scripts/parse_log.py compile.log');
        console.error('      paperfit run scripts/compile.sh');
        console.error('      paperfit run scripts/foo.py -- --flag value');
        process.exit(1);
      }
      relScript = rest[0];
      forward = rest.slice(1);
    }
    const norm = path.normalize(relScript).replace(/\\/g, '/');
    if (!norm.startsWith('scripts/') || norm.includes('..')) {
      console.error('paperfit run: 仅允许包内 scripts/ 下路径，且不得含 ..');
      process.exit(1);
    }
    const full = path.join(packageRoot(), norm);
    if (!fs.existsSync(full)) {
      console.error('未找到脚本:', full);
      process.exit(1);
    }
    const ext = path.extname(full);
    const runner = ext === '.py' ? pythonRunner() : ext === '.sh' ? 'bash' : null;
    if (!runner) {
      console.error('paperfit run: 仅支持 .py 与 .sh');
      process.exit(1);
    }
    const r = spawnSync(runner, [full, ...forward], {
      stdio: 'inherit',
      cwd: process.cwd(),
      env: process.env,
    });
    process.exit(r.status === null ? 1 : r.status ?? 1);
  });

// install-global — same as paperfit-install CLI
program
  .command('install-global')
  .description('Install PaperFit assets into a host home (claude/codex/cursor/all)')
  .option('--target <target>', 'Host target: claude | codex | cursor | all', 'claude')
  .option('--project <path>', 'Optional project root for host-specific project assets')
  .option('--force', 'Overwrite existing files')
  .option('--dry-run', 'Print planned copies only')
  .action((options) => {
    const script = path.join(__dirname, '..', 'scripts', 'install-host-global.js');
    const args = [script, '--target', options.target];
    if (options.project) args.push('--project', path.resolve(options.project));
    if (options.force) args.push('--force');
    if (options.dryRun) args.push('--dry-run');
    const r = spawnSync(process.execPath, args, { stdio: 'inherit' });
    process.exit(r.status ?? 1);
  });

program
  .command('upgrade')
  .description('一键更新：npm 全局安装最新 paperfit-cli，并执行 install-global 同步目标宿主')
  .option(
    '--local',
    '使用当前工作目录作为 npm 包路径安装（在克隆根目录执行，等价 npm install -g .）',
  )
  .option('--target <target>', 'Host target: claude | codex | cursor | all', 'claude')
  .option('--project <path>', 'Optional project root for host-specific project assets')
  .action((options) => {
    const pkgRoot = packageRoot();
    const spec = options.local ? process.cwd() : 'paperfit-cli@latest';
    console.log('📦 PaperFit upgrade: npm install -g', spec, '\n');
    const r1 = spawnSync('npm', ['install', '-g', spec], {
      stdio: 'inherit',
      shell: true,
      env: process.env,
    });
    if (r1.status !== 0) process.exit(r1.status === null ? 1 : r1.status);
    const script = path.join(pkgRoot, 'scripts', 'install-host-global.js');
    const installArgs = [script, '--target', options.target];
    if (options.project) installArgs.push('--project', path.resolve(options.project));
    const r2 = spawnSync(process.execPath, installArgs, { stdio: 'inherit' });
    if (r2.status !== 0) process.exit(r2.status === null ? 1 : r2.status);
    console.log(`\n✅ upgrade 完成。target=${options.target}`);
    if (options.target === 'claude' || options.target === 'all') {
      console.log('Claude 插件请另外在会话中执行：');
      console.log('   /plugin marketplace update paperfit-vto');
      console.log('   /plugin update paperfit@paperfit-vto\n');
    }
    process.exit(0);
  });

// init command
program
  .command('init')
  .description('Initialize PaperFit in current directory')
  .option('--interactive', 'Run interactive setup wizard')
  .action((options) => {
    console.log('🚀 Initializing PaperFit...\n');

    const targetDir = process.cwd();
    const scriptsDir = path.join(__dirname, '..', 'scripts');

    // Check if Python is available
    try {
      execSync('python3 --version', { stdio: 'ignore' });
      console.log('✅ Python 3 detected');
    } catch (e) {
      console.log('❌ Python 3 not found. Please install Python 3.8+');
      process.exit(1);
    }

    // Check if poppler is available (for pdf2image)
    try {
      execSync('which pdfinfo', { stdio: 'ignore' });
      console.log('✅ Poppler utilities detected');
    } catch (e) {
      console.log('⚠️  Poppler not found. Install with: brew install poppler');
    }

    // Check if latexmk is available
    try {
      execSync('which latexmk', { stdio: 'ignore' });
      console.log('✅ latexmk detected');
    } catch (e) {
      console.log('⚠️  latexmk not found. Install MacTeX or TeX Live');
    }

    console.log('\n✅ PaperFit initialized successfully!');
    console.log('\nNext steps:');
    console.log('  1. Open your LaTeX project in Claude Code');
    console.log('  2. Run: /fix-layout to start VTO optimization');
    console.log('  3. Run: /show-status to check current status');

    if (options.interactive) {
      console.log('\n📖 Launching interactive setup...');
      const setupScript = path.join(scriptsDir, 'configure_wizard.py');
      if (fs.existsSync(setupScript)) {
        const env = { ...process.env, PAPERFIT_PACKAGE_ROOT: path.join(__dirname, '..') };
        execSync(`${pythonRunner()} ${setupScript}`, { stdio: 'inherit', env });
      } else {
        console.log('configure_wizard.py not found. Skipping interactive setup.');
      }
    }
  });

program
  .command('wizard')
  .description('终端交互式论文画像：会刊/模板、页数口径、栏型 → data/paperfit-project.yaml 并 init state')
  .action(() => {
    const setupScript = path.join(__dirname, '..', 'scripts', 'configure_wizard.py');
    if (!fs.existsSync(setupScript)) {
      console.error('未找到', setupScript);
      process.exit(1);
    }
    const env = { ...process.env, PAPERFIT_PACKAGE_ROOT: path.join(__dirname, '..') };
    const r = spawnSync(pythonRunner(), [setupScript], { stdio: 'inherit', env, cwd: process.cwd() });
    process.exit(r.status === null ? 1 : r.status ?? 1);
  });

// install command
program
  .command('install [components...]')
  .description('Install PaperFit components')
  .option('--all', 'Install all components')
  .option('--target <target>', 'Host target: claude | codex | cursor | all', 'claude')
  .option('--project <path>', 'Optional project root for host-specific project assets')
  .action((components, options) => {
    console.log('📦 Installing components...\n');

    const installScript = path.join(__dirname, '..', 'install.sh');
    if (fs.existsSync(installScript)) {
      const installArgs = [installScript, '--target', options.target];
      if (options.project) installArgs.push('--project', path.resolve(options.project));
      const result = spawnSync('bash', installArgs, {
        stdio: 'inherit',
        cwd: process.cwd(),
        env: process.env,
      });
      if (result.status === 0) {
        console.log('✅ Installation complete!');
      } else {
        console.log('❌ Installation failed. Check logs for details.');
        process.exit(1);
      }
    } else {
      console.log('Install script not found.');
    }
  });

// status command
program
  .command('status')
  .description('Show current PaperFit status')
  .option('--state <path>', 'State file path', 'data/state.json')
  .option('--run-result <path>', 'RunResult JSON path to summarize')
  .action((options) => {
    const stateFile = path.resolve(process.cwd(), options.state || 'data/state.json');
    if (fs.existsSync(stateFile)) {
      printRuntimeStatusSummary(loadRuntimeStatusView(options));
    } else {
      console.log('📊 PaperFit Status: Not initialized');
      console.log('Run "paperfit init" to initialize.');
    }
  });

// doctor command
program
  .command('doctor')
  .description('Check installation health')
  .option('--target <target>', 'Host target: claude | codex | cursor | all', 'claude')
  .option('--project <path>', 'Optional project root for host-specific project assets')
  .action((options) => {
    console.log('🔍 Running health checks...\n');

    const installTargets = loadInstallTargetsManifest();
    const knownTargets = Object.keys(installTargets.targets);
    const resolvedTargets = options.target === 'all' ? knownTargets : [options.target];
    for (const target of resolvedTargets) {
      if (!installTargets.targets[target]) {
        console.error(`Unsupported target: ${target}`);
        process.exit(1);
      }
    }

    const checks = [
      { name: 'Python 3', command: 'python3 --version' },
      { name: 'pip3', command: 'pip3 --version' },
      { name: 'latexmk', command: 'which latexmk' },
      { name: 'pdfinfo (poppler)', command: 'which pdfinfo' },
    ];
    if (resolvedTargets.includes('claude')) {
      checks.push({ name: 'Claude Code CLI', command: 'claude --version' });
    }
    const projectRoot = resolveProjectRoot(options.project);

    let passed = 0;
    let failed = 0;

    console.log(`Targets: ${resolvedTargets.join(', ')}\n`);
    console.log('Dependencies');
    checks.forEach(check => {
      try {
        execSync(check.command, { stdio: 'ignore', timeout: 5000 });
        console.log(`✅ ${check.name}`);
        passed++;
      } catch (e) {
        console.log(`❌ ${check.name}`);
        failed++;
      }
    });

    const sharedRoot = resolveHostRoot(
      installTargets.shared_copy.root_env_var,
      installTargets.shared_copy.root_suffix,
    );
    const globalInstallManifestPath = path.join(sharedRoot, 'install-manifest.json');
    const globalInstallManifest = safeReadJson(globalInstallManifestPath);

    console.log('\nInstall Manifest');
    if (globalInstallManifest) {
      console.log(`✅ install-manifest.json`);
      passed++;
      const manifestVersion = globalInstallManifest.version || 'unknown';
      if (manifestVersion === version) {
        console.log(`✅ manifest version matches package: ${manifestVersion}`);
        passed++;
      } else {
        console.log(`❌ manifest version mismatch: installed=${manifestVersion} current=${version}`);
        failed++;
      }
      const manifestTargets = Array.isArray(globalInstallManifest.targets)
        ? globalInstallManifest.targets
        : [];
      const missingManifestTargets = resolvedTargets.filter((target) => !manifestTargets.includes(target));
      if (missingManifestTargets.length === 0) {
        console.log(`✅ requested targets recorded: ${resolvedTargets.join(', ')}`);
        passed++;
      } else {
        console.log(`❌ requested targets recorded: missing ${missingManifestTargets.join(', ')}`);
        failed++;
      }
      console.log(`   shared=${sharedRoot}`);
    } else {
      console.log(`❌ install-manifest.json`);
      console.log(`   expected at ${globalInstallManifestPath}`);
      failed++;
    }

    console.log('\nShared Assets');
    for (const entry of installTargets.shared_copy.copies) {
      const sharedPath = path.join(sharedRoot, entry.target);
      const ok = entry.type === 'directory'
        ? directoryHasVisibleContent(sharedPath)
        : fs.existsSync(sharedPath) && fs.statSync(sharedPath).isFile();
      console.log(`${ok ? '✅' : '❌'} ${entry.target}`);
      if (ok) {
        passed++;
      } else {
        failed++;
      }
    }

    console.log('\nHost Assets');
    for (const target of resolvedTargets) {
      const targetSpec = installTargets.targets[target];
      const rootDir = resolveHostRoot(targetSpec.home_env_var, targetSpec.root_suffix);
      console.log(`${target} -> ${rootDir}`);

      if (fs.existsSync(rootDir) && fs.statSync(rootDir).isDirectory()) {
        console.log(`  ✅ root directory`);
        passed++;
      } else {
        console.log(`  ❌ root directory`);
        failed++;
      }

      for (const entry of targetSpec.copies) {
        const installedPath = path.join(rootDir, entry.target);
        let ok = false;
        if (entry.type === 'directory') {
          ok = directoryHasVisibleContent(installedPath);
        } else if (entry.type === 'file') {
          ok = fs.existsSync(installedPath) && fs.statSync(installedPath).isFile();
        }
        console.log(`  ${ok ? '✅' : '❌'} ${entry.target}`);
        if (ok) {
          passed++;
        } else {
          failed++;
        }
      }
      if (target === 'codex') {
        const codexConfigPath = path.join(rootDir, 'config.toml');
        const codexConfig = parseCodexConfigSummary(codexConfigPath);
        if (codexConfig) {
          console.log(`  ✅ config.toml`);
          passed++;
          const providerLabel = codexConfig.modelProvider || 'default';
          const modelLabel = codexConfig.model || 'default';
          const baseUrlLabel = codexConfig.baseUrl || 'platform default';
          console.log(`     provider=${providerLabel} model=${modelLabel} base_url=${baseUrlLabel}`);
        } else {
          console.log(`  ⚠️ config.toml not found; Codex will rely on its own default login/provider`);
        }
        console.log('  Codex extra assets');
        const officialSkillPath = path.join(codexOfficialSkillsDir(), 'space-util-fixer', 'SKILL.md');
        const officialSkillsOk =
          fs.existsSync(officialSkillPath) && fs.statSync(officialSkillPath).isFile();
        console.log(`  ${officialSkillsOk ? '✅' : '❌'} ${officialSkillPath}`);
        if (officialSkillsOk) {
          passed++;
        } else {
          failed++;
        }

        const legacySkillPath = path.join(codexLegacySkillDir(rootDir), 'space-util-fixer', 'SKILL.md');
        const legacySkillsOk =
          fs.existsSync(legacySkillPath) && fs.statSync(legacySkillPath).isFile();
        console.log(`  ${legacySkillsOk ? '✅' : '❌'} ${legacySkillPath}`);
        if (legacySkillsOk) {
          passed++;
        } else {
          failed++;
        }

        const marketplacePath = codexPersonalMarketplacePath();
        const marketplaceJson = safeReadJson(marketplacePath);
        const marketplaceOk =
          marketplaceJson &&
          Array.isArray(marketplaceJson.plugins) &&
          marketplaceJson.plugins.some((plugin) => plugin.name === 'paperfit');
        console.log(`  ${marketplaceOk ? '✅' : '❌'} ${marketplacePath}`);
        if (marketplaceOk) {
          passed++;
        } else {
          failed++;
        }

        const pluginManifestPath = path.join(codexPluginDir(rootDir), '.codex-plugin', 'plugin.json');
        const pluginOk =
          fs.existsSync(pluginManifestPath) && fs.statSync(pluginManifestPath).isFile();
        console.log(`  ${pluginOk ? '✅' : '❌'} ${pluginManifestPath}`);
        if (pluginOk) {
          passed++;
        } else {
          failed++;
        }
      }
      console.log('');
    }

    if (projectRoot) {
      console.log(`Project Assets -> ${projectRoot}`);
      for (const target of resolvedTargets) {
        const targetSpec = installTargets.targets[target];
        if (!Array.isArray(targetSpec.project_copies) || targetSpec.project_copies.length === 0) {
          continue;
        }
        for (const entry of targetSpec.project_copies) {
          const installedPath = path.join(projectRoot, entry.target);
          const ok = entry.type === 'directory'
            ? directoryHasVisibleContent(installedPath)
            : fs.existsSync(installedPath) && fs.statSync(installedPath).isFile();
          console.log(`  ${ok ? '✅' : '❌'} ${target}:${entry.target}`);
          if (ok) {
            passed++;
          } else {
            failed++;
          }
        }
      }
      console.log('');
    } else if (resolvedTargets.includes('cursor')) {
      console.log('Project Assets');
      console.log('ℹ️ cursor project rules not checked; pass --project /path/to/paper');
    }

    console.log(`\n${passed}/${passed + failed} checks passed`);

    if (failed > 0) {
      console.log('\n💡 Install missing dependencies:');
      console.log('   brew install poppler mactex');
      console.log('   pip3 install -r requirements.txt');
      console.log(`   paperfit install-global --target ${options.target} --force`);
    }
  });

program.parse();
