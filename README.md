# Plan My Day

A daily-execution coach for a sales team — plan against goals, track follow-ups with an
act-to-stop buzzer, log meetings, sync MIS numbers, send WhatsApp updates, and close the
day with an auto-saved DSR.

---

## Deploying to Streamlit Cloud

1. **Push this repo to GitHub** (`.gitignore` already excludes secrets and local data).
2. On https://share.streamlit.io, create an app pointing at this repo, branch, and `app.py`.
3. Open **Settings -> Secrets** and paste your secrets in TOML format — use
   `.streamlit/secrets.toml.example` as the template (SHEET_ID, MIS_SHARE_URL, AI keys, and
   the `[gcp_service_account]` block).
4. Deploy. Every push to the tracked branch auto-redeploys.

### Data on Cloud
Streamlit Cloud's filesystem is **ephemeral** (resets on redeploy), so on Cloud the app
reads/writes **Google Sheets directly** (durable), with a short read-cache to stay under the
API quota. Run **locally** and it uses fast local files, backing them up to Google Sheets
every ~30 min. The app picks the right mode automatically.

### Google Sheet setup (the data store)
1. Create one Google Sheet that **you** own.
2. Share it (Editor) with the service account's `client_email`.
3. Put its ID in `SHEET_ID`. The app only adds tabs to this sheet — it never creates new
   Drive files (so it works on a free/consumer Google account).

---

## Users, logins, and roles

All users live in one table: `_common/users_master.xlsx` (mirrored to a `users_master` tab
in your Google Sheet). Each row is one login:

| column       | meaning                                                          |
|--------------|------------------------------------------------------------------|
| `user_key`   | the **login ID / username** (lowercase, no spaces)               |
| `name`       | display name                                                     |
| `role`       | role key, e.g. `partner_acquisition` — drives AI guidance        |
| `department` | free text                                                        |
| `login_role` | `lead` or `member` (lead unlocks lead-only views)                |
| `password`   | the user's password                                              |
| `active`     | `Yes` / `No` (set `No` to disable a login)                       |
| `created_at` | timestamp                                                        |

### How the pieces map
- **Login** = `user_key` + `password`. The typed password is checked against this row.
- **Role mapping** = the `role` column. It selects which **role guidance** the AI uses
  (see `role_prompts/`). The role key should match a `role_prompts/<role>.md` file for
  tailored coaching; otherwise generic guidance is used.
- **Lead vs member** = `login_role`. `lead` sees lead-only areas; `member` sees their own
  workspace only.

### Adding or changing users
Edit the `users_master` tab in your Google Sheet (or local `users_master.xlsx`): add a row
per person with `user_key`, `password`, `role`, `login_role`. On Cloud, edit the Sheet
directly; it takes effect on next login. Keep `user_key` lowercase.

### Role guidance (per role)
Role prompts live in `role_prompts/` **in this repo** (version-controlled). Edit a
`<role>.md` (role base) or `tweak_<user>.md` (per-person), commit, push — the deployed app
reads the new guidance after redeploy. See `role_prompts/README.md`.

---

## Running locally

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then fill it in
streamlit run app.py
```

Local data is stored under `Sarthi - Plan My Day/` (Windows: `D:\Sarthi - Plan My Day`).
WhatsApp auto-send works locally (drives your logged-in WhatsApp Web in Chrome); it is
disabled on Cloud.
