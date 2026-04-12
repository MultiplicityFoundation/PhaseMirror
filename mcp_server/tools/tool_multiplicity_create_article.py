"""MCP tool for creating/updating multiplicity knowledge articles."""

from __future__ import annotations

import os
from pathlib import Path


def multiplicity_create_article(article_path: str, content: str) -> dict[str, str]:
    target = Path(article_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
    return {
        'status': 'created',
        'path': str(target.resolve()),
    }
