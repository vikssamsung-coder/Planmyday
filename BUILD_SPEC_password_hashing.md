# Build Spec — Salted Password Hashing (completion)

## Status: core already exists

`storage.py` already implements salted hashing and uses it in login:

- `hash_password(password, salt=None)` → `"pbkdf2$<iterations>$<salt_hex>$<hash_hex>"`
  (PBKDF2-SHA256, 200,000 iterations, random 16-byte salt). Self-contained: the salt
  travels inside the stored string.
- `verify_password(password, stored)` → True/False. Handles the hash format **and** a
  legacy plaintext fallback (constant-time compare via `hmac.compare_digest` for hashes).
- `authenticate(user_key, password)` already calls `verify_password`.

**So why does it still look unsalted?** The *stored* password values are still plaintext —
the seeded `DEMO_USERS` (password = username) and any `[[users]]` / sheet rows you typed
use readable passwords, which `verify_password`'s legacy fallback still accepts. To "add
salt", we must (1) generate hashes, (2) store hashes instead of plaintext everywhere, and
(3) optionally remove the plaintext fallback to enforce hashing.

This spec covers the three remaining pieces.

---

## 1. Standalone hash generator — `hash_password.py`

A tiny script the lead runs to turn a plain password into the stored hash, to paste into
secrets or the `users_master` sheet.

```python
#!/usr/bin/env python3
"""Generate a salted password hash to store in secrets / the users sheet.
Usage:
    python hash_password.py                # prompts (hidden input)
    python hash_password.py "mypassword"   # arg (less safe; shell history)
"""
import sys
from storage import hash_password   # reuse the app's exact function

def main():
    if len(sys.argv) > 1:
        pw = sys.argv[1]
    else:
        import getpass
        pw = getpass.getpass("Password: ")
        if pw != getpass.getpass("Confirm:  "):
            print("Passwords don't match."); return
    if not pw:
        print("Empty password."); return
    print("\nStore this as the user's password value:\n")
    print(hash_password(pw))

if __name__ == "__main__":
    main()
```

Place at repo root. Output example:
`pbkdf2$200000$9f3c...$a17b...` — paste that into the `password` field of a `[[users]]`
block or the sheet row. It is safe to share/store; it is not the password.

> Build note: importing `storage` pulls the app's deps. If you want a zero-dependency
> generator, inline the PBKDF2 logic from `hash_password` into the script instead of
> importing. Functionally identical.

---

## 2. User Creator emits hashes (integrates with the User-Creator feature)

In the User Creator tool (Feature 2 of the three-feature spec), when building the secrets
block and sheet row, **hash the entered password** before emitting:

```python
stored_pw = storage.hash_password(entered_password)
```

- The emitted `[[users]]` TOML and the sheet-row CSV use `stored_pw` (the `pbkdf2$...`
  string), never the plaintext.
- `storage.add_user(...)` likewise stores `hash_password(password)` (add the hash in the
  caller, or hash inside `add_user` — pick one place and be consistent; hashing in the UI
  layer keeps `add_user` a pure writer).
- Show the lead the **plaintext once** on screen ("give this password to the user") with a
  clear "this is shown once and not stored" caption, then store only the hash.

UI copy to add: "Passwords are stored salted + hashed — they can't be read back, only
reset."

---

## 3. Migrate existing logins (one-time)

Existing plaintext passwords keep working via the fallback, but should be re-hashed.

### 3.1 Seeded demo users
`DEMO_USERS` in `storage.py` has plaintext (`password == username`). Either:
- **Remove the demo users** before production (recommended), or
- Replace their `password` values with `hash_password("...")` outputs.

### 3.2 Sheet / secrets users
For each real user, run `hash_password.py`, then replace their `password` value in the
`users_master` sheet (or `[[users]]` secrets) with the hash. Do this per user; logins keep
working throughout because the fallback accepts plaintext until you swap it.

### 3.3 Optional migration helper (in-app, lead-only)
A button in the lead area: "Re-hash any plaintext passwords" that scans `get_users()`,
and for any `password` not starting with `pbkdf2$`, replaces it with `hash_password(value)`
via a storage update. Caveat: this only works for sheet-stored users (the app can rewrite
the sheet); secrets-defined users must be edited by hand in the Secrets box. Gate behind a
confirm. (Optional — manual migration via §3.2 is sufficient for a small team.)

---

## 4. Enforce hashing (after migration)

Once every stored password is a `pbkdf2$...` hash, tighten `verify_password` to drop the
plaintext fallback so a stray plaintext value can never authenticate:

```python
def verify_password(password, stored):
    import hashlib, hmac
    stored = str(stored or "")
    if not stored.startswith("pbkdf2$"):
        return False                      # hashes only — no plaintext accepted
    try:
        _, iters, salt_hex, hash_hex = stored.split("$", 3)
        dk = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False
```

Do this **only after** confirming all users are migrated, or you'll lock out anyone still
on plaintext.

---

## 5. Files touched

- **New:** `hash_password.py` (standalone generator).
- `app.py` — User Creator hashes before emitting/saving; optional lead "re-hash" button;
  add the "stored salted+hashed" UI copy.
- `storage.py` — (core already done) optionally tighten `verify_password` per §4 after
  migration; ensure `add_user` stores hashes consistently.
- `README` / `secrets.toml.example` — note that `password` values are PBKDF2 hashes
  produced by `hash_password.py`, not plaintext.

## 6. Test checklist

- `hash_password("x")` → starts with `pbkdf2$200000$`; two calls give **different** salts
  (and different hashes) for the same input.
- `verify_password("x", hash_password("x"))` is True; wrong password is False.
- `authenticate` succeeds for a user whose stored password is a hash; fails on wrong pw.
- Legacy: before §4, a plaintext-stored user still logs in; after §4, only hashed users do.
- User Creator: emitted `[[users]]` block shows a `pbkdf2$...` value, never the plaintext.
- Migration: after re-hashing, all `get_users()` rows have `pbkdf2$` passwords; logins work.

## 7. Security notes (state honestly)

- Hashing protects passwords **at rest** (secrets, sheet). Transport security (HTTPS) is
  already provided by Streamlit Cloud.
- Hashed passwords are **non-recoverable** — only resettable. The lead must be able to
  regenerate a hash (the generator / User Creator covers this).
- 200,000 PBKDF2 iterations is a reasonable 2025 baseline; raise if needed (older hashes
  still verify because the iteration count is stored inside each hash).
