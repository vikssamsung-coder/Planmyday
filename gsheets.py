"""Google Sheets backend (single-spreadsheet model) — persistent storage for the hosted
or local app. Activates only when Google credentials + a SHEET_ID are in st.secrets;
otherwise the app uses local Excel (see storage.py).

WHY ONE SHEET YOU OWN:
A service account on a consumer Google account has ZERO Drive storage quota, so it can't
*create* files (you get "storage quota exceeded") — not even inside a folder you own,
because the service account would still be the file's owner. The fix: YOU create one
Google Sheet (you own it, your quota applies) and share it with the service account as
Editor. The app only OPENS that sheet and adds TABS / writes rows — which modifies your
existing file rather than creating new ones, so the quota error never occurs.

Layout inside the one sheet:
- tabular data -> a tab named "<title>__<store>" (e.g. "_common__users_master",
  "arjun__tasks"). storage.py passes (title, store) derived from each file path.
- prompts / text -> a tab named "prompt__<name>" with the full text in cell A1.
"""

import threading
import time
import pandas as pd

SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
          "https://www.googleapis.com/auth/drive"]

_TL = threading.local()     # per-thread client + spreadsheet handle (SSL isn't thread-safe)
_CACHE = {}                 # (title, tab) -> (ts, DataFrame)
_TTL = 8


def _secrets():
    try:
        import streamlit as st
        if "gcp_service_account" in st.secrets and "SHEET_ID" in st.secrets:
            return dict(st.secrets["gcp_service_account"]), str(st.secrets["SHEET_ID"])
    except Exception:
        pass
    return None, None


def enabled():
    info, sheet_id = _secrets()
    return bool(info and sheet_id)


def _sheet_id():
    return _secrets()[1]


def _client():
    gc = getattr(_TL, "gc", None)
    if gc is None:
        import gspread
        from google.oauth2.service_account import Credentials
        info, _ = _secrets()
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        gc = gspread.authorize(creds)
        _TL.gc = gc
    return gc


def _book():
    """The one spreadsheet (opened by ID), per thread. Never created by the app."""
    bk = getattr(_TL, "book", None)
    if bk is None:
        bk = _client().open_by_key(_sheet_id())
        _TL.book = bk
    return bk


def _tab(name, cols=1, rows=100):
    """Get a worksheet by name, creating the TAB if missing (modifies the existing file —
    no new Drive file, so no quota issue)."""
    import gspread
    bk = _book()
    try:
        return bk.worksheet(name)
    except gspread.WorksheetNotFound:
        return bk.add_worksheet(title=name, rows=max(rows, 100), cols=max(cols, 12))


def _tabname(title, tab):
    return f"{title}__{tab}"[:99]


# ----------------------------------------------------------------- tabular data

def read_df(title, tab, columns):
    key = (title, tab)
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _TTL:
        return hit[1].copy()
    import gspread
    try:
        bk = _book()
        try:
            ws = bk.worksheet(_tabname(title, tab))
        except gspread.WorksheetNotFound:
            df = pd.DataFrame(columns=columns)
            _CACHE[key] = (time.time(), df)
            return df.copy()
        values = ws.get_all_values()
    except Exception:
        return pd.DataFrame(columns=columns)
    if not values:
        df = pd.DataFrame(columns=columns)
    else:
        header = values[0]
        width = len(header)
        rows = [r + [""] * (width - len(r)) for r in values[1:]]
        df = pd.DataFrame(rows, columns=header)
    for c in columns:
        if c not in df.columns:
            df[c] = ""
    df = df[columns].fillna("")
    _CACHE[key] = (time.time(), df)
    return df.copy()


def set_df(title, tab, df, columns):
    for c in columns:
        if c not in df.columns:
            df[c] = ""
    ws = _tab(_tabname(title, tab), cols=len(columns) + 2, rows=len(df) + 20)
    data = [list(columns)] + df[columns].astype(str).values.tolist()
    ws.clear()
    ws.update(range_name="A1", values=data, value_input_option="RAW")
    _CACHE[(title, tab)] = (time.time(), df[columns].astype(str).fillna(""))


# ----------------------------------------------------------------- prompt text (tabs)

def _prompt_tab(name):
    return f"prompt__{name}"[:99]


def text_exists(name):
    import gspread
    try:
        _book().worksheet(_prompt_tab(name))
        return True
    except gspread.WorksheetNotFound:
        return False
    except Exception:
        return False


def read_text(name):
    import gspread
    try:
        ws = _book().worksheet(_prompt_tab(name))
        vals = ws.get_all_values()
        return vals[0][0] if vals and vals[0] else ""
    except gspread.WorksheetNotFound:
        return ""
    except Exception:
        return ""


def write_text(name, content):
    ws = _tab(_prompt_tab(name), cols=1, rows=1)
    ws.clear()
    ws.update(range_name="A1", values=[[content]], value_input_option="RAW")


# ----------------------------------------------------------------- diagnostics

def health_check():
    if not enabled():
        return False, "Using local storage."
    try:
        _book()
        return True, "Data saved to cloud."
    except Exception as e:
        msg = str(e)
        if "PERMISSION_DENIED" in msg or "403" in msg:
            return False, "Can't reach cloud storage (permission)."
        if "not found" in msg.lower() or "404" in msg:
            return False, "Can't reach cloud storage (not found)."
        return False, "Can't reach cloud storage."
