import os

import pytest

from researcher.llm import load_env

load_env()


def pytest_collection_modifyitems(config, items):
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return
    skip = pytest.mark.skip(reason="GEMINI_API_KEY not set")
    for item in items:
        if "llm" in item.keywords:
            item.add_marker(skip)
