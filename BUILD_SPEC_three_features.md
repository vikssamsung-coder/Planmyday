# Build Spec — Three Master-Code Features

This spec adds three lead-facing features to **Plan My Day**:

1. **Role-Tweak Writer** — turn daily-log observations about a person into a properly
   formatted `tweak_<user>.md` the app can consume (in-app tool **and** documented prompt).
2. **User Creator** — create a user and emit both the **secrets block** (ready to paste
   into Streamlit Cloud Secrets) **and** the `users_master` sheet row.
3. **DSR Reader** — aggregate saved DSRs into a team-performance summary, shown on screen
   **and** downloadable as a Word report.

All three live behind the existing lead gate
(`is_lead = user.get("login_role") == "lead" or user.get("role") == "lead"`), in a new
top-nav tab **“Lead”** (members never see it).

---

## 0. Shared groundwork

### 0.1 New nav tab (lead only)
In `header_nav(is_lead)`, when `is_lead`, append a **“Lead”** entry. In `main()`'s router,
map `"Lead": lead_view`. `lead_view(user)` renders three sub-sections via
`st.radio("section", ["Role tweaks", "Create user", "Team DSR review"], horizontal=True)`
— radios (not `st.tabs`) so any future mic components mount correctly.

```python
def lead_view(user):
    if not (user.get("login_role") == "lead" or user.get("role") == "lead"):
        st.error("This area is for the team lead."); return
    sec = st.radio("section", ["Role tweaks", "Create user", "Team DSR review"],
                   horizontal=True, key="lead_section")
    if sec == "Role tweaks":      _lead_role_tweak(user)
    elif sec == "Create user":    _lead_create_user(user)
    else:                          _lead_dsr_review(user)
```

### 0.2 AI plumbing already available
- `ai._chat_json(system, user_obj, max_tokens)` → returns parsed JSON dict; has rule-based
  fallbacks when no key is set. Use for any structured generation.
- `ai.master_system()` is prepended to every call (the companion constitution). New system
  prompts below are **appended** to it inside the helper functions (mirror existing usage).
- Honor the house tone: concise, honest, no flattery.

---

## 1. Feature 1 — Role-Tweak Writer

### 1.1 Purpose
The lead pastes (or dictates) free-form observations about a team member — pulled from
daily logs, MoMs, or memory — and the app returns a **clean role tweak** in the exact
format `read_role_tweak()` expects, ready to commit as `role_prompts/tweak_<user>.md`.

### 1.2 The tweak format (what the model must output)
A tweak is short Markdown that *refines* the role base for one person. Keep it tight
(the base carries the bulk). Target shape:

```markdown
# Personal tweak — <Name> (<role>)

## Focus
- <1–3 bullets: what this person should prioritise right now>

## Strengths to lean on
- <what they already do well; the AI should reinforce these>

## Watch-outs
- <recurring pitfalls to gently steer away from>

## Coaching style
- <how cues to this person should sound: blunt vs. encouraging, detail level, etc.>
```

Rules the generator must follow (put in the system prompt):
- Output **only** the tweak Markdown — no preamble, no code fences.
- Keep it under ~150 words. The base is the source of depth; the tweak is a patch.
- Be concrete and behavioural; no vague praise. Tie each point to something observable.
- Never include sensitive personal data (health, etc.) — coaching guidance only.
- If merging with an existing tweak, **preserve still-valid points**, update what changed,
  drop what's contradicted.

### 1.3 New AI function — `ai.write_role_tweak(...)`
Add to `ai.py`:

```python
ROLE_TWEAK_SYSTEM = """You write a concise PERSONAL TWEAK that refines an existing role
guidance for ONE team member, based on the lead's observations. Output ONLY the tweak in
this exact Markdown shape (no preamble, no code fences):

# Personal tweak — <Name> (<role>)

## Focus
- ...
## Strengths to lean on
- ...
## Watch-outs
- ...
## Coaching style
- ...

Under ~150 words. Concrete and behavioural — every point tied to something observable. No
vague praise, no sensitive personal data (coaching guidance only). If a CURRENT TWEAK is
given, keep still-valid points, update what changed, drop what's contradicted."""

def write_role_tweak(name, role, role_base, observations, current_tweak=""):
    """Return tweak Markdown. observations = the lead's notes/daily-log excerpts."""
    payload = {
        "name": name, "role": role,
        "role_base": role_base[:4000],
        "current_tweak": current_tweak[:2000],
        "observations": observations[:6000],
        "instructions": "Write/update the personal tweak from these observations.",
    }
    # text-out (not JSON): reuse the raw-completion path like broadcast_message does, OR
    # ask _chat_json for {"tweak": "..."} and read .get("tweak"). Prefer JSON for safety:
    data = _chat_json(ROLE_TWEAK_SYSTEM + "\n\nReturn STRICT JSON: {\"tweak\": \"...\"}",
                      payload, max_tokens=500)
    return (data.get("tweak") or "").strip()
```
(Fallback when no key: assemble a minimal tweak from the observations — headings + the
first few observation lines as bullets — so the feature still works, just dumber.)

### 1.4 In-app tool — `_lead_role_tweak(user)`
UI flow:
1. **Pick the team member** — `selectbox` over `storage.get_users()` (exclude the lead if
   desired). Resolve `puk` (user_key), `prole` (role), `pname` (name).
2. **Pull source material** (buttons/checkboxes the lead can combine):
   - “Use recent daily logs” → `storage.get_daily_logs(puk)` (their logs) and/or the
     lead's own logs that mention them. Concatenate the `transcript` fields (cap length).
   - “Use last meetings” → `storage.get_meetings(puk)` recent `ai_written`/`outcome`.
   - **Free-text box + mic** (`streamlit_mic_recorder`, transcribe via `ai.transcribe`) for
     the lead to add observations directly.
3. Show the **current tweak** if any: `storage.read_role_tweak(puk)`.
4. **Generate** button → `ai.write_role_tweak(pname, prole, storage.read_role_base(prole),
   observations, current_tweak)`. Show the result in a `st.code(tweak, language="markdown")`
   so it's easy to copy.
5. **Two save paths**, clearly labelled (mirror the GitHub-source-of-truth model):
   - **“Copy for GitHub”**: display the filename `role_prompts/tweak_<puk>.md` and the
     content in `st.code(...)`. The lead commits it to the repo (the real source of truth).
   - **“Save locally now (until next deploy)”**: optional convenience —
     `storage.write_role_tweak(puk, tweak)` writes the local repo copy for immediate local
     testing. Caption that on Cloud this lasts only until the next redeploy, so committing
     to GitHub is the durable path.

> Note: This is consistent with the earlier decision that **role guidance is GitHub-managed
> and not user-visible**. This tool is **lead-only** and produces the file content; the
> commit still happens via GitHub.

### 1.5 Documented prompt (the “run it yourself” path)
Add a short doc block (in this file, §1.6) the lead can paste into any chat model with the
same system prompt + the observations, to get the same tweak format without the app.

### 1.6 Standalone prompt template
```
SYSTEM: <paste ROLE_TWEAK_SYSTEM from §1.3>

USER:
Name: <name>
Role: <role>
Role base:
<paste the contents of role_prompts/<role>.md>
Current tweak (if any):
<paste role_prompts/tweak_<user>.md, or "none">
Observations:
<paste daily-log excerpts / notes about this person>
```
The model returns the tweak Markdown → save as `role_prompts/tweak_<user>.md`, commit, push.

---

## 2. Feature 2 — User Creator (secrets block + sheet row)

### 2.1 Purpose
The lead fills a small form (username, name, role, login_role, password, department) and
gets **two ready-to-use outputs**:
- A **secrets TOML block** to paste into Streamlit Cloud → Settings → Secrets.
- The **`users_master` sheet row** values (to add to the sheet), plus an optional one-click
  “add to users now”.

### 2.2 Decision: users in secrets *and* sheet
Per the choice to “generate BOTH”, support an optional **secrets-defined users** source in
addition to the sheet. Implement a `[[users]]` array in secrets:

```toml
[[users]]
user_key = "arjun"
name = "Arjun"
role = "partner_acquisition"
login_role = "member"
password = "<set-a-strong-one>"
department = "Partner Acquisition"
active = "Yes"
```

`storage.get_users()` must MERGE both sources (sheet rows + secrets `[[users]]`), with
**secrets taking precedence** on `user_key` collision (so secrets can override/seed logins
on Cloud where the sheet may be fresh). Update `authenticate()` only via `get_users()`, so
it transparently honors both.

```python
def _secrets_users():
    try:
        import streamlit as st
        rows = st.secrets.get("users", [])
        return [dict(r) for r in rows]
    except Exception:
        return []

def get_users():
    sheet = _read(_users_path(), schemas.USERS)          # existing
    merged = {r["user_key"]: r for _, r in sheet.iterrows()} if not sheet.empty else {}
    for u in _secrets_users():                            # secrets override
        uk = str(u.get("user_key","")).strip().lower()
        if uk:
            row = {c: "" for c in schemas.USERS}
            row.update({k: u.get(k, row.get(k, "")) for k in schemas.USERS})
            row["user_key"] = uk
            merged[uk] = row
    import pandas as pd
    return pd.DataFrame(list(merged.values()), columns=schemas.USERS) if merged \
           else pd.DataFrame(columns=schemas.USERS)
```

> Security: passwords are plain text in both the sheet and secrets. Acceptable for a small
> internal team. (Optional hardening — §2.6.)

### 2.3 In-app tool — `_lead_create_user(user)`
Form fields: `user_key` (lowercased, validated: no spaces, unique-warn against
`get_users()`), `name`, `role` (free text or a select of known roles), `login_role`
(`member`/`lead`), `password` (with a “generate strong” helper), `department`, `active`
(default `Yes`).

On **“Build outputs”** (no secrets are written by the app — it only *emits* them):

1. **Secrets block** — render in `st.code(toml_text, language="toml")`:
   ```toml
   [[users]]
   user_key = "arjun"
   name = "Arjun"
   role = "partner_acquisition"
   login_role = "member"
   password = "..."
   department = "Partner Acquisition"
   active = "Yes"
   ```
   Caption: “Paste this into Streamlit Cloud → Settings → Secrets (append to existing).”

2. **Sheet row** — show the values as a one-line table and as comma-separated values in
   column order (`schemas.USERS`), so the lead can paste into the `users_master` tab.

3. **Optional “Add to users now”** button → builds the row dict and appends via a new
   `storage.add_user(row)` (writes to `users_master.xlsx`; syncs to Sheets like other data).
   Locally this persists; on Cloud it writes to Sheets (durable).

### 2.4 New storage helper — `storage.add_user(...)`
```python
def add_user(user_key, name, role, login_role="member", password="",
             department="", active="Yes"):
    df = _read(_users_path(), schemas.USERS)
    uk = str(user_key).strip().lower()
    if not df.empty and (df["user_key"].astype(str).str.lower() == uk).any():
        return False, "That username already exists."
    row = {c: "" for c in schemas.USERS}
    row.update({"user_key": uk, "name": name, "role": role, "department": department,
                "login_role": login_role, "password": password, "active": active,
                "created_at": _now()})
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _write(_users_path(), df, schemas.USERS)
    return True, "added"
```

### 2.5 TOML emitter (helper in app.py)
```python
def _user_secrets_block(d):
    def q(v): return '"' + str(v).replace('"', '\\"') + '"'
    keys = ["user_key","name","role","login_role","password","department","active"]
    return "[[users]]\n" + "\n".join(f"{k} = {q(d.get(k,''))}" for k in keys)
```

### 2.6 Optional hardening (future)
Hash passwords (e.g. `hashlib.sha256` with a per-deploy salt) and store the hash; the
emitter would output the hash, and `authenticate()` would compare hashes. Not in scope now;
note it so it isn't forgotten.

---

## 3. Feature 3 — DSR Reader (team performance summary)

### 3.1 Purpose
The lead picks team members and a date range; the app gathers each member's **saved DSR
text** (`storage.get_dsr`) plus their numbers, and produces:
- an **on-screen AI summary** of team and per-member performance, and
- a **downloadable Word report**.

### 3.2 Reading DSRs across users
DSRs are stored per user via `save_dsr` / `get_dsr` (store `dsr_log.xlsx`). Add a helper to
pull a date range:

```python
def get_dsrs_range(user_key, start_date, end_date):
    df = _read(_dsr_log_path(user_key), schemas.DSR_LOG)
    if df.empty: return []
    m = (df["date"] >= start_date) & (df["date"] <= end_date)
    return [(r["date"], r["report_text"]) for _, r in df[m].sort_values("date").iterrows()]
```

On Cloud, `get_dsr*` reads from Sheets (durable); locally from files. To review **other**
users' DSRs, the lead's app must read each member's store — this works because storage
paths are by `user_key` and (on Cloud) all users' tabs are in the same Sheet. (Locally, the
lead only has their own machine's data unless restored — note this limitation; on Cloud it's
complete.)

### 3.3 New AI function — `ai.team_performance(...)`
```python
TEAM_PERF_SYSTEM = """You are a sales-leadership analyst. From each team member's Daily
Status Reports (their own words + numbers), produce a clear performance read for the lead.
Be specific and honest — call out who is on track, who is slipping, and why, citing what's
in the DSRs. No flattery. Structure:
- Team headline (2–3 sentences: overall momentum, biggest risk, biggest win).
- Per member: status (On track / At risk / Behind), what's working, what's not, one
  concrete suggestion.
- Watch list: the 1–3 people/issues needing the lead's attention this week.
Return STRICT JSON:
{"headline": "...", "members": [{"name": "...", "status": "...", "working": "...",
"not_working": "...", "suggestion": "..."}], "watchlist": ["..."]}"""

def team_performance(period_label, per_member):
    """per_member: list of {"name":..., "role":..., "dsr_text": "<concatenated DSRs>"}."""
    payload = {"period": period_label, "members": per_member}
    return _chat_json(TEAM_PERF_SYSTEM, payload, max_tokens=1800)
```
Fallback (no key): rule-based — per member, derive status from the latest DSR's
tasks-done / KPIs-behind lines via simple parsing; headline = counts.

### 3.4 In-app tool — `_lead_dsr_review(user)`
1. **Scope controls**: `multiselect` of team members (default all from `get_users()` where
   `login_role != "lead"`), and a date range (`st.date_input` start/end; default last 7
   days). A “Working days only” note is fine.
2. **Gather**: for each selected member, `storage.get_dsrs_range(puk, start, end)`,
   concatenate the texts (cap each member to a sane length to protect tokens — e.g. last
   ~5 DSRs or ~4000 chars), build the `per_member` list.
3. **Summarise** button → `ai.team_performance(period_label, per_member)`.
4. **On-screen**: render the headline, a per-member table (Name · Status badge · working ·
   not working · suggestion), and the watch list. Color the status (green/amber/red).
5. **Download**: a “⬇️ Download team report (Word)” button building a `.docx` via a new
   `report.build_team_report(period_label, summary, per_member_meta)` (reuse the docx
   helpers/style from `dsr.py`). Include: cover (period, generated time), team headline,
   per-member sections, watch list, and an appendix listing which dates were included per
   member.

### 3.5 New report function — `report.build_team_report(...)`
Mirror `dsr.build_docx` structure (python-docx, same fonts/heading helpers). Sections:
- Title “Team Performance Review”, period, generated timestamp, divider.
- Summary table: members count, on-track / at-risk / behind tallies.
- Team headline paragraph.
- Per-member blocks (heading = name + status; bullets for working / not working /
  suggestion).
- Watch list.
- Appendix: per member, the dates of DSRs included.
Return `bytes` for `st.download_button`.

### 3.6 Privacy / scope
- Lead-only (gate as in §0.1). Members cannot open this view.
- The summary uses DSR **text** the team already generates; no new personal data.
- Cap token usage by limiting DSRs per member and total members per run; warn if the
  selection is very large.

---

## 4. Files touched (summary)

- `app.py` — add `lead_view` + `_lead_role_tweak`, `_lead_create_user`, `_lead_dsr_review`;
  add “Lead” to `header_nav` + router; add `_user_secrets_block` helper.
- `ai.py` — add `write_role_tweak`, `team_performance` (+ their SYSTEM prompts + fallbacks).
- `storage.py` — add `add_user`, `get_dsrs_range`, `get_dsrs_range`-style readers; update
  `get_users()` to merge secrets `[[users]]`; (DSR + role-tweak read/write already exist).
- `report.py` — add `build_team_report`.
- `schemas.py` — no new stores required (USERS, DSR_LOG already exist).
- `secrets.toml.example` / README — document the optional `[[users]]` block and the new
  Lead tab.

## 5. Test checklist
- Role tweak: observations in → valid `tweak_<user>.md` shape out; local save writes the
  repo copy; empty-key fallback still returns a usable tweak.
- User creator: form → correct `[[users]]` TOML + correct CSV row; `add_user` rejects dupes;
  `get_users()` merges secrets over sheet; `authenticate()` honors a secrets-only user.
- DSR review: multi-member + date range gathers the right DSRs; summary renders; Word
  report downloads; lead-gate blocks members; large-selection token guard fires.
- Regression: existing login, Today, Close My Day, MIS sync, WhatsApp unaffected.
