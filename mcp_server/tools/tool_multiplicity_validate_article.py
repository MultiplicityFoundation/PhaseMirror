"""MCP tool for validating multiplicity knowledge articles."""

from __future__ import annotations

import subprocess


def multiplicity_validate_article(article_path: str) -> dict[str, str]:
    command = ['node', 'packages/multiplicity-knowledge/validate_article.js', article_path]
    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f'validation failed: {proc.stderr.strip()}')
    return {
        'status': 'valid',
        'path': article_path,
        'message': proc.stdout.strip(),
    }
