#!/usr/bin/env python3
"""Pre-install DuckDB extensions during Docker build.

Uses the DuckDB Python API so it handles the correct version-specific
download URL and signature verification automatically.
"""

import duckdb


def main():
    version = duckdb.__version__
    print(f"📥 Pre-installing DuckDB extensions (duckdb {version})...")
    conn = duckdb.connect()

    for ext in ['iceberg', 'httpfs']:
        print(f'   Installing {ext}...')
        conn.execute(f"INSTALL {ext};")
        print(f'   ✅ {ext} installed')

    conn.close()
    print('✅ All extensions installed successfully!')


if __name__ == '__main__':
    main()
