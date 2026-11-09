#!/usr/bin/env python3
"""
Seed the Lakekeeper catalog with demo Iceberg tables.

Uses DuckDB 1.4+ (with native Iceberg write support) to connect directly
to the running Lakekeeper REST catalog and create two tables:
  - demo.demo.sales     (10 rows)
  - demo.demo.customers (5 rows)

Run after `docker compose up -d`:
  pixi run seed
  # or:
  python demo-data/seed.py
"""

import sys
import duckdb

CATALOG_URL  = "http://localhost:8181"
S3_ENDPOINT  = "localhost:9000"
WAREHOUSE    = "demo"

def main():
    print("=" * 55)
    print("  Seeding Iceberg demo data")
    print(f"  Catalog : {CATALOG_URL}")
    print(f"  MinIO   : {S3_ENDPOINT}")
    print("=" * 55)

    con = duckdb.connect(":memory:")

    print("\n📦 Loading DuckDB extensions...")
    con.execute("INSTALL iceberg; LOAD iceberg;")
    con.execute("INSTALL httpfs;  LOAD httpfs;")

    print("🔑 Configuring S3 credentials...")
    con.execute(f"""
        CREATE OR REPLACE SECRET minio_s3 (
            TYPE       S3,
            KEY_ID     'admin',
            SECRET     'password',
            ENDPOINT   '{S3_ENDPOINT}',
            USE_SSL    false,
            URL_STYLE  'path',
            REGION     'us-east-1'
        );
    """)

    print("🔑 Configuring Iceberg REST secret...")
    con.execute("""
        CREATE OR REPLACE SECRET iceberg_rest (
            TYPE  ICEBERG,
            TOKEN 'dummy'
        );
    """)

    print(f"🔗 Attaching catalog '{WAREHOUSE}' from {CATALOG_URL}...")
    con.execute(f"""
        ATTACH '{WAREHOUSE}' AS demo (
            TYPE     ICEBERG,
            ENDPOINT '{CATALOG_URL}/catalog/',
            SECRET   iceberg_rest,
            ACCESS_DELEGATION_MODE 'none'
        );
    """)

    print("📁 Creating namespace demo.demo...")
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS demo.demo;")
    except Exception as e:
        print(f"   (namespace may already exist: {e})")

    # ── sales ──────────────────────────────────────────────────────────────────
    print("\n📋 Creating demo.demo.sales...")
    con.execute("""
        CREATE TABLE IF NOT EXISTS demo.demo.sales (
            sale_id   INTEGER,
            product   VARCHAR,
            amount    INTEGER,
            sale_date DATE,
            region    VARCHAR
        );
    """)
    rows = con.execute("SELECT COUNT(*) FROM demo.demo.sales").fetchone()[0]
    if rows == 0:
        con.execute("""
            INSERT INTO demo.demo.sales VALUES
                (1,  'Laptop',   1200, DATE '2025-01-01', 'North'),
                (2,  'Mouse',      25, DATE '2025-01-02', 'South'),
                (3,  'Keyboard',   75, DATE '2025-01-03', 'East'),
                (4,  'Monitor',   350, DATE '2025-01-04', 'West'),
                (5,  'Headset',   120, DATE '2025-01-05', 'Central'),
                (6,  'Laptop',   1350, DATE '2025-01-06', 'North'),
                (7,  'Mouse',      28, DATE '2025-01-07', 'South'),
                (8,  'Keyboard',   80, DATE '2025-01-08', 'East'),
                (9,  'Monitor',   370, DATE '2025-01-09', 'West'),
                (10, 'Headset',   125, DATE '2025-01-10', 'Central');
        """)
        print("   ✅ Inserted 10 rows")
    else:
        print(f"   ℹ️  Already has {rows} rows — skipping insert")

    # ── customers ──────────────────────────────────────────────────────────────
    print("\n📋 Creating demo.demo.customers...")
    con.execute("""
        CREATE TABLE IF NOT EXISTS demo.demo.customers (
            customer_id   INTEGER,
            customer_name VARCHAR,
            country       VARCHAR,
            tier          VARCHAR
        );
    """)
    rows = con.execute("SELECT COUNT(*) FROM demo.demo.customers").fetchone()[0]
    if rows == 0:
        con.execute("""
            INSERT INTO demo.demo.customers VALUES
                (1, 'Acme Corp',    'USA',     'Gold'),
                (2, 'Globex',       'UK',      'Silver'),
                (3, 'Initech',      'Germany', 'Gold'),
                (4, 'Umbrella Inc', 'France',  'Bronze'),
                (5, 'Hooli',        'USA',     'Silver');
        """)
        print("   ✅ Inserted 5 rows")
    else:
        print(f"   ℹ️  Already has {rows} rows — skipping insert")

    # ── employees_sensitive (shared sovereignty-demo dataset) ───────────────────
    from pathlib import Path
    csv_path = Path(__file__).resolve().parents[2] / "shared" / "sample_data" / "employees_sensitive.csv"
    print(f"\n📋 Creating demo.demo.employees_sensitive from {csv_path.name}...")
    con.execute("""
        CREATE TABLE IF NOT EXISTS demo.demo.employees_sensitive (
            employee_id INTEGER, full_name VARCHAR, national_id VARCHAR,
            iban VARCHAR, salary INTEGER, bonus INTEGER
        );
    """)
    rows = con.execute("SELECT COUNT(*) FROM demo.demo.employees_sensitive").fetchone()[0]
    if rows == 0:
        con.execute(f"INSERT INTO demo.demo.employees_sensitive SELECT * FROM read_csv_auto('{csv_path}');")
        print("   ✅ Inserted sensitive employee rows")

    # ── verify ─────────────────────────────────────────────────────────────────
    print("\n🔍 Verification:")
    print(con.execute("SELECT * FROM demo.demo.sales ORDER BY sale_id").fetchdf().to_string(index=False))
    print()
    print(con.execute("SELECT * FROM demo.demo.customers ORDER BY customer_id").fetchdf().to_string(index=False))

    print("\n✅ Demo data ready!")
    print("   Tables: demo.demo.sales, demo.demo.customers")


if __name__ == "__main__":
    main()
