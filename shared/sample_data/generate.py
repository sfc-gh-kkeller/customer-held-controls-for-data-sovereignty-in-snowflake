"""Deterministic synthetic HR dataset shared by all sovereignty-option demos.

Non-sensitive columns: employee_id, department, country, hire_year, level
Sensitive columns:     full_name, national_id, iban, salary, bonus
"""
import csv
import random
from pathlib import Path

FIRST = ["Anna", "Lukas", "Mia", "Jonas", "Lea", "Finn", "Emma", "Paul", "Sofia", "Noah",
         "Marie", "Elias", "Clara", "Ben", "Lina", "Felix", "Ida", "Leon", "Nora", "Max"]
LAST = ["Mueller", "Schmidt", "Schneider", "Fischer", "Weber", "Meyer", "Wagner", "Becker",
        "Hoffmann", "Koch", "Dubois", "Rossi", "Garcia", "Jansen", "Novak"]
DEPTS = ["Finance", "Engineering", "Sales", "HR", "Legal", "Operations"]
COUNTRIES = ["DE", "FR", "NL", "CH", "AT"]

OUT = Path(__file__).parent


def generate(n: int = 200, seed: int = 42):
    rnd = random.Random(seed)
    rows = []
    for i in range(1, n + 1):
        country = rnd.choice(COUNTRIES)
        level = rnd.randint(1, 6)
        salary = 45_000 + level * 14_000 + rnd.randint(-6_000, 9_000)
        rows.append({
            "employee_id": i,
            "department": rnd.choice(DEPTS),
            "country": country,
            "hire_year": rnd.randint(2008, 2026),
            "level": level,
            "full_name": f"{rnd.choice(FIRST)} {rnd.choice(LAST)}",
            "national_id": f"{country}{rnd.randint(10**9, 10**10 - 1)}",
            "iban": f"{country}{rnd.randint(10, 99)}{rnd.randint(10**15, 10**16 - 1)}",
            "salary": salary,
            "bonus": int(salary * rnd.choice([0, 0.05, 0.1, 0.15])),
        })
    return rows


PUBLIC_COLS = ["employee_id", "department", "country", "hire_year", "level"]
SENSITIVE_COLS = ["employee_id", "full_name", "national_id", "iban", "salary", "bonus"]


def write_csvs(rows):
    for name, cols in [("employees_full", PUBLIC_COLS + SENSITIVE_COLS[1:]),
                       ("employees_public", PUBLIC_COLS),
                       ("employees_sensitive", SENSITIVE_COLS)]:
        with open(OUT / f"{name}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)


if __name__ == "__main__":
    write_csvs(generate())
    print("wrote employees_full/public/sensitive.csv to", OUT)
