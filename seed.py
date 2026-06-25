"""Seed the workspace so the app runs out of the box.

Creates _common/users_master.xlsx and, for each demo user, a monthly_targets +
monthly_plan for the current month. Safe to run repeatedly — it won't overwrite
existing target/plan/task data, only fills what's missing.
"""

from datetime import datetime
import pandas as pd

import storage
import schemas

MONTH = datetime.now().strftime("%Y-%m")
NOW = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

USERS = [
    {"user_key": "nishi", "name": "Nishi", "role": "sales_rm", "department": "Client Engagement",
     "login_role": "member", "password": "nishi", "active": "Yes", "created_at": NOW},
    {"user_key": "yash", "name": "Yash Chouhan", "role": "trainer", "department": "Training",
     "login_role": "member", "password": "yash", "active": "Yes", "created_at": NOW},
    {"user_key": "vikrant", "name": "Vikrant", "role": "lead", "department": "Leadership",
     "login_role": "lead", "password": "vikrant", "active": "Yes", "created_at": NOW},
    {"user_key": "arjun", "name": "Arjun", "role": "partner_acquisition", "department": "Partnerships",
     "login_role": "member", "password": "arjun", "active": "Yes", "created_at": NOW},
]

TARGETS = {
    "nishi": [
        {"kpi_name": "Revenue", "monthly_target": "2500000", "achieved_mtd": "1480000", "target_unit": "INR", "priority": "P1"},
        {"kpi_name": "Trade Activation", "monthly_target": "200", "achieved_mtd": "78", "target_unit": "Count", "priority": "P1"},
        {"kpi_name": "New Accounts", "monthly_target": "150", "achieved_mtd": "96", "target_unit": "Count", "priority": "P2"},
    ],
    "yash": [
        {"kpi_name": "Trainings Delivered", "monthly_target": "40", "achieved_mtd": "22", "target_unit": "Count", "priority": "P1"},
        {"kpi_name": "Impact Validation", "monthly_target": "40", "achieved_mtd": "9", "target_unit": "Count", "priority": "P1"},
    ],
    "arjun": [
        {"kpi_name": "Partners Acquired", "monthly_target": "30", "achieved_mtd": "11", "target_unit": "Count", "priority": "P1"},
        {"kpi_name": "Accounts Opened", "monthly_target": "300", "achieved_mtd": "120", "target_unit": "Count", "priority": "P1"},
        {"kpi_name": "Accounts Activated", "monthly_target": "200", "achieved_mtd": "54", "target_unit": "Count", "priority": "P1"},
        {"kpi_name": "Cross-sell Revenue", "monthly_target": "500000", "achieved_mtd": "180000", "target_unit": "INR", "priority": "P2"},
    ],
}

PLANS = {
    "nishi": [
        {"activity": "Funded-not-traded push", "impact_category": "Direct",
         "linked_kpi": "Trade Activation", "daily_minimum_action": "Call 30 funded-not-traded clients",
         "success_metric": "5 first trades/day"},
        {"activity": "Top partner commitment tracking", "impact_category": "Direct",
         "linked_kpi": "Revenue", "daily_minimum_action": "Capture partner commitment by 11 AM",
         "success_metric": "Daily revenue commitment logged"},
        {"activity": "New account KYC closure", "impact_category": "Direct",
         "linked_kpi": "New Accounts", "daily_minimum_action": "Close stuck KYC cases",
         "success_metric": "All same-day KYCs cleared"},
    ],
    "yash": [
        {"activity": "Daily training delivery", "impact_category": "Direct",
         "linked_kpi": "Trainings Delivered", "daily_minimum_action": "Run 2 sessions",
         "success_metric": "2 sessions completed"},
        {"activity": "Post-training impact check", "impact_category": "Direct",
         "linked_kpi": "Impact Validation", "daily_minimum_action": "Validate outcomes for yesterday's session",
         "success_metric": "Measured improvement captured"},
    ],
}


def seed():
    # users — write THROUGH the storage backend (routes to Google Sheets on cloud,
    # Excel locally). Using df.to_excel directly would bypass cloud and never seed users.
    if storage.get_users().empty:
        df = pd.DataFrame(USERS)[schemas.USERS]
        storage._write(storage._users_path(), df, schemas.USERS)
        print(f"Seeded {len(df)} users")

    for uk, rows in TARGETS.items():
        if storage.get_targets(uk, MONTH).empty:
            user = next(u for u in USERS if u["user_key"] == uk)
            data = []
            for r in rows:
                row = {c: "" for c in schemas.MONTHLY_TARGETS}
                row.update(r)
                row.update({"month": MONTH, "user_key": uk, "role": user["role"],
                            "created_at": NOW, "updated_at": NOW})
                data.append(row)
            storage.save_targets(uk, pd.DataFrame(data)[schemas.MONTHLY_TARGETS])
            print(f"Seeded targets for {uk}")

    for uk, rows in PLANS.items():
        if storage.get_plan(uk, MONTH).empty:
            user = next(u for u in USERS if u["user_key"] == uk)
            data = []
            for i, r in enumerate(rows, 1):
                row = {c: "" for c in schemas.MONTHLY_PLAN}
                row.update(r)
                row.update({"month": MONTH, "user_key": uk, "role": user["role"],
                            "activity_id": f"act_{i:03d}", "status": "Active",
                            "created_at": NOW, "updated_at": NOW})
                data.append(row)
            storage.save_plan(uk, pd.DataFrame(data)[schemas.MONTHLY_PLAN])
            print(f"Seeded monthly plan for {uk}")


if __name__ == "__main__":
    seed()
    import paths
    print("Seed complete. Workspace at:", paths.base_dir())
