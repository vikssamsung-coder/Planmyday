# Role prompts (the AI's guidance per role)

These files shape how the app's AI coaches each person — companion cues, task generation,
goal-alignment (the Gate), meeting write-ups, message tone, and the learning loop.

## How it's layered

For a given person, the app stacks up to three layers:

1. **Role base** — `<role>.md` — the shared description for that role (everyone in the role
   inherits it). This is the bulk of the guidance.
2. **Per-person tweak** — `tweak_<user_key>.md` — a few lines specific to one teammate
   (their focus, style). Refines the base where they overlap.
3. **Localised learning** — `<role>.learn.md` — built automatically over time from that
   user's history (you don't edit this by hand).

## How to update (GitHub is the source of truth)

1. Edit the relevant `.md` file here in the repo.
2. Commit and push to the tracked branch.
3. Streamlit Cloud auto-redeploys on push; the new guidance is live in a minute or two.
   (Locally, changes take effect immediately.)

## What to put in a role base

Cover these — plain prose is fine:

- **Purpose** — what this person is here to achieve (1–2 lines).
- **KPIs / targets** — the numbers they own (this is what the Gate checks tasks against).
- **Daily work** — what the work actually looks like day to day.
- **What good looks like** — the judgment, sequencing, and behaviours of a strong performer.
- **Pitfalls to avoid** — what goes wrong.
- **Communication tone** — how they should sound to partners / clients / team.
- **Domain notes** — products, partner types, objection patterns, broking-specific language.

## Filenames

- Role base: lowercase role key, e.g. `partner_acquisition.md`, `sales_rm.md`, `trainer.md`.
- Person tweak: `tweak_<user_key>.md`, e.g. `tweak_arjun.md`, `tweak_nishi.md`.

(The `<role>` and `<user_key>` values must match what's in the app's user records.)
