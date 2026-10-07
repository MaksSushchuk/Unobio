"""End-to-end workflow: input -> researcher -> analytics -> writer -> Report (web/src/types.ts). See pipeline.py."""

from .pipeline import run
from .report_schema import Report

__all__ = ["Report", "run"]
