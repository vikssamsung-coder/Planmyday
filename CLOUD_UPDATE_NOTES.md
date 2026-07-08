# planmyday — cloud update

This is the full cloud repo with every fix applied. Desktop-only modules
(engine_loader, reports_engine, updater, desktop_config, engines/) are intentionally
NOT here — the cloud app doesn't use them and app.py runs fine without them.

## Deploy
1. Replace the contents of your `planmyday` repo with these files, commit, push.
   Streamlit Community Cloud redeploys automatically.
2. Log in as ADMIN -> Admin -> Users -> ⚙️ Database schema (Neon) ->
   "Update database schema now"  (creates login_log, ensures all tables — idempotent).
3. Admin -> Users: add the analyst logins (username, name, role, password). These go to
   Neon, so the desktop app uses the same accounts.

## What changed
- Dictate button now uses Streamlit's native audio_input (reliable on Cloud).
- Selective Neon routing added but is a NO-OP on Cloud (everything still goes to Neon,
  because the cloud disk is ephemeral). It only thins Neon on desktop machines.
- Admin: new "Users" tab (manage logins/roles/passwords), a "Database schema" button,
  and a "Team progress (synced from machines)" panel in Analysis.
- login_log table added to the schema (login analysis now captured on Cloud too).
- requirements: streamlit>=1.35.

## Secrets (Streamlit Cloud dashboard, not a file)
Keep NEON_DATABASE_URL (pooled endpoint), OPENAI_API_KEY, ANTHROPIC_API_KEY set in the
app's Secrets in the Streamlit dashboard. The same NEON_DATABASE_URL goes on each Windows
desktop machine's local .streamlit/secrets.toml so both share one database.
