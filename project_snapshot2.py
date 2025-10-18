#!/usr/bin/env python3
# SUMMARY: Generates hierarchical project architecture documentation by recursively scanning for coding-relevant files and extracting their SUMMARY comments. Creates timestamped JSON output with nested folder structure, file summaries, and virtual environment package listings.
"""
Project Architecture Documentation Generator

Creates a hierarchical JSON representation of project structure focusing on:
- Coding-relevant files with extracted SUMMARY comments
- Virtual environment package listing
- Only includes folders containing coding files
- Excludes common junk directories (node_modules, .git, __pycache__, etc.)
"""

import argparse
import datetime as dt
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List

# ---- Configuration Constants ----

# File extensions considered coding-relevant (user can extend this list)
CODING_RELEVANT_EXTS = {
    # Programming languages
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".scala", ".r", ".m",

    # Web
    ".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue",

    # Configuration
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",

    # Shell/Scripts
    ".sh", ".bash", ".zsh", ".bat", ".ps1", ".cmd",

    # Documentation
    ".md", ".txt", ".rst", ".adoc",

    # Database/Query
    ".sql",

    # Build/Package
    ".xml", ".gradle", ".cmake", ".dockerfile",

    # Other
    ".gitignore", ".dockerignore",
}

# Directory names to always exclude from traversal (common junk directories)
ALWAYS_EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".eggs", ".cache", ".idea", ".vscode",
    "venv",  # Exclude the venv directory from tree traversal
}

# Exclusion patterns for files and folders (supports wildcards)
# Examples: ".claude", "*.log", "temp*", "build/*"
EXCLUSION_PATTERNS = [
    ".claude",
    "project_architecture_*.json",  # Exclude generated architecture files
    "project_snapshot2.py",  # Exclude this script itself
]

# Regex pattern to extract SUMMARY comments from files
SUMMARY_PATTERN = re.compile(r'^\s*#\s*SUMMARY:\s*(.+)$', re.MULTILINE)

# ---- Helper Functions ----

def is_junk_dir_name(name: str) -> bool:
    """Determine if a directory should be excluded from traversal."""
    return name.lower() in ALWAYS_EXCLUDE_DIRS


def matches_exclusion_pattern(path: Path, root: Path) -> bool:
    """
    Check if a path matches any exclusion pattern.

    Supports wildcards (*) and can match against:
    - File/folder names (e.g., ".claude", "*.log")
    - Relative paths (e.g., "build/*", "*/temp")
    """
    try:
        rel_path = str(path.relative_to(root))
        name = path.name

        for pattern in EXCLUSION_PATTERNS:
            # Check against both the name and the relative path
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel_path, pattern):
                return True

        return False
    except (ValueError, Exception):
        return False


def is_coding_relevant_file(path: Path) -> bool:
    """Check if a file has a coding-relevant extension."""
    return path.suffix.lower() in CODING_RELEVANT_EXTS or path.name in CODING_RELEVANT_EXTS


def extract_summary(path: Path) -> tuple[Optional[str], bool]:
    """
    Extract SUMMARY comment from a file.

    Returns:
        Tuple of (summary_text, has_summary)
        - If found: (summary_text, True)
        - If not found: ("SUMMARY comment not found", False)
    """
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        match = SUMMARY_PATTERN.search(content)
        if match:
            return (match.group(1).strip(), True)
        else:
            return ("SUMMARY comment not found", False)
    except Exception as e:
        return (f"Error reading file: {str(e)}", False)


def get_venv_packages(venv_path: Path) -> Optional[List[Dict[str, str]]]:
    """
    Get installed packages from virtual environment using pip freeze.

    Returns:
        List of {"name": "package", "version": "1.2.3"} dicts, or None if venv not found
    """
    if not venv_path.exists() or not venv_path.is_dir():
        return None

    # Try to find pip executable in venv
    pip_paths = [
        venv_path / "bin" / "pip",  # Unix-like
        venv_path / "Scripts" / "pip.exe",  # Windows
        venv_path / "Scripts" / "pip",  # Windows (alternative)
    ]

    pip_exe = None
    for pip_path in pip_paths:
        if pip_path.exists():
            pip_exe = pip_path
            break

    if pip_exe is None:
        return None

    try:
        # Run pip freeze to get installed packages
        result = subprocess.run(
            [str(pip_exe), "freeze"],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            return None

        # Parse pip freeze output (format: package==version)
        packages = []
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            # Handle different formats: package==version, package @ file://...
            if '==' in line:
                name, version = line.split('==', 1)
                packages.append({"name": name.strip(), "version": version.strip()})
            elif ' @ ' in line:
                # Editable installs: package @ file://...
                name = line.split(' @ ', 1)[0].strip()
                packages.append({"name": name, "version": "editable"})
            else:
                # Fallback for other formats
                packages.append({"name": line, "version": "unknown"})

        return packages
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, Exception):
        return None


def has_coding_files_recursive(path: Path) -> bool:
    """
    Recursively check if a directory contains any coding-relevant files.
    """
    try:
        for item in path.rglob('*'):
            if item.is_file() and is_coding_relevant_file(item):
                # Check if the file is not in a junk directory
                if not any(is_junk_dir_name(part) for part in item.relative_to(path).parts[:-1]):
                    return True
        return False
    except (PermissionError, OSError):
        return False


def build_coding_tree(root: Path) -> tuple[Optional[Dict[str, Any]], int, int]:
    """
    Build nested tree structure containing only folders with coding-relevant files.

    Returns:
        Tuple of (tree_structure, folder_count, file_count)
    """
    folder_count = 0
    file_count = 0

    def recurse_dir(d: Path, rel_path: str = "") -> Optional[Dict[str, Any]]:
        """Recursively process a directory, filtering for coding-relevant content."""
        nonlocal folder_count, file_count

        # Get all children, excluding junk directories and exclusion patterns
        try:
            children = [
                child for child in d.iterdir()
                if not (child.is_dir() and is_junk_dir_name(child.name))
                and not matches_exclusion_pattern(child, root)
            ]
            children.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return None

        # Separate files and directories
        coding_files = []
        subdirs = []

        for child in children:
            try:
                if child.is_file() and is_coding_relevant_file(child):
                    # Extract summary from the file
                    summary, has_summary = extract_summary(child)

                    coding_files.append({
                        "name": child.name,
                        "path": str(child.relative_to(root)),
                        "summary": summary,
                        "has_summary": has_summary,
                    })
                    file_count += 1
                elif child.is_dir():
                    # Recursively process subdirectory
                    subdir_node = recurse_dir(
                        child,
                        f"{rel_path}/{child.name}" if rel_path else child.name
                    )
                    if subdir_node is not None:
                        subdirs.append(subdir_node)
            except (PermissionError, OSError):
                continue

        # Only include this directory if it has coding files or non-empty subdirectories
        if not coding_files and not subdirs:
            return None

        folder_count += 1

        return {
            "name": d.name if d != root else root.name,
            "path": rel_path if rel_path else ".",
            "files": coding_files,
            "subdirectories": subdirs,
        }

    tree = recurse_dir(root)
    return tree, folder_count, file_count


def write_architecture_json(root: Path, out_path: Path):
    """
    Generate and write the architecture documentation JSON.

    Includes:
    - Meta information with timestamps and summary counts
    - Virtual environment package listing
    - Nested tree structure with files and summaries
    """
    timestamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Get venv packages
    venv_path = root / "venv"
    venv_packages = get_venv_packages(venv_path)

    # Build the coding file tree
    tree, folder_count, file_count = build_coding_tree(root)

    # Assemble the JSON structure
    data = {
        "meta": {
            "generated": timestamp,
            "root": str(root.resolve()),
            "excluded_directories": sorted(list(ALWAYS_EXCLUDE_DIRS)),
            "exclusion_patterns": EXCLUSION_PATTERNS,
            "coding_relevant_extensions": sorted(list(CODING_RELEVANT_EXTS)),
            "summary": {
                "folders_with_coding_files": folder_count,
                "coding_files": file_count,
                "venv_packages": len(venv_packages) if venv_packages else 0,
            },
        },
        "venv_packages": venv_packages if venv_packages else [],
        "structure": tree,
    }

    # Write JSON to disk
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Architecture documentation written to: {out_path}")


# ---- Command Line Interface ----

def parse_args(argv=None):
    """Parse command line arguments."""
    p = argparse.ArgumentParser(
        description="Generate project architecture documentation with file summaries as JSON."
    )

    # No arguments needed - always generates timestamped output in current directory

    return p.parse_args(argv)


def main():
    """Main entry point for the architecture documentation generator."""
    args = parse_args()

    # Use current working directory as project root
    root = Path.cwd()

    # Generate timestamped output filename
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_filename = f"project_architecture_{timestamp}.json"
    out_path = root / out_filename

    # Generate the documentation
    try:
        write_architecture_json(root=root, out_path=out_path)
    except KeyboardInterrupt:
        print("\nAborted by user.", file=sys.stderr)
        sys.exit(130)


# Entry point when script is run directly
if __name__ == "__main__":
    main()
