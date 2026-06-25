r"""
paths.py — one place that decides where the workspace lives.

Resolution order:
  1. STORAGE_BASE_DIR env var (explicit override, used in tests/CI)
  2. Windows  -> D:\Sarthi - Plan My Day   (the production location)
              -> falls back to C:\Sarthi - Plan My Day if D: isn't present
  3. Anything else (Mac/Linux dev) -> ./Sarthi - Plan My Day next to the app

The folder name is deliberately the same everywhere so docs and support are simple.
"""

import os
import sys

WORKSPACE_NAME = "Sarthi - Plan My Day"


def base_dir():
    override = os.environ.get("STORAGE_BASE_DIR")
    if override:
        return override

    if sys.platform.startswith("win"):
        for drive in ("D:", "C:"):
            if os.path.isdir(drive + os.sep):
                return os.path.join(drive + os.sep, WORKSPACE_NAME)
        return os.path.join("C:" + os.sep, WORKSPACE_NAME)

    # dev fallback (Mac/Linux): keep it beside the app, not in $HOME
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, WORKSPACE_NAME)


def common_dir():
    return os.path.join(base_dir(), "_common")


def repo_dir():
    """The app's own code directory — i.e. the GitHub repo root, both locally and on
    Streamlit Cloud (which runs from a clone of the repo)."""
    return os.path.dirname(os.path.abspath(__file__))


def role_prompts_dir():
    """Role prompts live in the repo (version-controlled via GitHub): edit + commit +
    push, and the deployed app reads the new version after it redeploys."""
    return os.path.join(repo_dir(), "role_prompts")


def workspace_role_prompts_dir():
    """Legacy/local fallback location inside the data workspace (read-only fallback)."""
    return os.path.join(common_dir(), "role_prompts")


def role_cache_dir():
    """Fast local cache of the repo's role prompts (mirrors the committed files; refreshed
    whenever a newer commit changes them). Lives in the data workspace, not the repo."""
    return os.path.join(common_dir(), "role_cache")


def user_dir(user_key):
    return os.path.join(base_dir(), user_key)


def user_briefs_dir(user_key):
    return os.path.join(user_dir(user_key), "briefs")


def user_reports_dir(user_key):
    return os.path.join(user_dir(user_key), "reports")
