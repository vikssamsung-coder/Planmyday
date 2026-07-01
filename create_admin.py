"""One-time helper: create (or update) a SEPARATE ADMIN login for the CMS.

The admin login is a normal user row whose role is ADMIN. When you log in with it,
the app shows only the Admin module (Publish / Manage / MIS push). Your everyday
account keeps its normal role and tabs.

Run it locally, pointed at the SAME storage the app uses:

  # Against the production Neon database (recommended):
  NEON_DATABASE_URL="postgresql://…your Singapore string…" python create_admin.py

  # Or against a local Excel workspace:
  STORAGE_BASE_DIR="/path/to/workspace" python create_admin.py
"""
import getpass

import pandas as pd

import schemas
import storage


def main():
    print("Create / update an ADMIN login for Plan My Day.\n")
    uk = (input("Admin username [admin]: ").strip().lower() or "admin")
    name = input("Display name [Admin]: ").strip() or "Admin"
    pw = getpass.getpass("Password: ").strip()
    pw2 = getpass.getpass("Confirm password: ").strip()
    if not pw or pw != pw2:
        print("Passwords empty or don't match — aborting.")
        return

    df = storage._read(storage._users_path(), schemas.USERS)
    row = {
        "user_key": uk, "name": name, "role": "ADMIN", "department": "Admin",
        "login_role": "admin", "password": storage.hash_password(pw),
        "active": "Yes", "created_at": storage._now(),
    }
    if not df.empty and (df["user_key"].astype(str).str.lower() == uk).any():
        i = df[df["user_key"].astype(str).str.lower() == uk].index[0]
        for k, v in row.items():
            df.loc[i, k] = v
        action = "updated"
    else:
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        action = "created"
    storage._write(storage._users_path(), df, schemas.USERS)
    print(f"\n✅ Admin login '{uk}' {action}.")
    print("   Log in with that username + password to reach the Admin tab.")


if __name__ == "__main__":
    main()
