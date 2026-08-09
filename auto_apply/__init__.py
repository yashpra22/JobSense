"""
auto_apply — JobSense × Browser-Use Auto-Apply Integration Module

This module reads high-match jobs from seen_jobs.db and uses the browser-use
AI agent to automatically fill and submit job applications on Greenhouse,
Lever, Ashby, and Workday portals.

Usage:
    python -m auto_apply.run_auto_apply [--dry-run] [--max-jobs N] [--min-score S]
    # or directly:
    python auto_apply/run_auto_apply.py
"""
