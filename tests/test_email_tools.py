"""Compatibility entry point for the old monolithic test module.

The tests now live in focused modules under ``tests/``. Run
``python3 -m unittest discover -s tests -p 'test*.py'`` for the full suite.
Direct ``python3 -m unittest tests.test_email_tools`` still loads the split suite.
"""

from __future__ import annotations

import unittest

_SPLIT_TEST_MODULES = [
    "tests.test_email_classification",
    "tests.test_email_tool_runtime",
    "tests.test_email_cli",
    "tests.test_agent_cli",
    "tests.test_real_eval_helpers",
    "tests.test_sqlite_persistence",
    "tests.test_auth_provider_tools",
    "tests.test_qq_imap_provider",
]


def load_tests(loader: unittest.TestLoader, standard_tests: unittest.TestSuite, pattern: str | None) -> unittest.TestSuite:
    if pattern is not None:
        return unittest.TestSuite()
    suite = unittest.TestSuite()
    for module_name in _SPLIT_TEST_MODULES:
        suite.addTests(loader.loadTestsFromName(module_name))
    return suite
