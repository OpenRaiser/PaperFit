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
 *   paperfit（Codex skill）入口 ~/.agents/skills/paperfit/SKILL.md
 *   paperfit wizard        终端 TUI：会刊/模板、页数口径等 → data/paperfit-project.yaml + state
 *   paperfit upgrade       一键：npm 全局最新 paperfit-cli + install-global（支持 --target）
 *   paperfit render <pdf>  Run bundled render_pages.py (from your project cwd)
 *   paperfit run scripts/… Run bundled .py/.sh under package scripts/ (cwd = project)
 *   paperfit runtime …     Run executable orchestrator state transitions
 *   paperfit monitor       Start the read-only PaperFit Observatory web UI
 *   e.g. scripts/detect_column_void.py — OpenCV 双栏列内竖向空洞（A5 辅助）
 */

const { program } = require('commander');
const { execSync: executeCommandSync, spawnSync: spawnProcessSync } = require('child_process');

// Keep subprocess output in the calling host without opening extra Windows consoles.
function execSync(command, options = {}) {
  return executeCommandSync(command, { ...options, windowsHide: true });
}

function spawnSync(command, args, options = {}) {
  const result = spawnProcessSync(command, args, { ...options, windowsHide: true });
  if (result.error && options.stdio !== 'ignore') {
    console.error('PaperFit: failed to start "' + command + '": ' + result.error.message);
  }
  return result;
}
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

let cachedPythonRunner;
function pythonRunner() {
  if (process.env.PAPERFIT_PYTHON) return process.env.PAPERFIT_PYTHON;
  if (cachedPythonRunner) return cachedPythonRunner;
  const venvRoot = path.join(packageRoot(), '.venv');
  for (const candidate of [path.join(venvRoot, 'bin', 'python'),
    path.join(venvRoot, 'Scripts', 'python.exe')]) {
    if (fs.existsSync(candidate)) return (cachedPythonRunner = candidate);
  }
  if (process.platform === 'win32') {
    // WindowsApps aliases can exit without useful output or open the Store.
    // Inspect actual PATH executables; never change the user's PATH.
    for (const directory of (process.env.PATH || '').split(path.delimiter)) {
      const clean = directory.replace(/^"|"$/g, '');
      if (!clean || /[\\/]Microsoft[\\/]WindowsApps(?:[\\/]|$)/i.test(clean)) continue;
      for (const name of ['python.exe', 'python3.exe']) {
        const candidate = path.join(clean, name);
        if (!fs.existsSync(candidate)) continue;
        const probe = spawnProcessSync(candidate, ['--version'], {
          encoding: 'utf8', timeout: 5000, windowsHide: true
        });
        if (probe.status === 0 && /^Python 3\./.test((probe.stdout || '') + (probe.stderr || ''))) {
          return (cachedPythonRunner = candidate);
        }
      }
    }
  }
  return (cachedPythonRunner = process.platform === 'win32' ? 'python' : 'python3');
}

const isWindows = process.platform === 'win32';

function probeCommand(command) {
  try {
    execSync(command, { stdio: 'ignore', timeout: 5000 });
    return true;
  } catch (_) {
    return false;
  }
}

function probePython() {
  const result = spawnSync(pythonRunner(), ['--version'], {
    encoding: 'utf8', timeout: 5000, stdio: ['ignore', 'pipe', 'pipe']
  });
  return result.status === 0 && /^Python 3\./.test((result.stdout || '') + (result.stderr || ''));
}

function probePythonDependencies() {
  const result = spawnSync(pythonRunner(), ['-c', 'import yaml, fitz, cv2, pdf2image'], {
    stdio: 'ignore', timeout: 10000
  });
  return result.status === 0;
}

function commandExists(command) {
  const probe = isWindows
    ? { file: 'where', args: [command] }
    : { file: 'which', args: [command] };
  const result = spawnSync(probe.file, probe.args, { stdio: 'ignore' });
  return result.status === 0;
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
    .option('--engine <name>', 'TeX engine: pdflatex | xelatex | lualatex; preserve the manuscript compiler')
    .option('--template <template>', '模板键，如 NeurIPS2025')
    .option('--target-pages <n>', '目标页数')
    .option('--page-policy <policy>', '页数目标: at_most (默认) | exact')
    .option('--page-budget-scope <scope>', '目标计页范围: main_body | with_refs | with_appendix')
    .option('--page-budget <scope>', '页数口径: main_body | with_refs | with_appendix')
    .option('--strict', '严格模式')
    .option('--max-rounds <n>', '最大轮次')
    .option('--round-mode <mode>', '多轮模式: safe(1轮) | fix-layout(2轮) | until-stable(5轮+双止损)')
    .option('--apply', '允许 source-changing typed runtime 执行源码写回；默认只 dry-run');
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
  const gatekeeper = status.gatekeeper || {};
  const visual = status.visual || {};
  const staleSections = Array.isArray(status.status_consistency?.stale_sections)
    ? status.status_consistency.stale_sections
    : [];

  console.log('📊 PaperFit Status\n');
  if (status.main_tex) console.log(`  Main TeX: ${status.main_tex}`);
  if (status.task_type) console.log(`  Task: ${status.task_type}`);
  if (status.task?.target_pages != null) {
    const scope = status.task?.page_budget_scope || 'unspecified';
    console.log(`  Page Budget: ${status.task.target_pages} (${scope})`);
  }
  console.log(`  Status: ${status.status || 'UNKNOWN'}`);
  if (status.gatekeeper_decision) console.log(`  Gatekeeper: ${status.gatekeeper_decision}`);
  if (status.run_result_path) console.log(`  RunResult: ${status.run_result_path}`);
  if (freshness.status) console.log(`  Artifact Freshness: ${freshness.status}`);
  if (visual.vlm_diagnosis?.configured) {
    const vision = visual.vlm_diagnosis;
    const coverage = Number.isFinite(vision.coverage) ? `${Math.round(vision.coverage * 100)}%` : 'unknown';
    console.log(`  Vision Inspection: ${vision.status || 'unknown'}; coverage=${coverage}; clean=${vision.clean ?? 'unknown'}`);
  }
  if (visual.b2_width_findings) {
    const b2 = visual.b2_width_findings;
    const bySubtype = b2.by_subtype && Object.keys(b2.by_subtype).length > 0
      ? ` (${Object.entries(b2.by_subtype).map(([key, value]) => `${key}=${value}`).join(', ')})`
      : '';
    const pages = Array.isArray(b2.pages) && b2.pages.length > 0
      ? ` pages=${b2.pages.join(',')}`
      : '';
    console.log(`  B2 Width Findings: ${b2.total ?? 0}${bySubtype}${pages}`);
  }
  if (status.status_consistency?.stale_state_overridden) {
    console.log(`  Status Note: ${status.status_consistency.reason || 'current gatekeeper artifact overrides stale runtime state'}`);
  }
  if (Array.isArray(gatekeeper.reasons) && gatekeeper.reasons.length > 0) {
    console.log(`  Gatekeeper Reasons: ${gatekeeper.reasons.join('; ')}`);
  }

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
  const gatekeeperReasons = Array.isArray(gatekeeper.reasons) ? gatekeeper.reasons : [];
  const gatekeeperDecision = gatekeeper.decision || status.gatekeeper_decision || '';
  const gatekeeperBlocking = gatekeeperReasons.length > 0 || ['CONTINUE', 'BLOCKED'].includes(String(gatekeeperDecision).toUpperCase());
  if (Array.isArray(gatekeeper.remaining_defects) && gatekeeper.remaining_defects.length > 0) {
    console.log(gatekeeperBlocking ? '\n  Gatekeeper Blocking Detail' : '\n  Gatekeeper Remaining Defects');
    const severityRank = { critical: 0, major: 1, unknown: 2, minor: 3 };
    const defectsForDisplay = gatekeeper.remaining_defects
      .slice()
      .sort((left, right) => {
        const leftRank = severityRank[String(left?.severity || 'unknown').toLowerCase()] ?? 2;
        const rightRank = severityRank[String(right?.severity || 'unknown').toLowerCase()] ?? 2;
        return leftRank - rightRank;
      });
    defectsForDisplay.slice(0, 5).forEach((defect, index) => {
      const family = defect?.defect_family || defect?.id || 'unknown';
      const severity = defect?.severity || 'unknown';
      const page = defect?.page != null ? ` p.${defect.page}` : '';
      const description = defect?.description ? ` - ${defect.description}` : '';
      console.log(`    ${index + 1}. ${family} [${severity}]${page}${description}`);
    });
  }

  if (runtime.run_id || runtime.event_count || runtime.event_log) {
    console.log('\n  Runtime');
    if (staleSections.includes('runtime.last_runtime_state')) {
      console.log('    Note: current gatekeeper artifact overrides this run state');
    }
    if (runtime.run_id) console.log(`    Run ID: ${runtime.run_id}`);
    if (runtime.event_log) console.log(`    Event Log: ${runtime.event_log}`);
    if (runtime.event_count != null) console.log(`    Events: ${runtime.event_count}`);
    if (runtime.last_runtime_state) console.log(`    Last State: ${runtime.last_runtime_state}`);
  }

  if (
    repair.plan_candidates ||
    repair.execution_status ||
    repair.skipped ||
    repair.requires_approval != null ||
    repair.b2_width_candidates
  ) {
    console.log('\n  修复计划');
    if (repair.plan_candidates != null) console.log(`    Candidates: ${repair.plan_candidates}`);
    if (repair.b2_width_candidates) {
      const b2 = repair.b2_width_candidates;
      const bySubtype = b2.by_subtype && Object.keys(b2.by_subtype).length > 0
        ? ` (${Object.entries(b2.by_subtype).map(([key, value]) => `${key}=${value}`).join(', ')})`
        : '';
      console.log(`    B2 Width Candidates: ${b2.total ?? 0}${bySubtype}`);
      if (Array.isArray(b2.labels) && b2.labels.length > 0) {
        console.log(`    B2 Width Candidate Labels: ${b2.labels.slice(0, 5).join(', ')}`);
      }
    }
    if (repair.b2_width_targetable_candidates) {
      const b2 = repair.b2_width_targetable_candidates;
      const bySubtype = b2.by_subtype && Object.keys(b2.by_subtype).length > 0
        ? ` (${Object.entries(b2.by_subtype).map(([key, value]) => `${key}=${value}`).join(', ')})`
        : '';
      console.log(`    B2 Width Targetable Candidates: ${b2.total ?? 0}${bySubtype}`);
      if (Array.isArray(b2.labels) && b2.labels.length > 0) {
        console.log(`    B2 Width Targetable Labels: ${b2.labels.slice(0, 5).join(', ')}`);
      }
    }
    if (repair.b2_width_untargetable_candidates && (repair.b2_width_untargetable_candidates.total ?? 0) > 0) {
      const b2 = repair.b2_width_untargetable_candidates;
      const byReason = b2.by_reason && Object.keys(b2.by_reason).length > 0
        ? ` (${Object.entries(b2.by_reason).map(([key, value]) => `${key}=${value}`).join(', ')})`
        : '';
      console.log(`    B2 Width Untargetable Candidates: ${b2.total ?? 0}${byReason}`);
      if (Array.isArray(b2.labels) && b2.labels.length > 0) {
        console.log(`    B2 Width Untargetable Labels: ${b2.labels.slice(0, 5).join(', ')}`);
      }
    }
    if (repair.b2_width_unmatched_findings != null) {
      console.log(`    B2 Width Unmatched Findings: ${repair.b2_width_unmatched_findings}`);
      const b2Findings = repair.b2_width_findings || {};
      const unmatchedPages = Array.isArray(repair.b2_width_unmatched_pages) && repair.b2_width_unmatched_pages.length > 0
        ? repair.b2_width_unmatched_pages
        : b2Findings.pages;
      const unmatchedIds = Array.isArray(repair.b2_width_unmatched_finding_ids) && repair.b2_width_unmatched_finding_ids.length > 0
        ? repair.b2_width_unmatched_finding_ids
        : b2Findings.finding_ids;
      if (repair.b2_width_unmatched_findings > 0 && Array.isArray(unmatchedPages) && unmatchedPages.length > 0) {
        console.log(`    B2 Width Unmatched Pages: ${unmatchedPages.join(', ')}`);
      }
      if (repair.b2_width_unmatched_findings > 0 && Array.isArray(unmatchedIds) && unmatchedIds.length > 0) {
        console.log(`    B2 Width Unmatched IDs: ${unmatchedIds.slice(0, 5).join(', ')}`);
      }
    }
    if (repair.b2_width_selected_candidates && (repair.b2_width_selected_candidates.total ?? 0) > 0) {
      const b2 = repair.b2_width_selected_candidates;
      const bySubtype = b2.by_subtype && Object.keys(b2.by_subtype).length > 0
        ? ` (${Object.entries(b2.by_subtype).map(([key, value]) => `${key}=${value}`).join(', ')})`
        : '';
      console.log(`    B2 Width Selected Candidates: ${b2.total ?? 0}${bySubtype}`);
      const labels = Array.isArray(b2.labels) && b2.labels.length > 0 ? b2.labels : b2.objects;
      if (Array.isArray(labels) && labels.length > 0) {
        console.log(`    B2 Width Selected Labels: ${labels.slice(0, 5).join(', ')}`);
      }
    }
    if (repair.execution_status) console.log(`    Execution: ${repair.execution_status}`);
    if (repair.applied_count != null) console.log(`    Applied: ${repair.applied_count}`);
    if (Array.isArray(repair.selected_candidates) && repair.selected_candidates.length > 0) {
      const selected = repair.selected_candidates.slice(0, 5).map((candidate) => {
        const object = candidate?.object || candidate?.defect_id || 'unknown';
        const subtype = candidate?.visual_width_subtype ? `/${candidate.visual_width_subtype}` : '';
        const kind = candidate?.object_kind ? `/${candidate.object_kind}` : '';
        const page = candidate?.page != null ? `@p.${candidate.page}` : '';
        return `${object}${subtype}${kind}${page}`;
      });
      console.log(`    Selected: ${selected.join(', ')}`);
    }
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
    if (staleSections.includes('repair_loop_policy')) {
      console.log('    Note: policy is from the earlier run; current gatekeeper should drive next action');
    }
    if (repairLoop.execution_mode) console.log(`    Mode: ${repairLoop.execution_mode}`);
    if (repairLoop.round_limit != null) console.log(`    Round Limit: ${repairLoop.round_limit}`);
    if (repairLoop.candidate_batch_limit != null) console.log(`    Candidate Batch Limit: ${repairLoop.candidate_batch_limit}`);
    if (repairLoop.stop_condition) console.log(`    Stop: ${repairLoop.stop_condition}`);
    if (repairLoop.next_round_allowed != null) console.log(`    Next Round Allowed: ${repairLoop.next_round_allowed}`);
    if (repairLoop.next_round_reason) console.log(`    Reason: ${repairLoop.next_round_reason}`);
    const carryForward = repairLoop.approval_scope_carry_forward || {};
    if (carryForward.status) {
      console.log(`    Approval Carry-forward: ${carryForward.status}`);
    }
    if (carryForward.reason) {
      console.log(`    Approval Carry-forward Reason: ${carryForward.reason}`);
    }
    const carryForwardChecks = carryForward.checks || {};
    const failedCarryForwardChecks = Object.entries(carryForwardChecks)
      .filter(([, value]) => value === false)
      .map(([key]) => key);
    if (failedCarryForwardChecks.length > 0) {
      console.log(`    Approval Carry-forward Failed Checks: ${failedCarryForwardChecks.join(', ')}`);
    }
    const candidateGate = repairLoop.candidate_approval_scope_gate || {};
    if (candidateGate.status) {
      console.log(`    Candidate Gate: ${candidateGate.status}`);
    }
    if (candidateGate.reason) {
      console.log(`    Candidate Gate Reason: ${candidateGate.reason}`);
    }
    if (Array.isArray(candidateGate.blocked_candidates) && candidateGate.blocked_candidates.length > 0) {
      console.log(`    Blocked Candidates: ${candidateGate.blocked_candidates.length}`);
      const blockedOperations = candidateGate.blocked_candidates
        .map((candidate) => candidate?.risk?.operation)
        .filter(Boolean)
        .slice(0, 5);
      if (blockedOperations.length > 0) {
        console.log(`    Blocked Operations: ${blockedOperations.join(', ')}`);
      }
    }
    const readiness = repairLoop.second_round_apply_readiness || {};
    if (readiness.status) {
      console.log(`    Second Round Readiness: ${readiness.status}`);
    }
    if (readiness.reason) {
      console.log(`    Second Round Reason: ${readiness.reason}`);
    }
    const readinessChecks = readiness.checks || {};
    const failedReadinessChecks = Object.entries(readinessChecks)
      .filter(([, value]) => value === false)
      .map(([key]) => key);
    if (failedReadinessChecks.length > 0) {
      console.log(`    Second Round Failed Checks: ${failedReadinessChecks.join(', ')}`);
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
  .command('monitor')
  .description('启动只读 PaperFit Observatory 可视化监控台')
  .option('--project <path>', '论文项目根目录', '.')
  .option('--benchmark-root <path>', '可选 benchmark cases 根目录')
  .option('--host <host>', '监听地址', '127.0.0.1')
  .option('--port <port>', '监听端口', '8765')
  .action((options) => {
    const script = path.join(packageRoot(), 'scripts', 'monitor_server.py');
    if (!fs.existsSync(script)) {
      console.error('未找到脚本:', script);
      process.exit(1);
    }
    const args = [
      script,
      '--project',
      options.project || '.',
      '--host',
      options.host || '127.0.0.1',
      '--port',
      String(options.port || '8765'),
    ];
    if (options.benchmarkRoot) {
      args.push('--benchmark-root', options.benchmarkRoot);
    }
    const r = spawnSync(pythonRunner(), args, {
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

program
  .command('rollback [target]')
  .description('从修复前快照回滚源码（target 缺省时读 state 里的 rollback_target）')
  .option('--state <path>', 'State file path', 'data/state.json')
  .option('--output <path>', '回滚报告输出路径', 'data/rollback_report.json')
  .action((target, options) => {
    let rollbackTarget = target;
    if (!rollbackTarget) {
      const state = safeReadJson(path.resolve(process.cwd(), options.state || 'data/state.json'));
      rollbackTarget =
        (state && state.content_integrity && state.content_integrity.rollback_target) ||
        (state && state.pre_repair_snapshot && state.pre_repair_snapshot.rollback_target);
    }
    if (!rollbackTarget) {
      console.error('未找到回滚目标：请传入快照 manifest 路径，或先执行 source-changing 任务生成快照。');
      process.exit(1);
    }
    const script = path.join(packageRoot(), 'scripts', 'orchestrator_runtime.py');
    const args = [
      script,
      '--state',
      options.state || 'data/state.json',
      'rollback-to-snapshot',
      rollbackTarget,
      '--output',
      options.output || 'data/rollback_report.json',
    ];
    const r = spawnSync(pythonRunner(), args, { stdio: 'inherit', cwd: process.cwd(), env: process.env });
    process.exit(r.status === null ? 1 : r.status ?? 1);
  });

program
  .command('resume')
  .description('从 data/task.json 恢复并重跑上一次 typed runtime 任务')
  .option('--task <path>', 'TaskSpec JSON 路径', 'data/task.json')
  .option('--output <path>', 'RunResult 输出路径', 'data/run_result_resumed.json')
  .action((options) => {
    const taskPath = path.resolve(process.cwd(), options.task || 'data/task.json');
    if (!fs.existsSync(taskPath)) {
      console.error(`未找到任务文件 ${taskPath}：resume 需要先跑过一次 typed 任务（如 fix-layout）生成 data/task.json。`);
      process.exit(1);
    }
    const script = path.join(packageRoot(), 'scripts', 'orchestrator_runtime.py');
    const args = [
      script,
      'run-task',
      taskPath,
      '--output',
      options.output || 'data/run_result_resumed.json',
    ];
    const r = spawnSync(pythonRunner(), args, { stdio: 'inherit', cwd: process.cwd(), env: process.env });
    process.exit(r.status === null ? 1 : r.status ?? 1);
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

program
  .command('page-fit')
  .description('只读页数目标与末页诊断工具箱；由宿主审查候选，不自动接受或改源码')
  .option('--main <path>', '主 .tex 文件')
  .option('--root <path>', '论文工程根目录')
  .option('--log <path>', '候选编译日志')
  .option('--target-pages <n>', '目标页数')
  .option('--page-policy <policy>', 'exact | at_most')
  .option('--page-budget-scope <scope>', 'main_body | with_refs | with_appendix')
  .option('--pdf <path>', '当前候选 PDF')
  .option('--before-pdf <path>', '对照 PDF')
  .option('--attempt-tier <tier>', '宿主尝试级别')
  .option('--defects-stable', '宿主已稳定处理可读性缺陷；不证明源码与 PDF 绑定')
  .option('--data-dir <path>', '当前运行证据目录')
  .option('--output <path>', 'JSON 输出文件')
  .allowUnknownOption(true)
  .allowExcessArguments(true)
  .action(() => {
    const position = process.argv.indexOf('page-fit');
    const args = position >= 0 ? process.argv.slice(position + 1) : [];
    const result = spawnSync(pythonRunner(), [path.join(packageRoot(), 'scripts', 'page_fit_toolbox.py'), ...args], {
      stdio: 'inherit', cwd: process.cwd(), env: process.env, windowsHide: true,
    });
    process.exit(result.status === null ? 1 : (result.status ?? 1));
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
    if (r.status !== 0) {
      if (r.error) {
        console.error('PaperFit run failed to start. Interpreter: ' + runner);
        if (ext === '.py') console.error('Select an available Python interpreter using PAPERFIT_PYTHON.');
      } else if (r.signal) {
        console.error('PaperFit script terminated by signal ' + r.signal + '. Interpreter: ' + runner);
      } else {
        console.error('PaperFit script returned exit ' + r.status + '. Consult its output/report for the result.');
      }
    }
    process.exit(r.status === null ? 1 : r.status ?? 1);
  });

// install-global — same as paperfit-install CLI
program
  .command('install-global')
  .description('Install PaperFit assets into a host home (claude/codex/cursor/all)')
  .option('--target <target>', 'Host target: claude | codex | cursor | all', 'claude')
  .option('--project <path>', 'Optional project root for host-specific project assets')
  .option('--scope <scope>', 'Install scope: global | project', 'global')
  .option('--home <path>', 'Explicit host home for isolated setup')
  .option('--force', 'Overwrite existing PaperFit files')
  .option('--dry-run', 'Print planned copies only')
  .action((options) => {
    const script = path.join(__dirname, '..', 'scripts', 'install-host-global.js');
    const args = [script, '--target', options.target, '--scope', options.scope];
    if (options.home) args.push('--home', path.resolve(options.home));
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
    if (probePython()) {
      console.log('✅ Python 3 detected');
    } else {
      console.log('❌ Python 3 not found. Please install Python 3.8+');
      process.exit(1);
    }

    // Check if poppler is available (for pdf2image)
    if (commandExists('pdfinfo')) {
      console.log('✅ Poppler utilities detected');
    } else {
      console.log(
        isWindows
          ? '⚠️  Poppler not found. Install with: choco install poppler (or scoop install poppler)'
          : '⚠️  Poppler not found. Install with: brew install poppler',
      );
    }

    // Check if latexmk is available
    if (commandExists('latexmk')) {
      console.log('✅ latexmk detected');
    } else {
      console.log('⚠️  latexmk not found. Install TeX Live (or MiKTeX on Windows)');
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
  .description('Check toolchain and discoverable host skill')
  .option('--target <target>', 'Host target: claude | codex | cursor | all', 'claude')
  .option('--project <path>', 'Paper project with local skills (defaults to cwd when a project receipt exists)')
  .action((options) => {
    const manifest = loadInstallTargetsManifest();
    const targets = options.target === 'all' ? Object.keys(manifest.targets) : [options.target];
    for (const target of targets) {
      if (!manifest.targets[target]) { console.error('Unsupported target: ' + target); process.exit(1); }
    }
    const home = process.env.HOME || os.homedir();
    const project = options.project ? path.resolve(options.project) : process.cwd();
    const localReceipt = safeReadJson(path.join(project, '.paperfit', 'install-manifest.json'));
    const projectScope = localReceipt?.scope === 'project';
    const sharedRoot = path.join(projectScope ? project : home, '.paperfit');
    const receipt = projectScope ? localReceipt : safeReadJson(path.join(sharedRoot, 'install-manifest.json'));
    let passed = 0, failed = 0;
    function check(name, ok) { console.log((ok ? '✅ ' : '❌ ') + name); if (ok) passed++; else failed++; }
    console.log('PaperFit health: ' + targets.join(', ') + ' (' + (projectScope ? 'project' : 'global') + ' scope)');
    check('Python 3: ' + pythonRunner(), probePython());
    check('Python modules: yaml, fitz, cv2, pdf2image', probePythonDependencies());
    for (const command of ['latexmk', 'pdfinfo', 'pdftoppm']) check(command, commandExists(command));
    check('install receipt: ' + sharedRoot, Boolean(receipt));
    if (receipt) {
      check('receipt version: ' + receipt.version, receipt.version === version);
      check('recorded targets', targets.every(target => (receipt.targets || []).includes(target)));
      check('bundled CLI: ' + receipt.packageRoot, Boolean(receipt.packageRoot) &&
        fs.existsSync(path.join(receipt.packageRoot, 'bin', 'paperfit.js')));
    }
    for (const target of targets) {
      const spec = manifest.targets[target];
      const localEntry = (spec.project_copies || []).find(entry => entry.target.endsWith('skills/paperfit'));
      const globalEntry = spec.copies.find(entry => entry.target.endsWith('skills/paperfit') || entry.target === 'skills');
      const localSkill = localEntry ? path.join(project, localEntry.target, 'SKILL.md') : '';
      const globalSkill = globalEntry ? path.join(home, spec.root_suffix, globalEntry.target,
        globalEntry.target === 'skills' ? 'paperfit' : '', 'SKILL.md') : '';
      const skillPath = projectScope || (options.project && fs.existsSync(localSkill)) ? localSkill : globalSkill;
      let metadataOk = false;
      if (skillPath && fs.existsSync(skillPath)) {
        const content = fs.readFileSync(skillPath, 'utf8');
        metadataOk = /^---\r?\n/.test(content) && /^name:\s*paperfit\s*$/m.test(content) &&
          /^description:\s*\S/m.test(content);
      }
      check(target + ' discoverable paperfit skill: ' + skillPath, metadataOk);
    }
    console.log(passed + '/' + (passed + failed) + ' checks passed');
    console.log('Codex entry: $paperfit. No custom role/provider registration is required.');
    if (failed) {
      console.log('Install host assets: paperfit-install --target ' + options.target +
        (projectScope ? ' --scope project --project "' + project + '"' : ''));
      console.log('Python dependencies: "' + pythonRunner() + '" -m pip install -r "' +
        path.join(packageRoot(), 'requirements.txt') + '"');
      process.exitCode = 1;
    }
  });

program.parse();
