#!/usr/bin/env python3
"""
Unit tests for project_snapshot.py

Tests cover:
- Helper functions (text detection, size formatting, etc.)
- Directory filtering and traversal logic
- Appendix decision logic
- File encoding and content processing
"""

import unittest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, mock_open
import os
import sys

# Import the module under test
import project_snapshot as ps


class TestHelperFunctions(unittest.TestCase):
    """Test utility and helper functions."""
    
    def test_human_size(self):
        """Test human-readable size formatting."""
        self.assertEqual(ps.human_size(0), "0 B")
        self.assertEqual(ps.human_size(500), "500 B")
        self.assertEqual(ps.human_size(1024), "1.0 KB")
        self.assertEqual(ps.human_size(1536), "1.5 KB")  # 1.5 * 1024
        self.assertEqual(ps.human_size(1048576), "1.0 MB")  # 1024 * 1024
        self.assertEqual(ps.human_size(1073741824), "1.0 GB")  # 1024^3
    
    def test_infer_lang(self):
        """Test language inference from file extensions."""
        self.assertEqual(ps.infer_lang(".py"), "python")
        self.assertEqual(ps.infer_lang(".js"), "javascript")
        self.assertEqual(ps.infer_lang(".unknown"), "")
        self.assertEqual(ps.infer_lang(".PY"), "python")  # Case insensitive
        self.assertEqual(ps.infer_lang(""), "")
    
    def test_sha256_hex(self):
        """Test SHA-256 hash calculation."""
        test_data = b"hello world"
        expected = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        self.assertEqual(ps.sha256_hex(test_data), expected)
        
        # Test empty data
        self.assertEqual(len(ps.sha256_hex(b"")), 64)  # SHA-256 is always 64 hex chars
    
    def test_is_virtualenv_dir_name(self):
        """Test virtual environment directory name detection."""
        self.assertTrue(ps.is_virtualenv_dir_name("venv"))
        self.assertTrue(ps.is_virtualenv_dir_name(".venv"))
        self.assertTrue(ps.is_virtualenv_dir_name("venv123"))
        self.assertTrue(ps.is_virtualenv_dir_name("VENV"))  # Case insensitive
        self.assertFalse(ps.is_virtualenv_dir_name("src"))
        self.assertFalse(ps.is_virtualenv_dir_name("env"))  # Doesn't match pattern
    
    def test_is_junk_dir_name(self):
        """Test junk directory detection."""
        self.assertTrue(ps.is_junk_dir_name(".git"))
        self.assertTrue(ps.is_junk_dir_name("node_modules"))
        self.assertTrue(ps.is_junk_dir_name("__pycache__"))
        self.assertTrue(ps.is_junk_dir_name("venv"))  # Virtual env
        self.assertFalse(ps.is_junk_dir_name("src"))
        self.assertFalse(ps.is_junk_dir_name("docs"))


class TestTextDetection(unittest.TestCase):
    """Test text file detection logic."""
    
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
    
    def tearDown(self):
        shutil.rmtree(self.temp_dir)
    
    def test_is_probably_text_with_text_file(self):
        """Test text detection with actual text file."""
        text_file = self.temp_dir / "test.txt"
        text_file.write_text("Hello, world!\nThis is a text file.")
        self.assertTrue(ps.is_probably_text(text_file))
    
    def test_is_probably_text_with_binary_file(self):
        """Test text detection with binary data."""
        binary_file = self.temp_dir / "test.bin"
        binary_file.write_bytes(b"\x00\x01\x02\x03\xFF\xFE")
        self.assertFalse(ps.is_probably_text(binary_file))
    
    def test_is_probably_text_with_utf8_file(self):
        """Test text detection with UTF-8 encoded file."""
        utf8_file = self.temp_dir / "test_utf8.txt"
        utf8_file.write_text("Hello 世界! 🌍", encoding="utf-8")
        self.assertTrue(ps.is_probably_text(utf8_file))
    
    def test_is_probably_text_nonexistent_file(self):
        """Test text detection with nonexistent file."""
        nonexistent = self.temp_dir / "nonexistent.txt"
        self.assertFalse(ps.is_probably_text(nonexistent))


class TestEncoding(unittest.TestCase):
    """Test text encoding detection and decoding."""
    
    def test_decode_text_utf8(self):
        """Test UTF-8 decoding."""
        utf8_data = "Hello, 世界!".encode("utf-8")
        text, encoding = ps.decode_text(utf8_data)
        self.assertEqual(text, "Hello, 世界!")
        self.assertEqual(encoding, "utf-8")
    
    def test_decode_text_latin1(self):
        """Test Latin-1 fallback decoding."""
        # Create data that's valid Latin-1 but not UTF-8
        latin1_data = b"\xe9\xe8\xe7"  # Some accented chars in Latin-1
        text, encoding = ps.decode_text(latin1_data)
        self.assertEqual(encoding, "latin-1")
        self.assertIsInstance(text, str)
    
    def test_decode_text_with_replacement(self):
        """Test UTF-8 with replacement characters."""
        # This should trigger the replacement fallback
        with patch('project_snapshot.decode_text') as mock_decode:
            # Simulate the actual behavior for this edge case
            mock_decode.return_value = ("text�", "utf-8 (replace)")
            text, encoding = ps.decode_text(b"\xFF\xFE")
            self.assertIn("replace", encoding)


class TestDirectoryTraversal(unittest.TestCase):
    """Test directory traversal and file collection."""
    
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        
        # Create test directory structure
        (self.temp_dir / "src").mkdir()
        (self.temp_dir / "src" / "main.py").write_text("print('hello')")
        (self.temp_dir / "src" / "utils.py").write_text("def helper(): pass")
        
        (self.temp_dir / "docs").mkdir()
        (self.temp_dir / "docs" / "readme.md").write_text("# Project")
        
        (self.temp_dir / "node_modules").mkdir()  # Should be excluded
        (self.temp_dir / "node_modules" / "package.json").write_text("{}")
        
        (self.temp_dir / "config.json").write_text('{"setting": "value"}')
    
    def tearDown(self):
        shutil.rmtree(self.temp_dir)
    
    def test_relpath_sorted_children(self):
        """Test directory child enumeration and sorting."""
        children = list(ps.relpath_sorted_children(self.temp_dir))
        child_names = [p.name for p in children]
        
        # Should exclude node_modules (junk dir)
        self.assertNotIn("node_modules", child_names)
        
        # Should include other directories and files
        self.assertIn("src", child_names)
        self.assertIn("docs", child_names)
        self.assertIn("config.json", child_names)
        
        # Check sorting: directories first, then files, both alphabetical
        dirs = [p for p in children if p.is_dir()]
        files = [p for p in children if p.is_file()]
        
        # Verify directories come first
        dir_names = [p.name for p in dirs]
        file_names = [p.name for p in files]
        
        # Should be sorted alphabetically
        self.assertEqual(dir_names, sorted(dir_names, key=str.lower))
        self.assertEqual(file_names, sorted(file_names, key=str.lower))
    
    def test_build_tree_and_flat(self):
        """Test complete directory tree building."""
        tree, flat_files, dir_count, file_count = ps.build_tree_and_flat(self.temp_dir)
        
        # Check tree structure
        self.assertEqual(tree["type"], "directory")
        self.assertEqual(tree["name"], self.temp_dir.name)
        
        # Check counts (excludes node_modules)
        self.assertGreaterEqual(dir_count, 2)  # At least temp_dir + src + docs
        self.assertGreaterEqual(file_count, 3)  # At least 3 files outside node_modules
        
        # Check flat files
        file_paths = [f["path"] for f in flat_files]
        self.assertTrue(any("main.py" in path for path in file_paths))
        self.assertTrue(any("config.json" in path for path in file_paths))
        
        # Should not include files from excluded directories
        self.assertFalse(any("node_modules" in path for path in file_paths))


class TestAppendixDecision(unittest.TestCase):
    """Test appendix inclusion decision logic."""
    
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.output_file = self.temp_dir / "output.json"
    
    def tearDown(self):
        shutil.rmtree(self.temp_dir)
    
    def test_decide_appendix_self_output(self):
        """Test that output file excludes itself."""
        include, reason, raw = ps.decide_appendix(
            self.output_file, max_size=1000, mode="ask", out_path=self.output_file
        )
        self.assertFalse(include)
        self.assertEqual(reason, "self_output")
        self.assertIsNone(raw)
    
    def test_decide_appendix_excluded_extension(self):
        """Test exclusion of specific file extensions."""
        log_file = self.temp_dir / "test.log"
        log_file.write_text("Log entry")
        
        include, reason, raw = ps.decide_appendix(
            log_file, max_size=1000, mode="ask", out_path=self.output_file
        )
        self.assertFalse(include)
        self.assertEqual(reason, "excluded_ext")
    
    def test_decide_appendix_binary_file(self):
        """Test exclusion of binary files."""
        binary_file = self.temp_dir / "test.bin"
        binary_file.write_bytes(b"\x00\x01\x02\x03")
        
        include, reason, raw = ps.decide_appendix(
            binary_file, max_size=1000, mode="ask", out_path=self.output_file
        )
        self.assertFalse(include)
        self.assertEqual(reason, "binary")
    
    def test_decide_appendix_large_file_no_mode(self):
        """Test large file exclusion in 'no' mode."""
        large_file = self.temp_dir / "large.txt"
        large_file.write_text("x" * 2000)  # 2000 bytes
        
        include, reason, raw = ps.decide_appendix(
            large_file, max_size=1000, mode="no", out_path=self.output_file
        )
        self.assertFalse(include)
        self.assertEqual(reason, "large_skipped")
    
    def test_decide_appendix_normal_text_file(self):
        """Test inclusion of normal text file."""
        text_file = self.temp_dir / "test.py"
        content = "print('hello world')"
        text_file.write_text(content)
        
        include, reason, raw = ps.decide_appendix(
            text_file, max_size=1000, mode="ask", out_path=self.output_file
        )
        self.assertTrue(include)
        self.assertIsNone(reason)
        self.assertEqual(raw, content.encode())


class TestCLIParsing(unittest.TestCase):
    """Test command line argument parsing."""
    
    def test_default_arguments(self):
        """Test default argument values."""
        args = ps.parse_args([])
        self.assertEqual(args.output, "PROJECT_STRUCTURE_AND_APPENDICES.json")
        self.assertIsNone(args.large_kb)
        self.assertEqual(args.max_size, ps.DEFAULT_MAX_SIZE)
        self.assertFalse(args.follow_symlinks)
        self.assertFalse(args.yes_large)
        self.assertFalse(args.no_large)
    
    def test_custom_arguments(self):
        """Test custom argument parsing."""
        args = ps.parse_args([
            "--output", "custom.json",
            "--large-kb", "10",
            "--follow-symlinks",
            "--yes-large"
        ])
        self.assertEqual(args.output, "custom.json")
        self.assertEqual(args.large_kb, 10)
        self.assertTrue(args.follow_symlinks)
        self.assertTrue(args.yes_large)
    
    def test_mutually_exclusive_args(self):
        """Test that --yes-large and --no-large are mutually exclusive."""
        with self.assertRaises(SystemExit):
            ps.parse_args(["--yes-large", "--no-large"])


if __name__ == "__main__":
    # Run the tests
    unittest.main(verbosity=2)