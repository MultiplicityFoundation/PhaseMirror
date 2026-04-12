# Phase Mirror MVP CLI

This directory contains the Phase Mirror MVP CLI analyzer wrapper.

## Quick start

Install dependencies from the `mvp/cli` directory:

```bash
cd mvp/cli
npm install
```

Run the analyzer in dev mode:

```bash
npm run dev -- analyze --output-expr "test" --json
```

Build the CLI:

```bash
npm run build
```

Then use the generated binary from `dist` via:

```bash
node dist/index.js analyze --output-expr "test"
```

## Notes

The CLI invokes the Phase Mirror Python engine via `python3 -c` and sets
`PHAMIR_REPO_ROOT` to the MVP repository root.
