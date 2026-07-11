r"""
dump_sender.py — the "Sarthi" report section. Two sub-tabs:

  * Send Dumps  — queue one or more dumps, each a FOLDER (or file) PATH on this machine.
                  Files are read from disk (NOT uploaded), zipped, and emailed via Outlook.
                  A dump larger than the part size is split LOCALLY into parts; each part is
                  emailed, then the temp zip/parts are DELETED from the machine. The number
                  of parts is computed automatically and stamped in the email so the receiver
                  can reassemble. Each dump = its own batch = its own email(s). Desktop only
                  (needs Outlook + local files).

  * Request Report — pick a report; the request is saved to NEON (shared), so it works from
                  the cloud app too. The Sarthi receiver reads Neon and triggers the report.

No encryption (key-free) for now — bodies are marked `encryption: none`. No dry-run.
"""

import os
import json
import time
import socket
import hashlib
import zipfile
import shutil
from datetime import datetime

import streamlit as st

import paths
import storage

DEFAULT_RECEIVER = os.getenv("SARTHI_RECEIVER_EMAIL", "growth@bigul.co")
MULTIPART_KEYWORD = "[CDP MULTIPART]"
PART_MB = int(os.getenv("SARTHI_PART_MB", "8"))          # auto-split threshold; not user-set
_PART_BYTES = PART_MB * 1024 * 1024


# ---------- small helpers ----------

def _working(label="Working…"):
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        ph = st.empty()
        ph.markdown(
            "<div style='display:flex;align-items:center;gap:9px;padding:4px 0;'>"
            "<span class='pmd-ring'></span>"
            f"<span style='color:#1B7F4B;font-weight:600;font-size:0.9rem;'>{label}</span></div>"
            "<style>@keyframes pmdspin{to{transform:rotate(360deg)}}"
            ".pmd-ring{width:16px;height:16px;border:2.5px solid #B7E4C7;"
            "border-top-color:#1B7F4B;border-radius:50%;display:inline-block;"
            "animation:pmdspin .8s linear infinite;}</style>", unsafe_allow_html=True)
        try:
            yield
        finally:
            ph.empty()

    return _cm()


def _work_dir():
    d = os.path.join(paths.common_dir(), "dump_sender")
    os.makedirs(d, exist_ok=True)
    return d


def _history_path():
    return os.path.join(_work_dir(), "send_history.json")


def _now_id():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(b):
    h = hashlib.sha256(); h.update(b); return h.hexdigest()


def _human(n):
    n = float(n)
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.0f}{u}" if u == "B" else f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def _resolve_files(path):
    """A pasted path -> list of files. Folder = every file directly inside it; a file = itself."""
    p = os.path.expanduser(str(path or "").strip().strip('"'))
    if not p:
        raise ValueError("empty path")
    if os.path.isdir(p):
        files = [os.path.join(p, f) for f in sorted(os.listdir(p))
                 if os.path.isfile(os.path.join(p, f)) and not f.startswith("~$")]
        if not files:
            raise ValueError(f"no files found in folder: {p}")
        return files
    if os.path.isfile(p):
        return [p]
    raise ValueError(f"path not found: {p}")


def _log(record):
    try:
        hist = []
        if os.path.exists(_history_path()):
            with open(_history_path(), "r", encoding="utf-8") as fh:
                hist = json.load(fh)
        hist.insert(0, record)
        with open(_history_path(), "w", encoding="utf-8") as fh:
            json.dump(hist[:200], fh, indent=2)
    except Exception:
        pass


# ---------- Outlook ----------

def _send_outlook(to_email, subject, body, attachment_path):
    try:
        import pythoncom
        import win32com.client as win32
    except Exception:
        return False, "Outlook / pywin32 not available (Windows + Outlook required)."
    try:
        pythoncom.CoInitialize()
        outlook = win32.Dispatch("Outlook.Application")
        mail = outlook.CreateItem(0)
        mail.To = to_email
        mail.Subject = subject
        mail.Body = body
        mail.Attachments.Add(str(os.path.abspath(attachment_path)))
        mail.Send()
        return True, "sent"
    except Exception as e:
        return False, f"Outlook send failed: {e}"
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def _delete_from_sent_items(batch_id, tries=3, wait=2.0):
    try:
        import pythoncom
        import win32com.client as win32
    except Exception:
        return 0
    deleted = 0
    try:
        pythoncom.CoInitialize()
        outlook = win32.Dispatch("Outlook.Application")
        sent = outlook.GetNamespace("MAPI").GetDefaultFolder(5)
        for _ in range(tries):
            for it in list(sent.Items):
                try:
                    if batch_id in str(it.Subject):
                        it.Delete(); deleted += 1
                except Exception:
                    continue
            if deleted:
                break
            time.sleep(wait)
    except Exception:
        pass
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
    return deleted


# ---------- dump build + send ----------

def _zip_files(files, batch_id):
    zdir = os.path.join(_work_dir(), batch_id)
    os.makedirs(zdir, exist_ok=True)
    zpath = os.path.join(zdir, batch_id + ".zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, os.path.basename(f))
    return zpath


def _split_auto(path, batch_id):
    """Split into parts of PART_MB. Part COUNT is computed here, never set by the user."""
    parts = []
    with open(path, "rb") as f:
        i = 1
        while True:
            chunk = f.read(_PART_BYTES)
            if not chunk:
                break
            pp = os.path.join(os.path.dirname(path), f"{batch_id}.part{i:03d}")
            with open(pp, "wb") as o:
                o.write(chunk)
            parts.append({"part_no": i, "path": pp, "file_name": os.path.basename(pp),
                          "size_bytes": len(chunk), "sha256": _sha256_bytes(chunk)})
            i += 1
    return parts


def _body(dtype, batch_id, part, total, pkg_name, pkg_sha, files, sender, receiver, notes):
    lines = [
        "SARTHI CDP MULTIPART FILE", "",
        "encryption: none",
        f"batch_id: {batch_id}",
        f"part: {part['part_no']}/{total}",
        f"part_no: {part['part_no']}",
        f"total_parts: {total}", "",
        f"final_package_name: {pkg_name}",
        f"final_package_sha256: {pkg_sha}",
        f"part_file_name: {part['file_name']}",
        f"part_sha256: {part['sha256']}",
        f"part_size_bytes: {part['size_bytes']}", "",
        f"dump_type_key: {dtype.get('key','')}",
        f"dump_type_handler: {dtype.get('handler', dtype.get('key',''))}",
        f"report_name: {dtype.get('name','')}",
        f"sender_email: {sender}",
        f"to_email: {receiver}",
        f"machine_name: {socket.gethostname()}",
        f"created_at: {_now_text()}", "",
        f"source_files: {', '.join(os.path.basename(x) for x in files)}",
    ]
    if str(notes or "").strip():
        lines += ["", "notes:", notes.strip()]
    return "\n".join(lines)


def send_one_dump(dtype, path, sender_email, receiver_email, notes, delete_sent=True):
    """Zip the folder/file at `path`, auto-split if big, email each part, then delete the
    local zip + parts. Returns a result dict. The user's SOURCE files are never touched."""
    files = _resolve_files(path)                       # raises on bad path
    batch_id = (dtype.get("key", "dump") + "_" + _now_id())
    zpath = _zip_files(files, batch_id)
    pkg_sha = _sha256_file(zpath)
    pkg_name = os.path.basename(zpath)
    size = os.path.getsize(zpath)

    if size > _PART_BYTES:
        parts = _split_auto(zpath, batch_id)           # count computed automatically
    else:
        parts = [{"part_no": 1, "path": zpath, "file_name": pkg_name,
                  "size_bytes": size, "sha256": pkg_sha}]
    total = len(parts)

    sent, failed = 0, []
    for part in parts:
        subject = (f"{MULTIPART_KEYWORD} {dtype.get('name','Dump')} | Batch={batch_id} "
                   f"| Part={part['part_no']}/{total} | Sender={sender_email}")
        body = _body(dtype, batch_id, part, total, pkg_name, pkg_sha, files,
                     sender_email, receiver_email, notes)
        ok, msg = _send_outlook(receiver_email, subject, body, part["path"])
        if ok:
            sent += 1
        else:
            failed.append({"part": part["part_no"], "msg": msg})

    if not failed and delete_sent:
        _delete_from_sent_items(batch_id)
    # ALWAYS clean up the temp zip + parts we created locally (source files untouched)
    shutil.rmtree(os.path.join(_work_dir(), batch_id), ignore_errors=True)

    res = {"batch_id": batch_id, "type": dtype.get("name", ""), "files": len(files),
           "package_size": size, "total_parts": total, "sent": sent,
           "failed": len(failed), "failures": failed, "receiver": receiver_email}
    _log({"at": _now_text(), "kind": "dump", **{k: res[k] for k in
          ("batch_id", "type", "files", "total_parts", "sent", "failed", "receiver")}})
    return res


# ---------- UI ----------

def sarthi_view(user):
    uk = user.get("user_key", "")
    st.markdown("### 📮 Sarthi — reports")
    tab_send, tab_req = st.tabs(["📤 Send Dumps", "📋 Request Report"])
    with tab_send:
        _send_dumps_ui(user, uk)
    with tab_req:
        _request_report_ui(user, uk)


def _email_row(uk):
    saved = storage.get_user_email(uk)
    c = st.columns(2)
    sender = c[0].text_input("Your email", value=saved, placeholder="name@bigul.co",
                             key="sarthi_from")
    receiver = c[1].text_input("Sarthi email", value=DEFAULT_RECEIVER, key="sarthi_to")
    if sender.strip() and sender.strip() != saved:
        try:
            storage.set_user_email(uk, sender.strip())
        except Exception:
            pass
    return sender.strip(), receiver.strip()


def _send_dumps_ui(user, uk):
    st.caption("Queue one or more dumps. Each is a folder (or file) PATH on this machine — "
               "files are read from disk, zipped, and emailed. Large dumps are split "
               "automatically; parts are cleaned up after sending. Each dump = its own email(s).")
    if storage._on_cloud_host():
        st.info("Sending dumps needs local files + Outlook, so it runs on the desktop app "
                "only. Use **Request Report** here on the cloud.")
        return

    st.info("Encryption is OFF for now — dumps are sent as plain zips.")
    types = storage.get_dump_types(active_only=True)
    if not types:
        st.warning("No dump types configured. Ask an admin to add one (Admin → Registries).")
        return
    tlabels = [t["name"] for t in types]

    queue = st.session_state.setdefault("dump_queue", [])

    # add to queue
    ca = st.columns([2, 4, 1])
    dsel = ca[0].selectbox("Dump type", tlabels, key="dq_type")
    dpath = ca[1].text_input("Folder or file path", key="dq_path",
                             placeholder=r"e.g. D:\Dumps\LeadSquared\2026-07")
    with ca[2]:
        st.write("")
        if st.button("Add", key="dq_add", use_container_width=True):
            if dpath.strip():
                queue.append({"type_name": dsel, "path": dpath.strip()})
                st.rerun()
            else:
                st.warning("Paste a path first.")

    if queue:
        st.markdown("##### Queued dumps")
        for i, item in enumerate(list(queue)):
            rc = st.columns([2, 5, 1])
            rc[0].markdown(f"**{item['type_name']}**")
            rc[1].markdown(f"`{item['path']}`")
            if rc[2].button("✕", key=f"dq_rm_{i}"):
                queue.pop(i); st.rerun()

    sender, receiver = _email_row(uk)
    delete_sent = st.checkbox("Remove sent emails from Sent Items", value=True, key="dq_delsent")

    if st.button("Send all dumps", type="primary", key="dq_send", disabled=not queue):
        if not sender or not receiver:
            st.error("Your email and Sarthi email are required.")
            return
        by_name = {t["name"]: t for t in types}
        ok_n, fail_n = 0, 0
        for item in list(queue):
            dtype = by_name.get(item["type_name"], {"key": "generic", "name": item["type_name"]})
            with _working(f"Sending {item['type_name']}…"):
                try:
                    res = send_one_dump(dtype, item["path"], sender, receiver, "",
                                        delete_sent=delete_sent)
                except Exception as e:
                    st.error(f"{item['type_name']} — {e}")
                    fail_n += 1
                    continue
            if res["failed"] == 0:
                ok_n += 1
                st.success(f"✅ {item['type_name']}: {res['files']} file(s) → "
                           f"{_human(res['package_size'])} zip in {res['total_parts']} "
                           f"email(s) to {receiver}. Batch {res['batch_id']}")
            else:
                fail_n += 1
                st.error(f"{item['type_name']}: {res['sent']}/{res['total_parts']} parts sent, "
                         + "; ".join(f"part {x['part']}: {x['msg']}" for x in res["failures"]))
        if ok_n and not fail_n:
            st.session_state["dump_queue"] = []
            st.rerun()


def _request_report_ui(user, uk):
    st.caption("Pick a report to request. The request is saved to Neon (shared), so it works "
               "from the cloud app too — the Sarthi receiver reads it and runs the report, "
               "then emails it back to you.")
    types = storage.get_mis_types(active_only=True)
    if not types:
        st.warning("No reports configured yet. Ask an admin to add one (Admin → Registries).")
        return
    labels = [t["name"] for t in types]
    sel = st.selectbox("Report", labels, key="rr_sel")
    mis = types[labels.index(sel)]
    hint = str(mis.get("params_hint", "") or "")
    if hint:
        st.caption("Parameters: " + hint)

    saved = storage.get_user_email(uk)
    email = st.text_input("Your email (where Sarthi sends the report)", value=saved,
                          placeholder="name@bigul.co", key="rr_email")
    if email.strip() and email.strip() != saved:
        try:
            storage.set_user_email(uk, email.strip())
        except Exception:
            pass
    params = st.text_area("Parameters (e.g. date range, filters)", height=80, key="rr_params",
                          placeholder=hint or "e.g. 1–30 Jun 2026, Kolkata team")

    if st.button("Send request", type="primary", key="rr_send"):
        if not email.strip():
            st.error("Your email is required — the report is sent there.")
            return
        source = "cloud" if storage._on_cloud_host() else "desktop"
        with _working("Saving request…"):
            try:
                rid = storage.submit_report_request(uk, mis.get("key", ""), sel, params.strip(),
                                                    email.strip(), source=source)
            except Exception as e:
                st.error(f"Couldn't save the request: {e}")
                return
        st.success(f"Requested “{sel}”. Sarthi will email it to {email.strip()}. "
                   f"Request id: {rid}")

    reqs = storage.get_report_requests(uk, limit=15)
    if reqs:
        st.divider()
        st.markdown("##### My recent requests")
        for r in reqs:
            st.markdown(f"- **{r.get('report_name','')}** · {r.get('created_at','')} "
                        f"· {r.get('status','requested')}")
