import json
import sqlite3
from dataclasses import asdict, dataclass, field

@dataclass
class Extension:
    id: str
    name: str
    version: str
    category: str
    summary: str
    description: str = ""
    author_name: str = ""
    # Keep Discord snowflake IDs as strings to avoid integer-size/precision issues.
    author_id: str = ""
    updated: str = ""
    download: str = ""
    icon: str = "✦"
    requirements: list[str] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    status: str = "approved"
    changelog: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _ensure_schema(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS extensions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            version TEXT NOT NULL,
            category TEXT NOT NULL,
            summary TEXT NOT NULL,
            description TEXT NOT NULL,
            author_name TEXT NOT NULL,
            author_id TEXT NOT NULL,
            updated TEXT NOT NULL,
            download TEXT NOT NULL,
            icon TEXT NOT NULL,
            requirements TEXT NOT NULL,
            screenshots TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'approved',
            changelog TEXT NOT NULL DEFAULT ''
        )
        """
    )
    columns = {row[1] for row in db.execute("PRAGMA table_info(extensions)")}
    if "screenshots" not in columns:
        db.execute("ALTER TABLE extensions ADD COLUMN screenshots TEXT NOT NULL DEFAULT '[]'")
    if "status" not in columns:
        db.execute("ALTER TABLE extensions ADD COLUMN status TEXT NOT NULL DEFAULT 'approved'")
    if "changelog" not in columns:
        db.execute("ALTER TABLE extensions ADD COLUMN changelog TEXT NOT NULL DEFAULT ''")
    # Keep records accepted under the previous review status visible as approved.
    db.execute("UPDATE extensions SET status = 'approved' WHERE status = 'confirmed'")


def save_extension(db: sqlite3.Connection, extension: Extension) -> None:
    """Create/update an extension row in the supplied SQLite connection."""
    _ensure_schema(db)
    db.execute(
        """
        INSERT INTO extensions (
            id, name, version, category, summary, description, author_name,
            author_id, updated, download, icon, requirements, screenshots, status, changelog
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            version=excluded.version,
            category=excluded.category,
            summary=excluded.summary,
            description=excluded.description,
            author_name=excluded.author_name,
            author_id=excluded.author_id,
            updated=excluded.updated,
            download=excluded.download,
            icon=excluded.icon,
            requirements=excluded.requirements,
            screenshots=excluded.screenshots,
            status=excluded.status,
            changelog=excluded.changelog
        """,
        (
            extension.id,
            extension.name,
            extension.version,
            extension.category,
            extension.summary,
            extension.description,
            extension.author_name,
            str(extension.author_id),
            extension.updated,
            extension.download,
            extension.icon,
            json.dumps(extension.requirements),
            json.dumps(extension.screenshots),
            extension.status,
            extension.changelog,
        ),
    )
    db.commit()


def load_extensions(db: sqlite3.Connection, status: str | None = "approved") -> list[Extension]:
    """Load approved extensions by default; pass None to include every status."""
    _ensure_schema(db)
    rows = db.execute(
        """
        SELECT id, name, version, category, summary, description, author_name,
               author_id, updated, download, icon, requirements, screenshots, status, changelog
        FROM extensions
        WHERE (? IS NULL OR status = ?)
        ORDER BY name COLLATE NOCASE
        """,
        (status, status),
    ).fetchall()
    return [
        Extension(
            id=row[0],
            name=row[1],
            version=row[2],
            category=row[3],
            summary=row[4],
            description=row[5],
            author_name=row[6],
            author_id=row[7],
            updated=row[8],
            download=row[9],
            icon=row[10],
            requirements=json.loads(row[11]),
            screenshots=json.loads(row[12] or "[]"),
            status=row[13],
            changelog=row[14],
        )
        for row in rows
    ]
