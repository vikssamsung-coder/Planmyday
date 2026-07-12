r"""
mis_reports.py — "MIS & Reports": a LIST of reports a user is allowed to see, each with its
name, last-update time, and a Download button. Reports are never opened, parsed or rendered
in the app — we serve the raw bytes so the original file (formatting, formulas, pivots,
extra sheets) is preserved exactly.

No Microsoft Graph. A SharePoint/OneDrive link shared as "Anyone with the link can view" is
converted to a direct-download URL and fetched with requests.

Admin defines each report (name + link) and who can see it (Admin -> MIS Reports).
"""

import re
import base64
import mimetypes
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests
import streamlit as st

import storage

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
IST = timezone(timedelta(hours=5, minutes=30))
STALE_MINUTES = 30


# ---------- link handling ----------

def to_direct_download(share_url):
    """A 'view' share link -> a URL that returns the file bytes."""
    url = (share_url or "").strip()
    if not url:
        return ""
    host = urlparse(url).netloc.lower()

    if "1drv.ms" in host or "onedrive.live.com" in host:          # OneDrive personal
        b64 = base64.b64encode(url.encode()).decode()
        b64 = b64.replace("/", "_").replace("+", "-").rstrip("=")
        return f"https://api.onedrive.com/v1.0/shares/u!{b64}/root/content"

    if "sharepoint.com" in host:                                   # SharePoint / OneDrive for Business
        p = urlparse(url)
        q = parse_qs(p.query)
        q["download"] = ["1"]
        return urlunparse(p._replace(query=urlencode({k: v[0] for k, v in q.items()})))

    return url


# ---------- last-modified (HEAD only — no download) ----------

def fetch_last_modified(share_url, timeout=15):
    """The source's Last-Modified as a datetime, or None if it doesn't report one."""
    url = to_direct_download(share_url)
    if not url:
        return None
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400 or "last-modified" not in {k.lower() for k in r.headers}:
            # some endpoints refuse HEAD — fall back to a 1-byte ranged GET
            r = requests.get(url, timeout=timeout, allow_redirects=True,
                             headers={"Range": "bytes=0-0"}, stream=True)
            r.close()
        lm = r.headers.get("last-modified")
        return parsedate_to_datetime(lm) if lm else None
    except Exception:
        return None


def refresh_last_modified(report_key, share_url):
    """Re-read the source timestamp and cache it on the row. Returns the datetime or None."""
    ts = fetch_last_modified(share_url)
    now = datetime.now().isoformat(timespec="seconds")
    storage.save_report_fields(
        report_key,
        source_modified_at=(ts.isoformat(timespec="seconds") if ts else ""),
        last_checked_at=now)
    return ts


def _is_stale(last_checked_at):
    if not str(last_checked_at or "").strip():
        return True
    try:
        t = datetime.fromisoformat(str(last_checked_at))
    except Exception:
        return True
    return (datetime.now() - t) > timedelta(minutes=STALE_MINUTES)


# ---------- download (raw bytes — never parsed) ----------

@st.cache_data(ttl=300, show_spinner=False)
def fetch_report(share_url, timeout=60):
    """(bytes, filename, mime) straight from the source. Raises on failure."""
    r = requests.get(to_direct_download(share_url), timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    cd = r.headers.get("content-disposition", "") or ""
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd, re.I)
    name = m.group(1).strip() if m else "report.xlsx"
    mime = XLSX_MIME if name.lower().endswith(".xlsx") else (
        mimetypes.guess_type(name)[0] or "application/octet-stream")
    return r.content, name, mime


# ---------- display helpers ----------

def _fmt_updated(iso_text):
    """'12 Jul 2026, 6:40 PM · 2h ago' (IST), or '—' when the source reports nothing."""
    s = str(iso_text or "").strip()
    if not s:
        return "—"
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(IST)
    delta = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
    secs = max(int(delta.total_seconds()), 0)
    if secs < 3600:
        rel = f"{max(secs // 60, 1)}m ago"
    elif secs < 86400:
        rel = f"{secs // 3600}h ago"
    else:
        rel = f"{secs // 86400}d ago"
    return local.strftime("%d %b %Y, %-I:%M %p") + " · " + rel


def _group_labels():
    try:
        return {t["key"]: t["name"] for t in storage.get_mis_types(active_only=False)}
    except Exception:
        return {}


# ---------- user page ----------

def mis_reports_view(user):
    uk = user.get("user_key", "")
    role = user.get("role", "")
    login_role = user.get("login_role", "")

    st.markdown("### 📊 MIS & Reports")
    st.caption("Reports shared with you. Download opens the original file — nothing is "
               "changed or re-saved.")

    reports = storage.reports_for_user(uk, role, login_role)
    if not reports:
        st.info("No reports are shared with you yet.")
        return

    if st.button("🔄 Refresh update times", key="mr_refresh"):
        with st.spinner("Checking sources…"):
            for r in reports:
                refresh_last_modified(r["report_key"], r.get("source_url", ""))
        st.rerun()

    # lazy refresh: only re-check rows whose cached timestamp is stale
    stale = [r for r in reports if _is_stale(r.get("last_checked_at"))]
    if stale:
        for r in stale:
            refresh_last_modified(r["report_key"], r.get("source_url", ""))
        reports = storage.reports_for_user(uk, role, login_role)

    labels = _group_labels()
    groups = {}
    for r in reports:
        groups.setdefault(str(r.get("mis_key", "") or ""), []).append(r)

    for gkey in sorted(groups, key=lambda k: (k == "", labels.get(k, k))):
        gname = labels.get(gkey, gkey) if gkey else "Other"
        if len(groups) > 1 or gkey:
            st.markdown(f"##### {gname}")
        for r in groups[gkey]:
            rk = r["report_key"]
            c = st.columns([4, 3, 2])
            desc = str(r.get("description", "") or "")
            c[0].markdown(f"**{r.get('name', rk)}**" + (f"  \n<span style='color:#5C6B7A;"
                          f"font-size:12px;'>{desc}</span>" if desc else ""),
                          unsafe_allow_html=True)
            c[1].markdown(f"<span style='color:#5C6B7A;font-size:12.5px;'>"
                          f"{_fmt_updated(r.get('source_modified_at'))}</span>",
                          unsafe_allow_html=True)
            with c[2]:
                if st.button("Download", key=f"mr_dl_{rk}", use_container_width=True):
                    try:
                        with st.spinner("Fetching…"):
                            data, srv_name, mime = fetch_report(r.get("source_url", ""))
                        st.session_state[f"mr_data_{rk}"] = (
                            data, (r.get("file_name") or srv_name), mime)
                    except Exception:
                        st.error("Couldn't fetch this report. The link may not be shared "
                                 "as “Anyone with the link can view”, or the file has moved.")
                got = st.session_state.get(f"mr_data_{rk}")
                if got:
                    st.download_button("Save file", data=got[0], file_name=got[1],
                                       mime=got[2], key=f"mr_save_{rk}",
                                       use_container_width=True)
