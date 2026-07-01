# Local testing (with secrets.toml)

A quick path to run Plan My Day on your machine and test the new CMS + admin.

## 1. Install

```bash
cd plan-my-day
python3 -m venv .venv && source .venv/bin/activate      # optional but recommended
pip install -r requirements.txt
```

## 2. Configure secrets

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Then edit `.streamlit/secrets.toml`:

- **Database** — two choices:
  - **Local Excel (simplest first test):** *comment out* `NEON_DATABASE_URL`. The app runs on local Excel files with seeded demo users. Nothing external needed.
  - **Shared Neon DB:** set `NEON_DATABASE_URL` to your Singapore connection string. The app reads/writes the real cloud data, and the new `content` / `ai_usage` tables auto-create on first launch.
- **AI keys** — add `OPENAI_API_KEY` and/or `ANTHROPIC_API_KEY` (optional; without them the app uses rule-based fallbacks).

## 3. Add an ADMIN login to test the CMS

Easiest for local testing — add these blocks at the **bottom** of `secrets.toml`
(passwords here can be plaintext locally):

```toml
[[users]]
user_key = "admin"
name     = "Admin"
role     = "ADMIN"          # role ADMIN unlocks the Admin / CMS module
password = "test123"
active   = "Yes"

[[users]]
user_key = "vikrant"
name     = "Vikrant"
role     = "partner_acquisition"
password = "test123"
active   = "Yes"
```

(Against the real Neon DB you can instead run `python create_admin.py` once to
create a hashed admin account.)

## 4. Run

```bash
streamlit run app.py
```

## 5. What to test

- **As `admin`** → you land on the **Admin** tab only. Publish a banner, a YouTube
  video, a contest; try targeting Everyone vs one user; try schedule/expire; use
  **Manage** to unpublish/delete; try **MIS push** with a sheet.
- **As `vikrant`** (or any normal user) → you see the **Updates** tab with published
  content, and any live **banner** shows at the top of **Today**.
- New: the **Close your Day / Share Progress Brief** slate bar under the targets,
  the **Full screen** task view with target quadrants, the compact time picker, and
  **AI usage & spend** (per user, day-wise) in **Settings**.

> Note: a web app can't fire the buzzer when the browser is fully closed — that's a
> delivery limit, not a data one. While the app is open it fires on any page.
