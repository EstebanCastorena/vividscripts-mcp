"""Security regression tests.

Each file pins one of the audit findings tracked in
``Projects/VividScripts/MCP/Security Review/2026-05-17 Comprehensive Repo Audit.md``.
Tests here document *attacker* shapes the production code must reject:
the assertion side is "the bad input gets refused", not "the happy path
produces the right value".
"""
