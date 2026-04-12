#!/usr/bin/env node
import { Command } from 'commander';
import { analyzeCommand } from './commands/analyze';

// eslint-disable-next-line @typescript-eslint/no-var-requires
const pkg = require('../package.json') as { version: string };

const program = new Command();

program
  .name('phase-mirror')
  .description('Evaluate expressions and state transitions against the Phase Mirror governance engine')
  .version(pkg.version);

program.addCommand(analyzeCommand);

program.parse(process.argv);
