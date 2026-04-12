import { Command } from 'commander';
import { spawn } from 'child_process';
import * as path from 'path';

// __dirname at runtime: <repo-root>/packages/phase-mirror-cli/dist/commands
// 4 levels up → repo root
// In dev mode, __dirname is <repo-root>/cli/src, so we only need 3 levels.
const DEFAULT_REPO_ROOT = path.basename(__dirname) === 'src'
  ? path.resolve(__dirname, '../../../')
  : path.resolve(__dirname, '../../../../');
const DEFAULT_LEDGER_PATH = path.join(DEFAULT_REPO_ROOT, 'state', 'phase_mirror_cli_ledger.json');

// Exit codes
const EXIT_PASS = 0;
const EXIT_FAIL = 1;
const EXIT_REVIEW = 2;
const EXIT_ENGINE_ERROR = 3;

const PYTHON_BRIDGE = `
import sys, os, json
from pathlib import Path

repo_root = os.environ['PHAMIR_REPO_ROOT']
sys.path.insert(0, repo_root)
sys.path.insert(0, os.path.join(repo_root, 'governance'))

from policy.phase_mirror import evaluate, RHO_STAR
from contracts.shared.types import PIRTMExpr
from governance.ledger import AuditLedger

ledger_path = os.environ['PHAMIR_LEDGER_PATH']
input_text = os.environ['PHAMIR_INPUT_TEXT']
output_expr = os.environ['PHAMIR_OUTPUT_EXPR']
rollback_trigger = os.environ['PHAMIR_ROLLBACK_TRIGGER']

ledger = AuditLedger(location=Path(ledger_path))
expr = PIRTMExpr(('\\x00MLIR\\x00MAGIC' + output_expr).encode('utf-8'))
report = evaluate(
    input_text=input_text,
    output_expr=expr,
    rollback_trigger=rollback_trigger,
    audit_ledger=ledger,
)
ledger.save()
result = report.to_dict()
result['_cli'] = {
    'decision': report.decision,
    'rho_star': float(RHO_STAR),
    'threshold': float(report.rho_threshold),
    'execute': report.execute,
}
print(json.dumps(result))
`;

export const analyzeCommand = new Command('analyze')
  .description('Evaluate an expression against the Phase Mirror engine')
  .option('--input-text <text>', 'Triggering input or prompt context', 'cli-analyze')
  .option('--output-expr <expr>', 'Expression to evaluate (PIRTM body)', '')
  .option('--rollback-trigger <trigger>', 'Rollback trigger signal', 'none')
  .option(
    '--repo-root <path>',
    'Path to the PhaseMirror-HQ repo root',
    DEFAULT_REPO_ROOT,
  )
  .option('--ledger-path <path>', 'Path for the audit ledger JSON', DEFAULT_LEDGER_PATH)
  .option('--json', 'Print the full DissonanceReport JSON to stdout')
  .action((opts: {
    inputText: string;
    outputExpr: string;
    rollbackTrigger: string;
    repoRoot: string;
    ledgerPath: string;
    json: boolean | undefined;
  }) => {
    const env: NodeJS.ProcessEnv = {
      ...process.env,
      PHAMIR_REPO_ROOT: opts.repoRoot,
      PHAMIR_LEDGER_PATH: opts.ledgerPath,
      PHAMIR_INPUT_TEXT: opts.inputText,
      PHAMIR_OUTPUT_EXPR: opts.outputExpr,
      PHAMIR_ROLLBACK_TRIGGER: opts.rollbackTrigger,
    };

    let stdout = '';
    let stderr = '';

    const child = spawn('python3', ['-c', PYTHON_BRIDGE], { env });

    child.stdout.on('data', (chunk: Buffer) => {
      stdout += chunk.toString();
    });

    child.stderr.on('data', (chunk: Buffer) => {
      stderr += chunk.toString();
    });

    child.on('close', (code: number | null) => {
      if (code !== 0 || !stdout.trim()) {
        process.stderr.write(`phase-mirror engine error (python exit ${code ?? 'null'}):\n${stderr}\n`);
        process.exit(EXIT_ENGINE_ERROR);
      }

      let report: Record<string, unknown>;
      try {
        report = JSON.parse(stdout.trim()) as Record<string, unknown>;
      } catch {
        process.stderr.write(`phase-mirror: failed to parse engine output\n${stdout}\n`);
        process.exit(EXIT_ENGINE_ERROR);
      }

      if (opts.json) {
        process.stdout.write(JSON.stringify(report, null, 2) + '\n');
      }

      const cli = report['_cli'] as { decision: string; execute: boolean; rho_star: number; threshold: number } | undefined;
      const rho = (report['rho'] as number) ?? 0;
      const execute = cli?.execute ?? false;
      const rhoStar = cli?.rho_star ?? 0.7;

      if (!execute) {
        if (!opts.json) {
          process.stderr.write(`FAIL  rho=${rho.toFixed(4)}  kill-switch threshold reached\n`);
        }
        process.exit(EXIT_FAIL);
      }

      if (rho >= rhoStar) {
        if (!opts.json) {
          process.stdout.write(`REVIEW  rho=${rho.toFixed(4)}  tensions detected but below threshold — monitor\n`);
        }
        process.exit(EXIT_REVIEW);
      }

      if (!opts.json) {
        process.stdout.write(`PASS  rho=${rho.toFixed(4)}\n`);
      }
      process.exit(EXIT_PASS);
    });
  });
