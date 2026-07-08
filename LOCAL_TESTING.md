# Local testing / desktop setup (with secrets.toml)

Run Plan My Day - Desktop on a machine, including the new **Reports Engine** and
the in-app **Update** button.

## 1. Install

```bash
cd plan-my-day
python3 -m venv .venv && source .venv/bin/activate      # optional but recommended
pip install -r requirements.txt
```

(Windows: just double-click **Start Plan My Day.bat** — it installs requirements
and launches the app.)

## 2. Configure secrets

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

One file carries every secret. Edit `.streamlit/secrets.toml`:

- **AI keys** — `OPENAI_API_KEY` (GPT tasks **and** Whisper voice transcription —
  `gpt-4o-mini-transcribe`, falling back to `whisper-1`) and/or `ANTHROPIC_API_KEY`
  (Claude coaching). Without a key the app uses rule-based fallbacks and voice
  transcription is off. These are the high-value secrets — ship them with the
  installer, never commit them, rotate if a machine leaks.
- **Database** — leave `NEON_DATABASE_URL` commented to run fully local on Excel
  (simplest first test), or set the Neon **pooled** string to use the shared
  cloud DB (thin: login check + daily brief only).
- **GitHub** (for Update + Reports Engine) — `github_owner` / `github_repo` /
  `github_branch`. On a git checkout these auto-detect from the origin remote, so
  you can omit them; set them on zip-install machines. Add `github_pat` **only**
  for a private repo — a fine-grained, read-only token scoped to `contents:read`
  on this repo. Public repo → no token needed. Never hardcode a token in
  committed code (GitHub secret-scanning auto-revokes it).

`.streamlit/secrets.toml` is gitignored — the real file is never committed.

## 3. Add an ADMIN login to test the CMS

The example file already includes plaintext `admin` / `vikrant` test logins at the
bottom (fine for local testing). Against the real Neon DB, run
`python create_admin.py` once instead to create a hashed admin account.

## 4. Run

```bash
streamlit run app.py
```

## 5. What to test

- **As `admin`** → the **Admin** tab (CMS: banners, video, contests; targeting;
  schedule/expire; MIS push).
- **As `vikrant`** (or any normal user) → the **Updates** tab, live banner on
  **Today**, targets, full-screen task view, AI usage & spend in **Settings**.
- **Reports Engine** (new) → the **Reports** tab: pick an engine from the GitHub
  registry, upload a prompt and/or data dump(s), **Run**, and download the report.
  Everything runs locally; reports save under your `reports/` folder. If the engine
  list won't load, you're offline and have no cached registry yet — update once
  (below) while online.
- **App updates** (new) → **Settings → App updates → Check for updates & update
  now**. On a git checkout it runs `git pull`; on a zip install it pulls the repo
  tarball and overwrites code in place. Your data (under `D:\Sarthi - Plan My Day`)
  is never touched. **Restart the app afterwards** to load the new code.

## Adding a Reports Engine

Copy `engines/reference_rollup.py`, replace the body of `run(ctx)` with your
pandas merge + analysis (emit xlsx/docx/pdf via openpyxl / python-docx / reportlab),
commit it, add a line to `engines/registry.json`, and **pin its commit SHA** in that
line. The SHA pin is what stops a stray push from changing what runs on every machine.

> Note: a web app can't fire the buzzer when the browser is fully closed — a delivery
> limit, not a data one. While the app is open it fires on any page.
