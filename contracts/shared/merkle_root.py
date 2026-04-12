"""Merkle Root Computation for Governance Immutability Verification.

Per ADR-012, the Merkle root is the root of trust for all immutability.
This module computes a Merkle tree over all immutable files and returns a
single root hash that can be stored in the governance ledger.

Key insight: No file contains its own hash. Instead, all files' hashes are
computed, then a Merkle tree is built over them, and the root is stored
externally in the ledger. This breaks the self-reference paradox.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Optional


_GOVERNANCE_POINTER_FILE_SUFFIX = "contracts/shared/constants.py"
_GOVERNANCE_POINTER_LINE = "GOVERNANCE_MERKLE_ROOT_TX_ID: int = 0"


def _is_governance_pointer_file(file_path: Path) -> bool:
    return file_path.as_posix().endswith(_GOVERNANCE_POINTER_FILE_SUFFIX)


def _canonicalize_governance_pointer(raw_content: bytes, file_path: Path) -> bytes:
    if not _is_governance_pointer_file(file_path):
        return raw_content

    try:
        text = raw_content.decode("utf-8")
    except UnicodeDecodeError:
        return raw_content

    pattern = re.compile(r"^\s*GOVERNANCE_MERKLE_ROOT_TX_ID\s*(?::\s*int\s*)?=\s*\d+\s*$", re.MULTILINE)
    canonicalized = pattern.sub(_GOVERNANCE_POINTER_LINE, text)
    return canonicalized.encode("utf-8")


def _sha256_bytes(content: bytes) -> str:
    hasher = hashlib.sha256()
    hasher.update(content)
    return hasher.hexdigest()


def sha256_file_raw(file_path: Path) -> str:
    """Compute SHA256 hash of raw file bytes with no canonicalization."""
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def governance_root_hash_details(file_path: Path) -> dict[str, Any]:
    """Return raw-vs-root-input hash details for a file in governance root computation."""
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    raw_content = file_path.read_bytes()
    raw_hash = _sha256_bytes(raw_content)

    if _is_governance_pointer_file(file_path):
        root_input_bytes = _canonicalize_governance_pointer(raw_content, file_path)
        root_input_hash = _sha256_bytes(root_input_bytes)
        return {
            "raw_hash": raw_hash,
            "root_input_hash": root_input_hash,
            "is_canonicalized": raw_hash != root_input_hash,
            "hash_semantics": "canonicalized_governance_pointer",
        }

    return {
        "raw_hash": raw_hash,
        "root_input_hash": raw_hash,
        "is_canonicalized": False,
        "hash_semantics": "raw_bytes",
    }


def _normalize_file_inputs(primary_files: list[Path], extra_files: Optional[list[Path]] = None) -> list[Path]:
    seen: set[str] = set()
    combined = list(primary_files)
    if extra_files:
        combined.extend(extra_files)

    normalized: list[Path] = []
    for file_path in combined:
        key = str(file_path)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(file_path)
    return sorted(normalized, key=lambda path: str(path))

def sha256_file(file_path: Path) -> str:
    """Compute SHA256 hash of a file.
    
    Args:
        file_path: Path to the file
        
    Returns:
        Hex-encoded SHA256 hash
        
    Raises:
        FileNotFoundError: If file does not exist
        IOError: If file cannot be read
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    
    details = governance_root_hash_details(file_path)
    return str(details["root_input_hash"])


def merkle_tree(hashes: list[str]) -> tuple[str, dict[str, Any]]:
    """Build Merkle tree from leaf hashes.
    
    Constructs a binary Merkle tree by repeatedly hashing pairs of nodes.
    If the count is odd, the last hash is paired with itself.
    
    Args:
        hashes: List of leaf hashes (hex strings)
        
    Returns:
        (root_hash, tree_structure_dict) where tree_structure is for audit/debugging
    """
    if not hashes:
        # Empty tree has empty-string hash as root
        empty_hash = hashlib.sha256(b'').hexdigest()
        return empty_hash, {"root": empty_hash, "type": "empty"}
    
    if len(hashes) == 1:
        # Single leaf: root is the leaf itself
        return hashes[0], {"root": hashes[0], "type": "single_leaf"}
    
    # Build tree bottom-up
    tree: dict[str, Any] = {f"leaf_{i}": h for i, h in enumerate(hashes)}
    current_level = list(hashes)
    level_num = 0
    
    while len(current_level) > 1:
        level_num += 1
        next_level = []
        
        for i in range(0, len(current_level), 2):
            left = current_level[i]
            # If odd count, pair last with itself
            right = current_level[i + 1] if i + 1 < len(current_level) else left
            
            # Parent hash = SHA256(left_hash + right_hash)
            parent_preimage = (left + right).encode('utf-8')
            parent = hashlib.sha256(parent_preimage).hexdigest()
            next_level.append(parent)
            
            # Store in tree for audit
            tree[f"level_{level_num}_node_{len(next_level)-1}"] = {
                "left": left,
                "right": right,
                "parent": parent,
            }
        
        current_level = next_level
    
    root = current_level[0]
    tree["root"] = root
    return root, tree


def compute_governance_root(
    immutable_files: list[Path],
    governance_critical_tools: Optional[list[Path]] = None,
) -> str:
    """Compute Merkle root of all immutable files.
    
    Per ADR-012:
    1. Read all immutable files
    2. Compute SHA256 for each
    3. Build Merkle tree from hashes
    4. Return single root hash
    
    The root can be stored in the governance ledger without self-reference,
    since no individual file needs to contain its own hash.
    
    Args:
        immutable_files: List of paths to immutable files
        governance_critical_tools: Optional governance-critical tool files included
            in the same Merkle root trust boundary
        
    Returns:
        Root hash of the Merkle tree
        
    Raises:
        FileNotFoundError: If any file does not exist
    """
    all_files = _normalize_file_inputs(immutable_files, governance_critical_tools)

    if not all_files:
        # No immutable files: empty root
        return hashlib.sha256(b'').hexdigest()
    
    # Compute hash for each file (in order)
    hashes = []
    for file_path in all_files:
        try:
            file_hash = sha256_file(file_path)
            hashes.append(file_hash)
        except Exception as e:
            raise IOError(f"Failed to hash file {file_path}: {e}")
    
    # Build Merkle tree and return root
    root, _tree = merkle_tree(hashes)
    return root


def file_to_hash_dict(immutable_files: list[Path]) -> dict[str, str]:
    """Generate dict of file paths to hashes for audit trail.
    
    Args:
        immutable_files: List of paths to immutable files
        
    Returns:
        Dict mapping file path (str) to hash (str)
    """
    result = {}
    for file_path in immutable_files:
        try:
            result[str(file_path)] = sha256_file(file_path)
        except Exception as e:
            # Log but don't fail; this is for audit purposes
            result[str(file_path)] = f"ERROR: {e}"
    return result
