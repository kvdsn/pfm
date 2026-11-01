import glob
import logging
import os
import re
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator, Optional


logger = logging.getLogger("pfm")

AUTOMATIC_BACKUP_LABELS = frozenset({
    "before import",
    "before undoing import",
    "before merging categories",
    "before removing category",
    "before applying rules",
    "before deleting rule",
    "before restoring",
    "before upgrade",
})
KEEP_AUTOMATIC_BACKUPS = 20

ACCOUNT_TYPES = ("personal", "business", "kids")
# The entities that existed before "kids"; older databases carry a CHECK listing only these.
LEGACY_ACCOUNT_TYPES = ("personal", "business")
ACCOUNT_TYPE_CHECK = "account_type IN (" + ", ".join(f"'{kind}'" for kind in ACCOUNT_TYPES) + ")"
MATCH_MODES = ("contains", "word", "exact")
MATCH_MODE_CHECK = "match_mode IN (" + ", ".join(f"'{mode}'" for mode in MATCH_MODES) + ")"
FLOW_TYPES = ("income", "expense", "transfer")
UNCATEGORIZED = "Uncategorized"
SYSTEM_CATEGORIES = (UNCATEGORIZED,)

CATEGORY_RULES_TABLE = f"""
CREATE TABLE IF NOT EXISTS category_rules (
    id INTEGER PRIMARY KEY,
    account_type TEXT NOT NULL CHECK({ACCOUNT_TYPE_CHECK}),
    category_id INTEGER NOT NULL REFERENCES categories(id),
    match_text TEXT NOT NULL,
    match_fields TEXT NOT NULL,
    flow_type TEXT NOT NULL CHECK(flow_type IN ('income', 'expense', 'transfer')),
    priority INTEGER NOT NULL,
    legacy_normalized INTEGER NOT NULL DEFAULT 0,
    match_mode TEXT NOT NULL DEFAULT 'contains' CHECK({MATCH_MODE_CHECK})
);
"""

ACCOUNTS_TABLE = f"""
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    iban TEXT NOT NULL UNIQUE,
    account_type TEXT NOT NULL CHECK({ACCOUNT_TYPE_CHECK}),
    created_at TEXT NOT NULL,
    bank TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'bank' CHECK(kind IN ('bank', 'card'))
);
"""

CATEGORIES_TABLE = f"""
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    account_type TEXT NOT NULL CHECK({ACCOUNT_TYPE_CHECK}),
    name TEXT NOT NULL,
    is_income INTEGER NOT NULL DEFAULT 0
);
"""

# Tables whose CHECKs must list every current value (see CHECKED_COLUMNS). SQLite cannot alter a CHECK,
# so older ones are rebuilt.
ACCOUNT_TYPE_TABLES = {
    "accounts": ACCOUNTS_TABLE,
    "categories": CATEGORIES_TABLE,
    "category_rules": CATEGORY_RULES_TABLE,
}
# Each constrained column with its allowed values and the tables whose CHECK on it is kept current.
CHECKED_COLUMNS = {
    "account_type": (ACCOUNT_TYPES, ("accounts", "categories", "category_rules")),
    "match_mode": (MATCH_MODES, ("category_rules",)),
}

SCHEMA = ACCOUNTS_TABLE + CATEGORIES_TABLE + """
CREATE UNIQUE INDEX IF NOT EXISTS categories_name_idx ON categories(account_type, name);
CREATE TABLE IF NOT EXISTS category_budgets (
    id INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    year TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
    UNIQUE(category_id, year)
);
CREATE TABLE IF NOT EXISTS recurring_items (
    id INTEGER PRIMARY KEY,
    account_type TEXT NOT NULL,
    merchant TEXT NOT NULL,
    merchant_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('confirmed', 'ignored')),
    cadence TEXT NOT NULL CHECK(cadence IN ('monthly', 'quarterly', 'yearly')),
    day_of_month INTEGER CHECK(day_of_month BETWEEN 1 AND 31),
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    start_date TEXT NOT NULL,
    end_date TEXT,
    note TEXT NOT NULL DEFAULT '',
    linked_item_id INTEGER REFERENCES recurring_items(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(account_type, merchant_key, kind)
);
CREATE TABLE IF NOT EXISTS recurring_amounts (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES recurring_items(id) ON DELETE CASCADE,
    effective_date TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK(amount_cents != 0),
    UNIQUE(item_id, effective_date)
);
CREATE TABLE IF NOT EXISTS forecast_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS salary_schedules (
    year TEXT PRIMARY KEY,
    monthly_cents INTEGER NOT NULL CHECK(monthly_cents > 0),
    last_month INTEGER NOT NULL CHECK(last_month BETWEEN 1 AND 12),
    minimum_cents INTEGER NOT NULL DEFAULT 0 CHECK(minimum_cents >= 0)
);
CREATE TABLE IF NOT EXISTS day_rates (
    effective_date TEXT PRIMARY KEY,
    rate_cents INTEGER NOT NULL CHECK(rate_cents > 0)
);
CREATE TABLE IF NOT EXISTS days_off (
    id INTEGER PRIMARY KEY,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    fraction REAL NOT NULL DEFAULT 1 CHECK(fraction IN (0.5, 1)),
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS planned_items (
    id INTEGER PRIMARY KEY,
    account_type TEXT NOT NULL,
    date TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK(amount_cents != 0),
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    note TEXT NOT NULL DEFAULT '',
    linked_item_id INTEGER REFERENCES planned_items(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reimbursement_claims (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    description TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
    share REAL,
    source_transaction_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    personal_category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    business_category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    expected_date TEXT,
    paid_transaction_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS forecast_snapshots (
    id INTEGER PRIMARY KEY,
    month TEXT NOT NULL UNIQUE,
    taken_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS forecast_snapshot_balances (
    snapshot_id INTEGER NOT NULL REFERENCES forecast_snapshots(id) ON DELETE CASCADE,
    account_type TEXT NOT NULL,
    month TEXT NOT NULL,
    closing_cents INTEGER NOT NULL,
    low_cents INTEGER NOT NULL,
    high_cents INTEGER NOT NULL,
    PRIMARY KEY(snapshot_id, account_type, month)
);
CREATE TABLE IF NOT EXISTS forecast_snapshot_lines (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES forecast_snapshots(id) ON DELETE CASCADE,
    account_type TEXT NOT NULL,
    month TEXT NOT NULL,
    category_id INTEGER NOT NULL,
    category_name TEXT NOT NULL,
    is_income INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL,
    source TEXT NOT NULL,
    day TEXT,
    recurring_id INTEGER REFERENCES recurring_items(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS forecast_snapshot_lines_idx
    ON forecast_snapshot_lines(account_type, month);
CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    filename TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    inserted_count INTEGER NOT NULL,
    duplicate_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    import_id INTEGER NOT NULL REFERENCES imports(id),
    row_number INTEGER NOT NULL,
    booking_date TEXT NOT NULL,
    balance_date TEXT NOT NULL,
    value_date TEXT,
    amount_cents INTEGER NOT NULL,
    source_amount_cents INTEGER NOT NULL,
    balance_cents INTEGER,
    currency TEXT NOT NULL,
    merchant TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    counterparty_iban TEXT NOT NULL DEFAULT '',
    reference TEXT NOT NULL DEFAULT '',
    category_id INTEGER REFERENCES categories(id),
    flow_type TEXT NOT NULL CHECK(flow_type IN ('income', 'expense', 'transfer')),
    source_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    deleted_at TEXT,
    statement_id INTEGER REFERENCES card_statements(id),
    replaced_by_statement_id INTEGER REFERENCES card_statements(id),
    note TEXT NOT NULL DEFAULT '',
    follow_up TEXT CHECK(follow_up IN ('open', 'done')),
    follow_up_at TEXT,
    UNIQUE(account_id, fingerprint)
);
CREATE TABLE IF NOT EXISTS card_statements (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    statement_key TEXT NOT NULL,
    debit_date TEXT NOT NULL,
    UNIQUE(account_id, statement_key)
);
""" + CATEGORY_RULES_TABLE + """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name TEXT PRIMARY KEY
);
CREATE INDEX IF NOT EXISTS category_rules_account_priority_idx
    ON category_rules(account_type, priority, id);
CREATE INDEX IF NOT EXISTS transactions_date_idx
    ON transactions(booking_date, account_id);
CREATE INDEX IF NOT EXISTS transactions_account_kind_idx
    ON transactions(account_id, flow_type, deleted_at);
"""

# Columns added after the first release, with their definitions for ALTER TABLE.
ADDED_COLUMNS = {
    "accounts": {
        "bank": "TEXT NOT NULL DEFAULT ''",
        "kind": "TEXT NOT NULL DEFAULT 'bank' CHECK(kind IN ('bank', 'card'))",
    },
    "transactions": {
        "statement_id": "INTEGER REFERENCES card_statements(id)",
        "replaced_by_statement_id": "INTEGER REFERENCES card_statements(id)",
        "category_source": "TEXT NOT NULL DEFAULT 'default' CHECK(category_source IN ('rule', 'manual', 'default'))",
        # The rule that set the category; NULL unless category_source is 'rule'.
        "category_rule_id": "INTEGER",
        # A free-text note and an optional follow-up flag; neither affects the category or any total.
        "note": "TEXT NOT NULL DEFAULT ''",
        "follow_up": "TEXT CHECK(follow_up IN ('open', 'done'))",
        "follow_up_at": "TEXT",
    },
    "category_rules": {
        "match_mode": f"TEXT NOT NULL DEFAULT 'contains' CHECK({MATCH_MODE_CHECK})",
    },
    # Whether the category counts as income in reports; see backfill_income_categories.
    "categories": {
        "is_income": "INTEGER NOT NULL DEFAULT 0",
    },
    # The salary paid each month after a schedule's last month; 0 (none) for schedules saved before it existed.
    "salary_schedules": {
        "minimum_cents": "INTEGER NOT NULL DEFAULT 0 CHECK(minimum_cents >= 0)",
    },
    # Days off that repeat every so many weeks (0: once), optionally until a date; existing rows happen once.
    "days_off": {
        "repeat_weeks": "INTEGER NOT NULL DEFAULT 0 CHECK(repeat_weeks BETWEEN 0 AND 52)",
        "repeat_until": "TEXT",
    },
}
INDEXES = """
CREATE INDEX IF NOT EXISTS transactions_statement_idx ON transactions(statement_id);
CREATE INDEX IF NOT EXISTS transactions_replaced_idx ON transactions(replaced_by_statement_id);
CREATE INDEX IF NOT EXISTS transactions_counterparty_idx ON transactions(counterparty_iban);
CREATE INDEX IF NOT EXISTS transactions_follow_up_idx ON transactions(follow_up);
"""


def default_database_path() -> Path:
    if os.environ.get("PFM_HOME"):
        base = Path(os.environ["PFM_HOME"])
    else:
        base = Path(__file__).resolve().parents[3] / "data"
    return base / "finance.sqlite3"


def default_backup_dir(database_path: Path) -> Path:
    """The folder named by PFM_BACKUP_DIR, or else the database's own folder."""
    if os.environ.get("PFM_BACKUP_DIR"):
        return Path(os.environ["PFM_BACKUP_DIR"])
    return database_path.parent


@contextmanager
def connect(database_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a connection that commits on success, rolls back on error, and always closes."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize(database_path: Path, backup_dir: Optional[Path] = None) -> Optional[Path]:
    """Create or upgrade the database. Returns the backup path when an upgrade needed one."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    with closing(sqlite3.connect(database_path)) as probe:
        needs_upgrade = _has_legacy_schema(probe)
        needs_columns = bool(_missing_columns(probe))
        needs_rebuild = bool(_stale_check_tables(probe))
    if needs_upgrade or needs_columns or needs_rebuild:
        backup = backup_database(database_path, "before upgrade", backup_dir)
        try:
            prune_automatic_backups(database_path, backup_dir=backup_dir)
        except Exception:
            logger.exception("Could not prune old automatic backups")
    if needs_upgrade:
        _upgrade_legacy_schema(database_path)
    with connect(database_path) as db:
        db.executescript(SCHEMA)
        _add_missing_columns(db)
    _rebuild_stale_tables(database_path)
    with connect(database_path) as db:
        db.executescript(INDEXES)
        _run_once(
            db,
            "merchant-is-counterparty",
            # Early imports stored KBC's account holder ("Naam") as the merchant.
            """UPDATE transactions SET merchant = COALESCE(json_extract(source_json, '$."Naam tegenpartij"'), '')""",
        )
        for account_type in ACCOUNT_TYPES:
            _ensure_category(db, account_type, UNCATEGORIZED)
    return backup


def backup_database(database_path: Path, label: str = "", backup_dir: Optional[Path] = None) -> Path:
    """Copy the database into the backup folder as finance-backup-<timestamp>[-<label>].sqlite3.

    The folder defaults to default_backup_dir and is created if missing.
    Never overwrites: a backup made in the same second as another gets -2, -3, ... appended.
    """
    backup_dir = backup_dir or default_backup_dir(database_path)
    backup_dir.mkdir(parents=True, exist_ok=True)
    base = f"{database_path.stem}-backup-{datetime.now():%Y%m%d-%H%M%S}"
    slug = _slugify(label)
    if slug:
        base += f"-{slug}"
    target = backup_dir / f"{base}{database_path.suffix}"
    counter = 2
    while target.exists():
        target = backup_dir / f"{base}-{counter}{database_path.suffix}"
        counter += 1
    with closing(sqlite3.connect(database_path)) as source, closing(sqlite3.connect(target)) as copy:
        source.backup(copy)
    return target


def _slugify(label: str) -> str:
    """Lowercase letters and digits joined by single dashes, at most 40 characters."""
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40].strip("-")


def is_automatic_label(label: str) -> bool:
    """Whether a listed note is one of the automatic labels, allowing the -2, -3 suffix of same-second backups."""
    return label in AUTOMATIC_BACKUP_LABELS or re.sub(r" \d+$", "", label) in AUTOMATIC_BACKUP_LABELS


def is_reserved_note(note: str) -> bool:
    """Whether a manual backup's note would be listed as an automatic backup's."""
    return is_automatic_label(_slugify(note).replace("-", " "))


def list_backups(database_path: Path, backup_dir: Optional[Path] = None) -> list[dict]:
    """Backups in the backup folder (default: default_backup_dir), newest first."""
    backup_dir = backup_dir or default_backup_dir(database_path)
    prefix = f"{database_path.stem}-backup-"
    backups = []
    for path in backup_dir.glob(f"{glob.escape(prefix)}*{glob.escape(database_path.suffix)}"):
        if not path.is_file():
            continue
        tail = path.name[len(prefix):-len(database_path.suffix)]
        try:
            created = datetime.strptime(tail[:15], "%Y%m%d-%H%M%S")
            rest = tail[15:]
        except ValueError:
            created = datetime.fromtimestamp(path.stat().st_mtime)
            rest = tail
        label = "" if re.fullmatch(r"-\d+", rest) else rest.strip("-").replace("-", " ")
        stat = path.stat()
        backups.append({
            "_modified": stat.st_mtime_ns,
            "name": path.name,
            "created_at": created.isoformat(timespec="seconds"),
            "label": label,
            "automatic": is_automatic_label(label),
            "size_bytes": stat.st_size,
        })
    # Backups made in the same second sort by modification time, so the latest one is first.
    backups.sort(key=lambda item: (item["created_at"], item["_modified"], item["name"]), reverse=True)
    for item in backups:
        del item["_modified"]
    return backups


def prune_automatic_backups(
    database_path: Path, keep: int = KEEP_AUTOMATIC_BACKUPS, backup_dir: Optional[Path] = None
) -> list[str]:
    """Delete the oldest automatic backups beyond the newest `keep`. Manual backups are never touched.

    Returns the deleted file names.
    """
    backup_dir = backup_dir or default_backup_dir(database_path)
    automatic = [item for item in list_backups(database_path, backup_dir) if item["automatic"]]
    deleted = []
    for item in automatic[max(keep, 0):]:
        (backup_dir / item["name"]).unlink()
        deleted.append(item["name"])
    return deleted


RESTORE_REQUIRED_TABLES = {"transactions", "accounts", "categories"}


def restore_backup(database_path: Path, name: str, backup_dir: Optional[Path] = None) -> dict:
    """Replace the live database with a backup from the backup folder and upgrade it if it is older.

    Only names that list_backups returns are accepted. The backup is checked read-only before the
    live database is touched, and copied with sqlite's backup API so it works while the app runs.
    """
    backup_dir = backup_dir or default_backup_dir(database_path)
    entry = next((item for item in list_backups(database_path, backup_dir) if item["name"] == name), None)
    if entry is None:
        raise LookupError("Backup not found")
    source_path = backup_dir / entry["name"]
    unusable = ValueError(f"{name} is not a usable backup")
    try:
        # immutable: a backup is never written to, and read-only WAL files cannot be opened otherwise.
        with closing(sqlite3.connect(f"{source_path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)) as source:
            if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise unusable
            if not RESTORE_REQUIRED_TABLES <= _tables(source):
                raise unusable
            with closing(sqlite3.connect(database_path)) as live:
                source.backup(live)
    except sqlite3.DatabaseError as error:
        raise unusable from error
    initialize(database_path, backup_dir)
    return entry


def _tables(db: sqlite3.Connection) -> set[str]:
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def _run_once(db: sqlite3.Connection, name: str, sql: str) -> None:
    """Run a data migration unless it ran before."""
    if db.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (name,)).fetchone():
        return
    db.execute(sql)
    db.execute("INSERT INTO schema_migrations(name) VALUES (?)", (name,))


def _missing_columns(db: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """Added columns that existing tables still lack."""
    tables = _tables(db)
    missing = {}
    for table, columns in ADDED_COLUMNS.items():
        if table in tables:
            existing = _columns(db, table)
            absent = {name: definition for name, definition in columns.items() if name not in existing}
            if absent:
                missing[table] = absent
    return missing


def _add_missing_columns(db: sqlite3.Connection) -> None:
    """Add columns introduced after a database was created, filling them in for existing rows."""
    missing = _missing_columns(db)
    for table, columns in missing.items():
        for name, definition in columns.items():
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    if "bank" in missing.get("accounts", {}):
        # Only Belfius exports have a "Boekingsdatum" column.
        db.execute(
            """
            UPDATE accounts SET bank = CASE WHEN EXISTS (
                SELECT 1 FROM transactions t
                WHERE t.account_id = accounts.id
                  AND json_extract(t.source_json, '$.Boekingsdatum') IS NOT NULL
            ) THEN 'Belfius' ELSE 'KBC' END
            """
        )
    if "statement_id" in missing.get("transactions", {}):
        db.execute("UPDATE transactions SET counterparty_iban = upper(replace(counterparty_iban, ' ', ''))")
    if "category_source" in missing.get("transactions", {}):
        # Imported here because rules.py imports this module.
        from .rules import backfill_category_sources

        # The tables may still carry an old CHECK; the rebuild that follows adds the newer entities.
        stale = _stale_account_type_tables(db)
        for account_type in ACCOUNT_TYPES:
            if "categories" not in stale or account_type in LEGACY_ACCOUNT_TYPES:
                _ensure_category(db, account_type, UNCATEGORIZED)
        backfill_category_sources(db)
    if "is_income" in missing.get("categories", {}):
        from .categories import backfill_income_categories

        backfill_income_categories(db)


def _has_legacy_schema(db: sqlite3.Connection) -> bool:
    """Older versions archived categories with an `active` flag and kept merchant rules."""
    tables = _tables(db)
    return (
        "merchant_rules" in tables
        or ("categories" in tables and "active" in _columns(db, "categories"))
        or ("category_rules" in tables and "legacy_rule_id" in _columns(db, "category_rules"))
    )


def _ensure_category(db: sqlite3.Connection, account_type: str, name: str) -> int:
    row = db.execute(
        "SELECT id FROM categories WHERE account_type = ? AND name = ?", (account_type, name)
    ).fetchone()
    if row:
        return int(row[0])
    return int(db.execute(
        "INSERT INTO categories(account_type, name) VALUES (?, ?)", (account_type, name)
    ).lastrowid)


def _upgrade_legacy_schema(database_path: Path) -> None:
    """Remove archived categories, migrate merchant rules, and drop the legacy structures.

    Runs in one transaction with foreign keys checked before committing.
    """
    with closing(sqlite3.connect(database_path, isolation_level=None)) as db:
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("BEGIN IMMEDIATE")
        try:
            tables = _tables(db)
            if "active" in _columns(db, "categories"):
                _remove_archived_categories(db, tables)
                db.execute("DROP INDEX IF EXISTS categories_active_name_idx")
                db.execute("ALTER TABLE categories DROP COLUMN active")
            if "merchant_rules" in tables:
                _migrate_merchant_rules(db)
                db.execute("DROP TABLE merchant_rules")
            if "legacy_rule_id" in _columns(db, "category_rules"):
                db.execute("DROP TABLE IF EXISTS category_rules_upgrade")
                db.execute(CATEGORY_RULES_TABLE.replace("category_rules", "category_rules_upgrade", 1))
                db.execute(
                    """
                    INSERT INTO category_rules_upgrade(
                        id, account_type, category_id, match_text, match_fields, flow_type,
                        priority, legacy_normalized
                    )
                    SELECT id, account_type, category_id, match_text, match_fields, flow_type,
                           priority, legacy_normalized
                    FROM category_rules
                    """
                )
                db.execute("DROP TABLE category_rules")
                db.execute("ALTER TABLE category_rules_upgrade RENAME TO category_rules")
            problems = db.execute("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise RuntimeError(f"Database upgrade would break references: {problems[:3]}")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise


def _check_values(sql: str, column: str) -> Optional[set[str]]:
    """The values a table definition's CHECK on the column allows, or None when it has no such CHECK."""
    match = re.search(rf"CHECK\s*\(\s*{column}\s+IN\s*\(([^)]*)\)\s*\)", sql, re.I)
    if match is None:
        return None
    return set(re.findall(r"'([^']*)'", match.group(1)))


def _stale_check_tables(db: sqlite3.Connection, columns: tuple[str, ...] = tuple(CHECKED_COLUMNS)) -> list[str]:
    """Tables whose CHECK on one of the columns does not allow every current value."""
    stale = []
    for name, sql in db.execute(
        f"SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name IN ({', '.join('?' * len(ACCOUNT_TYPE_TABLES))})"
        " ORDER BY name",
        tuple(ACCOUNT_TYPE_TABLES),
    ):
        for column in columns:
            values, tables = CHECKED_COLUMNS[column]
            allowed = _check_values(sql or "", column)
            if name in tables and allowed is not None and allowed != set(values):
                stale.append(name)
                break
    return stale


def _stale_account_type_tables(db: sqlite3.Connection) -> list[str]:
    """Tables whose account_type CHECK does not allow every current entity."""
    return _stale_check_tables(db, ("account_type",))


def _rebuild_stale_tables(database_path: Path) -> None:
    """Rebuild tables whose CHECK predates an entity or match mode (SQLite's 12-step ALTER procedure).

    Rows and ids are copied unchanged. Runs in one transaction with foreign keys switched off while the
    tables are swapped, and checked before committing. Does nothing when no table is stale.
    """
    with closing(sqlite3.connect(database_path, isolation_level=None)) as db:
        if not _stale_check_tables(db):
            return
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("BEGIN IMMEDIATE")
        try:
            for table in _stale_check_tables(db):
                columns = ", ".join(row[1] for row in db.execute(f"PRAGMA table_info({table})"))
                indexes = [
                    row[0] for row in db.execute(
                        "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL",
                        (table,),
                    )
                ]
                new_table = f"{table}_rebuild"
                db.execute(f"DROP TABLE IF EXISTS {new_table}")
                db.execute(ACCOUNT_TYPE_TABLES[table].replace(f"CREATE TABLE IF NOT EXISTS {table} ", f"CREATE TABLE {new_table} ", 1))
                db.execute(f"INSERT INTO {new_table}({columns}) SELECT {columns} FROM {table}")
                db.execute(f"DROP TABLE {table}")
                db.execute(f"ALTER TABLE {new_table} RENAME TO {table}")
                for index_sql in indexes:
                    db.execute(index_sql)
            problems = db.execute("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise RuntimeError(f"Database upgrade would break references: {problems[:3]}")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise


def _remove_archived_categories(db: sqlite3.Connection, tables: set[str]) -> None:
    for account_type in LEGACY_ACCOUNT_TYPES:
        if not db.execute(
            "SELECT 1 FROM categories WHERE account_type = ? AND name = ? AND active = 1",
            (account_type, UNCATEGORIZED),
        ).fetchone():
            db.execute(
                "INSERT INTO categories(account_type, name, active) VALUES (?, ?, 1)",
                (account_type, UNCATEGORIZED),
            )
    for category_id, account_type in db.execute(
        "SELECT id, account_type FROM categories WHERE active = 0"
    ).fetchall():
        uncategorized_id = db.execute(
            "SELECT id FROM categories WHERE account_type = ? AND name = ? AND active = 1",
            (account_type, UNCATEGORIZED),
        ).fetchone()[0]
        db.execute(
            "UPDATE transactions SET category_id = ? WHERE category_id = ?", (uncategorized_id, category_id)
        )
        for table in ("category_rules", "merchant_rules"):
            if table in tables:
                db.execute(f"DELETE FROM {table} WHERE category_id = ?", (category_id,))
        db.execute("DELETE FROM categories WHERE id = ?", (category_id,))


def _migrate_merchant_rules(db: sqlite3.Connection) -> None:
    """Copy legacy merchant rules into editable category rules, unless done before."""
    name = "merchant-rules-to-category-rules"
    db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)")
    if db.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (name,)).fetchone():
        return
    db.execute(CATEGORY_RULES_TABLE)
    next_priority = dict(db.execute(
        "SELECT account_type, COALESCE(MAX(priority), -1) + 1 FROM category_rules GROUP BY account_type"
    ).fetchall())
    for account_type, merchant_key, category_id, flow_type in db.execute(
        "SELECT account_type, merchant_key, category_id, flow_type FROM merchant_rules ORDER BY id"
    ).fetchall():
        priority = next_priority.get(account_type, 0)
        db.execute(
            """
            INSERT INTO category_rules(
                account_type, category_id, match_text, match_fields, flow_type, priority, legacy_normalized
            ) VALUES (?, ?, ?, '["merchant"]', ?, ?, 1)
            """,
            (account_type, category_id, merchant_key, flow_type, priority),
        )
        next_priority[account_type] = priority + 1
    db.execute("INSERT INTO schema_migrations(name) VALUES (?)", (name,))


def require_account_type(account_type: str) -> None:
    if account_type not in ACCOUNT_TYPES:
        raise ValueError("Account type must be " + ", ".join(ACCOUNT_TYPES[:-1]) + ", or " + ACCOUNT_TYPES[-1])


def require_flow_type(flow_type: str) -> None:
    if flow_type not in FLOW_TYPES:
        raise ValueError("Flow type must be income, expense, or transfer")


def default_flow_type(amount_cents: int) -> str:
    """The flow type of an unclassified transaction: positive amounts are income."""
    return "income" if amount_cents > 0 else "expense"


def uncategorized_category_id(db: sqlite3.Connection, account_type: str) -> int:
    row = db.execute(
        "SELECT id FROM categories WHERE account_type = ? AND name = ?", (account_type, UNCATEGORIZED)
    ).fetchone()
    if row is None:
        raise RuntimeError(f"The {account_type} account has no {UNCATEGORIZED} category")
    return int(row["id"])


def get_category_id(db: sqlite3.Connection, account_type: str, name: str) -> int:
    """Return the id of the named category, creating it when missing."""
    return _ensure_category(db, account_type, name)


def normalize_key(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", text.upper()).strip()


def parse_money(value: str) -> int:
    cleaned = value.strip().replace(" ", "").replace(" ", "")
    if not cleaned:
        raise ValueError("Amount is empty")
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        decimal_value = Decimal(cleaned)
    except InvalidOperation as error:
        raise ValueError(f"Invalid amount: {value}") from error
    return int((decimal_value * 100).quantize(Decimal("1")))


def format_money(cents: Optional[int]) -> Optional[str]:
    if cents is None:
        return None
    return f"{Decimal(cents) / 100:.2f}"


def masked_iban(iban: str) -> str:
    if iban.startswith("REVOLUT-"):  # a synthetic key: Revolut exports carry no IBAN
        product, _, currency = iban[len("REVOLUT-"):].rpartition("-")
        return f"{product.replace('-', ' ').capitalize()} {currency}".strip()
    return f"•••• {iban[-4:]}"
