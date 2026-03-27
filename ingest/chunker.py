"""Chunking strategies for Markdown and HCL files.

Markdown:  split on H1/H2/H3 heading boundaries first, then fall back
           to fixed-size with overlap for sections that exceed *size*.
HCL:       split on top-level block boundaries (resource, data, module …)
           so a sliding window never bisects a semantic block.
"""

from __future__ import annotations

import re
from typing import List

# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,3})\s", re.MULTILINE)


def chunk_markdown(text: str, size: int = 600, overlap: int = 100) -> List[str]:
    """Split *text* on heading boundaries, then fixed-size with overlap."""
    sections = _split_on_headings(text)
    chunks: List[str] = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        if len(section) <= size:
            chunks.append(section)
        else:
            chunks.extend(_fixed_size_chunks(section, size, overlap))
    return chunks


def _split_on_headings(text: str) -> List[str]:
    positions = [m.start() for m in _HEADING_RE.finditer(text)]
    if not positions:
        return [text]
    sections: List[str] = []
    if positions[0] > 0:
        sections.append(text[: positions[0]])
    for i, pos in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(text)
        sections.append(text[pos:end])
    return sections


# ---------------------------------------------------------------------------
# HCL / Terraform
# ---------------------------------------------------------------------------

_HCL_BLOCK_RE = re.compile(
    r"^(resource|data|module|variable|output|locals|provider|terraform)\s",
    re.MULTILINE,
)


def chunk_hcl(text: str, max_block: int = 800, overlap: int = 50) -> List[str]:
    """Split *text* on top-level HCL block boundaries."""
    blocks = _split_on_hcl_blocks(text)
    chunks: List[str] = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if len(block) <= max_block:
            chunks.append(block)
        else:
            chunks.extend(_fixed_size_chunks(block, max_block, overlap))
    return chunks


def _split_on_hcl_blocks(text: str) -> List[str]:
    positions = [m.start() for m in _HCL_BLOCK_RE.finditer(text)]
    if not positions:
        return [text]
    blocks: List[str] = []
    if positions[0] > 0:
        blocks.append(text[: positions[0]])
    for i, pos in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(text)
        blocks.append(text[pos:end])
    return blocks


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


def _fixed_size_chunks(text: str, size: int, overlap: int) -> List[str]:
    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks
