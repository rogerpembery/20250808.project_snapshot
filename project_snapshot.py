#!/usr/bin/env python3
"""
JSON exporter for project structure + appendices (for GPT coding agents)

Upgrades:
- Adds `exclusions`: [{path, rel_path, reason}] explaining why a file wasn't appended.
- Each `files` entry includes `rel_path` and `language`.
- Each appendix entry includes `sha256` and `encoding`.
- Meta echoes `large_threshold_kb` (as well as bytes).
- Always excludes junk dirs (venv/.venv/venv*, .git, node_modules, __pycache__, etc.).
- .env-like files are included (no redaction).
- Appendices include TEXT files only, excluding .json and .log (by extension).
- "Large" prompt threshold is set in **kB** via --large-kb (bytes fallback via --max-size).
"""

# Standard library imports for file operations, JSON handling, CLI parsing, etc.
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable, Tuple, Dict, Any, Optional

# ---- Configuration Constants ----

# File extensions to exclude from appendices (still appear in directory structure)
APPENDIX_EXCLUDE_EXTS = {".json", ".log"}

# Default maximum file size threshold (5 KiB) - can be overridden by --large-kb
DEFAULT_MAX_SIZE = 5 * 1024

# Directory names to always exclude from traversal (common junk directories)
ALWAYS_EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".eggs", ".cache", ".idea", ".vscode",
}

# Mapping of file extensions to language identifiers for syntax highlighting
EXT_TO_LANG = {
    ".py":"python",".js":"javascript",".ts":"typescript",".tsx":"tsx",".jsx":"jsx",
    ".html":"html",".htm":"html",".css":"css",".scss":"scss",".md":"markdown",
    ".yml":"yaml",".yaml":"yaml",".toml":"toml",".ini":"ini",".cfg":"ini",
    ".sh":"bash",".zsh":"zsh",".bat":"bat",".ps1":"powershell",".sql":"sql",
    ".xml":"xml",".csv":"csv",".env":"",".txt":""
}

# Regex pattern to match virtual environment directory names (venv, .venv, venv123, etc.)
_VENV_NAME_RE = re.compile(r"^\.?venv.*$", re.IGNORECASE)

def is_virtualenv_dir_name(name: str) -> bool:
    """Check if a directory name matches virtual environment naming patterns."""
    return bool(_VENV_NAME_RE.match(name))

def is_junk_dir_name(name: str) -> bool:
    """Determine if a directory should be excluded from traversal (junk/cache dirs)."""
    lname = name.lower()
    return lname in ALWAYS_EXCLUDE_DIRS or is_virtualenv_dir_name(name)

# ---- Helper Functions ----

def is_probably_text(path: Path, sample_bytes: int = 8192) -> bool:
    """
    Heuristically determine if a file is probably text-based.
    Checks for null bytes, excessive control characters, and decodability.
    """
    try:
        # Read a sample from the beginning of the file
        with path.open("rb") as f:
            chunk = f.read(sample_bytes)
        
        # Null bytes are a strong indicator of binary files
        if b"\x00" in chunk:
            return False
        
        # Count control characters (excluding tab, LF, CR)
        ctrl = sum(b < 9 or (13 < b < 32) for b in chunk)
        if chunk and (ctrl / len(chunk)) > 0.05:  # > 5% control chars
            return False
        
        # Try to decode as text with strict UTF-8
        try:
            chunk.decode("utf-8", errors="strict")
            return True
        except UnicodeDecodeError:
            # Fall back to latin-1 (which can decode any byte sequence)
            try:
                chunk.decode("latin-1")
                return True
            except UnicodeDecodeError:
                return False
    except Exception:
        return False

def relpath_sorted_children(root: Path) -> Iterable[Path]:
    """
    Get child entries of a directory, filtered and sorted.
    Excludes junk directories, sorts directories first, then files alphabetically.
    """
    try:
        entries = list(root.iterdir())
    except PermissionError:
        return []
    
    # Filter out junk directories
    filtered = []
    for p in entries:
        try:
            if p.is_dir() and is_junk_dir_name(p.name):
                continue
        except PermissionError:
            pass
        filtered.append(p)
    
    # Sort: directories first, then files, both alphabetically (case-insensitive)
    filtered.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
    return filtered

def human_size(n: int) -> str:
    """
    Convert byte count to human-readable size string (e.g., "1.5 KB", "2.3 MB").
    """
    orig = n
    current_size: float = n
    for unit in ("B","KB","MB","GB","TB"):
        if current_size < 1024 or unit == "TB":
            return f"{orig} B" if unit=="B" else f"{current_size:.1f} {unit}"
        current_size /= 1024.0
    
    # This should never be reached, but satisfy type checker
    return f"{orig} B"

def infer_lang(ext: str) -> str:
    """Map file extension to language identifier for syntax highlighting."""
    return EXT_TO_LANG.get(ext.lower(), "")

def read_bytes(path: Path) -> Optional[bytes]:
    """Safely read all bytes from a file, returning None on any error."""
    try:
        with path.open("rb") as f:
            return f.read()
    except Exception:
        return None

def decode_text(b: bytes) -> Tuple[str, str]:
    """
    Decode bytes to text with encoding detection.
    Returns (text, encoding_used).
    
    Tries UTF-8 strict first, then Latin-1, finally UTF-8 with replacement chars.
    """
    try:
        return b.decode("utf-8", errors="strict"), "utf-8"
    except UnicodeDecodeError:
        try:
            return b.decode("latin-1", errors="strict"), "latin-1"
        except UnicodeDecodeError:
            return b.decode("utf-8", errors="replace"), "utf-8 (replace)"

def sha256_hex(b: bytes) -> str:
    """Calculate SHA-256 hash of bytes and return as hexadecimal string."""
    return hashlib.sha256(b).hexdigest()

# ---- Core Directory Traversal ----

def build_tree_and_flat(
    root: Path,
    follow_symlinks: bool = False,
) -> Tuple[Dict[str, Any], list[Dict[str, Any]], int, int]:
    """
    Recursively traverse directory structure and build both tree and flat representations.
    
    Returns:
        - Nested tree structure (Dict)
        - Flat list of all files (List[Dict])
        - Total directory count (int)
        - Total file count (int)
    """
    dir_count = 0
    file_count = 0

    def recurse_dir(d: Path) -> Dict[str, Any]:
        """Recursively process a directory and all its contents."""
        nonlocal dir_count, file_count
        
        # Create directory node with basic metadata
        node = {"type":"directory","name":d.name,"path":str(d.resolve()),"children":[]}
        dir_count += 1
        
        # Process each child entry
        for child in relpath_sorted_children(d):
            try:
                if child.is_dir():
                    # Handle symlinked directories based on follow_symlinks setting
                    if child.is_symlink() and not follow_symlinks:
                        node["children"].append({
                            "type":"symlink_dir","name":child.name,
                            "path":str(child.resolve()) if child.exists() else str(child),
                        })
                        continue
                    # Recursively process subdirectory
                    node["children"].append(recurse_dir(child))
                elif child.is_file():
                    # Get file size safely
                    try:
                        size = child.stat().st_size
                    except Exception:
                        size = None
                    
                    # Add file node with metadata
                    node["children"].append({
                        "type":"file","name":child.name,
                        "path":str(child.resolve()),
                        "size_bytes":size,
                    })
                    file_count += 1
                else:
                    # Handle special files (devices, pipes, etc.)
                    node["children"].append({"type":"special","name":child.name,"path":str(child)})
            except PermissionError:
                # Record permission errors as special nodes
                node["children"].append({"type":"error","name":child.name,"path":str(child),"error":"permission_denied"})
            except FileNotFoundError:
                # Record missing files as special nodes
                node["children"].append({"type":"error","name":child.name,"path":str(child),"error":"not_found"})
        return node

    # Build the directory tree starting from root
    tree = recurse_dir(root)

    # Extract flat list of all files from the tree structure
    flat_files: list[Dict[str, Any]] = []
    def collect_files(node: Dict[str, Any]):
        """Recursively collect all file nodes into flat list."""
        if node.get("type") == "file":
            flat_files.append(node)
            return
        # Recurse into child nodes
        for c in node.get("children", []):
            collect_files(c)
    
    collect_files(tree)
    return tree, flat_files, dir_count, file_count

# ---- Appendix Inclusion Decision Logic ----

def decide_appendix(path: Path, max_size: int, mode: str, out_path: Path) -> Tuple[bool, Optional[str], Optional[bytes]]:
    """
    Determine whether a file should be included in the appendices section.
    
    Args:
        path: File path to evaluate
        max_size: Maximum file size threshold in bytes
        mode: How to handle large files ('ask', 'yes', 'no')
        out_path: Output file path (to avoid including itself)
    
    Returns:
        Tuple of (should_include, exclusion_reason, file_bytes)
        Exclusion reasons: 'self_output', 'excluded_ext', 'binary', 'large_skipped', 'other'
    """
    # Skip the output file itself to avoid recursive inclusion
    if path.resolve() == out_path.resolve():
        return (False, "self_output", None)

    # Check if file extension is explicitly excluded from appendices
    ext = path.suffix.lower()
    if ext in APPENDIX_EXCLUDE_EXTS:
        return (False, "excluded_ext", None)

    # Skip binary files (use heuristic detection)
    if not is_probably_text(path):
        return (False, "binary", None)

    # Get file size for threshold checking
    try:
        size = path.stat().st_size
    except Exception:
        return (False, "other", None)

    # Handle files larger than the threshold based on mode setting
    if size > max_size:
        if mode == "no":
            # Automatically skip large files
            return (False, "large_skipped", None)
        elif mode == "ask":
            # Prompt user for each large file
            rel = path.relative_to(Path.cwd())
            while True:
                resp = input(
                    f"File '{rel}' is {human_size(size)} (> {max_size} bytes). "
                    "Include in appendix? [y/N/a=all yes/n=all no] "
                ).strip().lower()
                if resp in ("y","yes"):
                    break  # Include this file
                if resp in ("n","no",""):
                    return (False, "large_skipped", None)
                if resp in ("a","all","always"):
                    # Include this file (but don't change global mode)
                    break
                if resp in ("never","nn"):
                    return (False, "large_skipped", None)
                print("Please answer y, n, a (all yes), or 'never' (all no).")
        # If mode == "yes", fall through to include the file

    # Read file contents for inclusion
    b = read_bytes(path)
    if b is None:
        return (False, "other", None)
    
    # File passes all checks - include it in appendix
    return (True, None, b)

# ---- JSON Report Generation ----

def write_report_json(
    root: Path,
    out_path: Path,
    follow_symlinks: bool,
    max_size: int,
    large_mode: str,
):
    """
    Generate and write the complete JSON report containing:
    - Project metadata
    - Directory tree structure
    - Flat file listing
    - File contents (appendices)
    - Exclusion details with reasons
    """
    timestamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Build directory structure and get file listings
    tree, flat_files, dir_count, file_count = build_tree_and_flat(root, follow_symlinks=follow_symlinks)

    # Initialize collections for processing results
    appendices = []  # Files included in appendix with full content
    exclusions = []  # Files excluded from appendix with reasons
    total_appendix_bytes = 0

    # Process files in alphabetical order for consistent output
    files_sorted = sorted(flat_files, key=lambda f: f["path"].lower())

    # Process each file to determine appendix inclusion
    for f in files_sorted:
        p = Path(f["path"])
        if not p.is_file():
            continue

        # Decide whether to include this file in appendix
        include, reason, raw = decide_appendix(p, max_size=max_size, mode=large_mode, out_path=out_path)
        rel = str(p.relative_to(root))

        if include and raw is not None:
            # File is included - decode content and collect metadata
            text, encoding = decode_text(raw)
            size = None
            try:
                size = p.stat().st_size
            except Exception:
                pass
            lang = infer_lang(p.suffix.lower())
            
            # Add to appendices with full metadata
            appendices.append({
                "path": rel,
                "abs_path": str(p.resolve()),
                "size_bytes": size,
                "language": lang,
                "encoding": encoding,
                "sha256": sha256_hex(raw),
                "content": text,
            })
            if size:
                total_appendix_bytes += size
        else:
            # File is excluded - record the reason
            exclusions.append({
                "path": str(p.resolve()),
                "rel_path": rel,
                "reason": reason or "other",
            })

    # Enrich file entries with relative paths and language information
    enriched_files = []
    for f in files_sorted:
        p = Path(f["path"])
        enriched = dict(f)  # Copy original file info
        
        # Add relative path
        try:
            enriched["rel_path"] = str(p.relative_to(root))
        except Exception:
            enriched["rel_path"] = f["path"]
        
        # Add language identifier based on file extension
        enriched["language"] = infer_lang(p.suffix.lower())
        enriched_files.append(enriched)

    # Assemble the complete JSON report structure
    data = {
        "meta": {
            "generated": timestamp,
            "root": str(root.resolve()),
            "large_threshold_bytes": max_size,
            "large_threshold_kb": round(max_size / 1024, 2),
            "large_mode": large_mode,  # 'ask' | 'yes' | 'no'
            "excluded_directory_rules": {
                "virtualenv_regex": r"^\.?venv.*$",
                "name_set": sorted(list(ALWAYS_EXCLUDE_DIRS)),
            },
            "summary": {
                "directories": dir_count,
                "files": file_count,
                "appendices": len(appendices),
                "appendices_total_bytes": total_appendix_bytes,
                "exclusions": len(exclusions),
            },
        },
        "structure": tree,          # Nested directory tree representation
        "files": enriched_files,    # Flat list of all files with metadata
        "appendices": appendices,   # Full file contents with encoding/hash info
        "exclusions": exclusions,   # Files excluded from appendix with reasons
    }

    # Write the JSON report to disk
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote JSON report to: {out_path}")

# ---- Command Line Interface ----

def parse_args(argv=None):
    """Parse command line arguments for the project snapshot tool."""
    p = argparse.ArgumentParser(
        description="Export a recursive project structure and text-file appendices as JSON for GPT agents."
    )
    
    # Output file configuration
    p.add_argument("-o","--output", default="PROJECT_STRUCTURE_AND_APPENDICES.json",
                   help="Output JSON filename (default: %(default)s)")
    
    # File size threshold options
    p.add_argument("--large-kb", type=int, default=None,
                   help="Size threshold in kilobytes for prompting on large files.")
    p.add_argument("--max-size", type=int, default=DEFAULT_MAX_SIZE,
                   help=f"Fallback threshold in bytes if --large-kb not provided (default: {DEFAULT_MAX_SIZE}).")
    
    # Directory traversal options
    p.add_argument("--follow-symlinks", action="store_true",
                   help="Follow symlinked directories (off by default).")
    
    # Large file handling modes (mutually exclusive)
    group = p.add_mutually_exclusive_group()
    group.add_argument("--yes-large", action="store_true",
                       help="Automatically include large files without prompting.")
    group.add_argument("--no-large", action="store_true",
                       help="Automatically skip large files without prompting.")
    
    return p.parse_args(argv)

def main():
    """Main entry point for the project snapshot tool."""
    args = parse_args()
    
    # Set up paths - use current working directory as project root
    root = Path.cwd()
    out_path = (root / args.output).resolve()

    # Determine large file handling mode
    large_mode = "ask"  # Default: prompt user for each large file
    if args.yes_large:
        large_mode = "yes"  # Include all large files automatically
    elif args.no_large:
        large_mode = "no"   # Skip all large files automatically

    # Calculate size threshold (prefer --large-kb over --max-size)
    max_size = args.max_size
    if args.large_kb is not None:
        max_size = int(args.large_kb) * 1024  # Convert kB to bytes

    # Generate the report
    try:
        write_report_json(
            root=root,
            out_path=out_path,
            follow_symlinks=args.follow_symlinks,
            max_size=max_size,
            large_mode=large_mode,
        )
    except KeyboardInterrupt:
        print("\nAborted by user.", file=sys.stderr)
        sys.exit(130)  # Standard exit code for SIGINT

# Entry point when script is run directly
if __name__ == "__main__":
    main()
