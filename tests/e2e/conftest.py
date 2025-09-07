# tests/e2e/conftest.py
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import pytest

DEBUG = os.getenv("E2E_DEBUG", "0") in ("1", "true", "yes", "on")

ROOT = Path(__file__).resolve().parents[2]  # repo root
ENV_PATH = ROOT / ".env"
ENV_LOCAL_PATH = ROOT / ".env.local"

def _log(msg: str):
    if DEBUG:
        print(f"[E2E-CONFTEST] {msg}")

# Show basic context early
_log(f"cwd={Path.cwd()}")
_log(f"repo_root={ROOT}")
_log(f"sys.path[0]={sys.path[0]}")
_log(f".env exists? {ENV_PATH.exists()} -> {ENV_PATH}")
_log(f".env.local exists? {ENV_LOCAL_PATH.exists()} -> {ENV_LOCAL_PATH}")

# Load .env first, then .env.local overriding it
loaded_env = load_dotenv(ENV_PATH, override=False)
loaded_local = load_dotenv(ENV_LOCAL_PATH, override=True)

_log(f"load_dotenv(.env) returned {loaded_env}")
_log(f"load_dotenv(.env.local) returned {loaded_local}")

def pytest_report_header(config):
    return (
        f"E2E env -> RUN_LLM_TESTS={os.getenv('RUN_LLM_TESTS')!r}, "
        f"OPENAI_API_KEY={'set' if os.getenv('OPENAI_API_KEY') else 'missing'}, "
        f"LLM_TEST_MODEL={os.getenv('LLM_TEST_MODEL')!r}, "
        f"E2E_DEBUG={os.getenv('E2E_DEBUG')!r}"
    )

def pytest_collection_modifyitems(config, items):
    run_llm = os.getenv("RUN_LLM_TESTS", "").strip().lower() in ("1","true","yes","y","on")
    has_key = bool(os.getenv("OPENAI_API_KEY"))

    _log(f"post-load env -> RUN_LLM_TESTS_raw={os.getenv('RUN_LLM_TESTS')!r}, parsed={run_llm}, OPENAI_API_KEY_set={has_key}, MODEL={os.getenv('LLM_TEST_MODEL')!r}")

    if run_llm and has_key:
        _log("E2E gating: enabled (no skip markers added).")
        return

    reason = "Set OPENAI_API_KEY and RUN_LLM_TESTS=1 in .env.local (or export RUN_LLM_TESTS=1) to run live E2E LLM tests."
    for item in items:
        if item.nodeid.startswith("tests/e2e/"):
            _log(f"Skipping {item.nodeid} because run_llm={run_llm}, has_key={has_key}")
            item.add_marker(pytest.mark.skip(reason=reason))
