# main/management/commands/utf8mb4_convert.py
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.conf import settings

CRITICAL_TABLES = [
    "django_admin_log",
    "django_content_type",
    "auth_user",
    "auth_group",
    "auth_permission",
    "auth_group_permissions",
    "auth_user_groups",
    "auth_user_user_permissions",
    "django_session",
    "main_country",  # your model table
]

SQL_DB_DEFAULT = """
ALTER DATABASE `{db}`
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
"""

SQL_LIST_BASE_TABLES = """
SELECT TABLE_NAME
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = %s
  AND TABLE_TYPE = 'BASE TABLE'
ORDER BY TABLE_NAME;
"""

SQL_CONVERT_TABLE = """
ALTER TABLE `{table}`
  CONVERT TO CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
"""

SQL_LIST_NON_UTF8MB4_COLUMNS = """
SELECT TABLE_NAME, COLUMN_NAME, CHARACTER_SET_NAME, COLLATION_NAME
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = %s
  AND CHARACTER_SET_NAME IS NOT NULL
  AND CHARACTER_SET_NAME <> 'utf8mb4'
ORDER BY TABLE_NAME, ORDINAL_POSITION;
"""

SQL_SHOW_FULL_COLUMNS = "SHOW FULL COLUMNS FROM `{table}`;"

class Command(BaseCommand):
    help = "Convert database and tables to utf8mb4 so emoji (e.g., flags) work everywhere."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Convert ALL base tables (safe default is to convert critical tables + everything else best-effort).",
        )
        parser.add_argument(
            "--db",
            default=None,
            help="Override DB name if different from settings.DATABASES['default']['NAME']",
        )

    def handle(self, *args, **opts):
        dbname = opts["db"] or settings.DATABASES["default"]["NAME"]

        self.stdout.write(self.style.MIGRATE_HEADING(f"Using database: {dbname}"))
        with connection.cursor() as cursor:
            # 0) Force connection to speak utf8mb4 (belt-and-suspenders)
            cursor.execute("SET NAMES 'utf8mb4'")

            # 1) Make the database default utf8mb4
            self.stdout.write("Converting DATABASE default charset/collation to utf8mb4…")
            cursor.execute(SQL_DB_DEFAULT.format(db=dbname))

            # 2) Get list of base tables
            cursor.execute(SQL_LIST_BASE_TABLES, [dbname])
            all_tables = [row[0] for row in cursor.fetchall()]
            self.stdout.write(f"Found {len(all_tables)} base tables.")

            # 3) Build the order: critical first, then the rest
            critical = [t for t in CRITICAL_TABLES if t in all_tables]
            others = [t for t in all_tables if t not in critical]

            if opts["all"]:
                ordered = critical + others
            else:
                # Safe default: do critical ones first; attempt others after (best-effort)
                ordered = critical + others

            # 4) Convert tables with FK checks disabled
            failed = []
            success = []
            self.stdout.write("Disabling foreign key checks…")
            cursor.execute("SET FOREIGN_KEY_CHECKS=0;")
            try:
                for table in ordered:
                    try:
                        self.stdout.write(f"Converting `{table}` → utf8mb4…")
                        cursor.execute(SQL_CONVERT_TABLE.format(table=table))
                        success.append(table)
                    except Exception as e:
                        failed.append((table, str(e)))
                        self.stderr.write(self.style.WARNING(f"[SKIP] {table}: {e}"))
                self.stdout.write("Re-enabling foreign key checks…")
            finally:
                cursor.execute("SET FOREIGN_KEY_CHECKS=1;")

            # 5) Report non-utf8mb4 columns left (if any)
            self.stdout.write("\nChecking for any remaining non-utf8mb4 columns…")
            cursor.execute(SQL_LIST_NON_UTF8MB4_COLUMNS, [dbname])
            leftovers = cursor.fetchall()

        # Pretty report
        self.stdout.write(self.style.SUCCESS(f"\n✅ Converted tables: {len(success)}"))
        if failed:
            self.stdout.write(self.style.WARNING(f"⚠️  Tables that failed to convert: {len(failed)}"))
            for t, msg in failed:
                self.stdout.write(f"    - {t}: {msg}")

        if leftovers:
            self.stdout.write(self.style.WARNING("\nColumns still NOT utf8mb4:"))
            last_table = None
            for tbl, col, ch, co in leftovers:
                if tbl != last_table:
                    self.stdout.write(f"  • {tbl}")
                    last_table = tbl
                self.stdout.write(f"     - {col}: {ch}/{co}")
            self.stdout.write(
                "\nCommon fix for old MySQL index-length errors:\n"
                "  - Reduce indexed VARCHAR(255) to VARCHAR(191) OR\n"
                "  - Drop/recreate index with prefix length (e.g., INDEX(col(191)))\n"
                "Tell me the failing table and I’ll generate exact ALTERs."
            )
        else:
            self.stdout.write(self.style.SUCCESS("\n🎉 All text columns appear to be utf8mb4. You're good!"))
