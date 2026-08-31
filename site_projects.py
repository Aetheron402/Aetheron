"""
The sites a wallet has built, and every version of each one.

A generated page is rarely right the first time. Somebody wants the headline
changed, a Telegram link they had not set up yet, a different accent colour. The
ledger cannot answer that on its own: it records that a wallet paid for
site-builder and which file came out, but not what the page was built from, so
there is nothing to revise against. This is where that lives.

A project is one site. A version is one generated file belonging to it. Revising
adds a version rather than replacing the old one, so an earlier draft can always
be downloaded again if a change turns out worse than what it replaced.

The token details are stored as JSON rather than columns because a revision can
change any of them, and because a pre-launch site and a launched one carry
different fields. What matters here is that the next build starts from what the
last one used, instead of from an empty form.
"""

import json
import time
import uuid

from ledger_utils import _cursor, _q, USE_POSTGRES

_initialised = False


def init():
    """Create the tables. Safe to call repeatedly, called before every use."""
    global _initialised
    if _initialised:
        return

    text_pk = "TEXT PRIMARY KEY"
    with _cursor(commit=True) as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS site_projects (
                project_id {text_pk},
                wallet TEXT,
                name TEXT,
                symbol TEXT,
                mint TEXT,
                direction TEXT,
                details TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            """
        )
        # One row per build. asset_id is the primary key because that is what
        # the ledger and the download route already key on, so a version can be
        # matched to its payment without a second lookup.
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS site_versions (
                asset_id {text_pk},
                project_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                notes TEXT,
                filename TEXT,
                status TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            """
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_site_projects_wallet "
            "ON site_projects (wallet);",
            "CREATE INDEX IF NOT EXISTS idx_site_versions_project "
            "ON site_versions (project_id);",
        ):
            cur.execute(statement)

        # Added after the table shipped, so CREATE TABLE IF NOT EXISTS will not
        # bring them in on a database that already has one. Tried and ignored
        # rather than checked, because both backends spell the check
        # differently and a duplicate column is the only thing this can fail on.
        for column in ("direction_offset INTEGER DEFAULT 0",
                       "rerolls INTEGER DEFAULT 0"):
            try:
                cur.execute(f"ALTER TABLE site_projects ADD COLUMN {column};")
            except Exception:
                pass

    _initialised = True


def start(wallet, token, direction, notes, asset_id) -> str:
    """
    Open a new project and record its first version.

    Called when the build is queued rather than when it finishes, so a job that
    fails still leaves a trace the buyer can see and retry from.
    """
    init()
    project_id = "SITE-" + uuid.uuid4().hex[:12].upper()
    now = time.time()

    details = {
        "name": token.get("name"),
        "symbol": token.get("symbol"),
        "description": token.get("description"),
        "image": token.get("image"),
        "twitter": (token.get("socials") or {}).get("twitter"),
        "telegram": (token.get("socials") or {}).get("telegram"),
        "website": (token.get("socials") or {}).get("website"),
    }

    with _cursor(commit=True) as cur:
        cur.execute(
            _q("""
                INSERT INTO site_projects
                    (project_id, wallet, name, symbol, mint, direction,
                     details, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
            """),
            (project_id, wallet, token.get("name"), token.get("symbol"),
             token.get("mint"), direction, json.dumps(details), now, now),
        )
        cur.execute(
            _q("""
                INSERT INTO site_versions
                    (asset_id, project_id, version, notes, filename,
                     status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s);
            """),
            (asset_id, project_id, 1, notes, None, "pending", now),
        )

    return project_id


def add_version(project_id, asset_id, notes) -> int:
    """
    Record a revision of an existing project and return its version number.

    The number is derived from what is already stored rather than passed in, so
    two revisions bought at the same moment cannot both claim to be version 3.
    """
    init()
    now = time.time()

    with _cursor(commit=True) as cur:
        cur.execute(
            _q("SELECT COALESCE(MAX(version), 0) FROM site_versions "
               "WHERE project_id = %s;"),
            (project_id,),
        )
        version = int((cur.fetchone() or [0])[0]) + 1

        cur.execute(
            _q("""
                INSERT INTO site_versions
                    (asset_id, project_id, version, notes, filename,
                     status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s);
            """),
            (asset_id, project_id, version, notes, None, "pending", now),
        )
        cur.execute(
            _q("UPDATE site_projects SET updated_at = %s WHERE project_id = %s;"),
            (now, project_id),
        )

    return version


def finish(asset_id, filename):
    """Mark a version built and attach the file it produced."""
    init()
    with _cursor(commit=True) as cur:
        cur.execute(
            _q("UPDATE site_versions SET filename = %s, status = %s "
               "WHERE asset_id = %s;"),
            (filename, "complete", asset_id),
        )


def fail(asset_id):
    """Mark a version failed, so the buyer sees why nothing arrived."""
    init()
    with _cursor(commit=True) as cur:
        cur.execute(
            _q("UPDATE site_versions SET status = %s WHERE asset_id = %s;"),
            ("failed", asset_id),
        )


def update_details(project_id, details, direction=None):
    """
    Replace the stored token details, for a revision that changed them.

    Only keys with a value are written. A revision that adds a Telegram link
    must not wipe the description because the form field for it was left empty.
    """
    init()
    current = get(project_id)
    if not current:
        return

    merged = dict(current["details"])
    for key, value in (details or {}).items():
        if value not in (None, ""):
            merged[key] = value

    with _cursor(commit=True) as cur:
        cur.execute(
            _q("""
                UPDATE site_projects
                SET details = %s, name = %s, symbol = %s,
                    direction = COALESCE(%s, direction), updated_at = %s
                WHERE project_id = %s;
            """),
            (json.dumps(merged), merged.get("name"), merged.get("symbol"),
             direction, time.time(), project_id),
        )


def rerolls_used(project_id) -> int:
    """
    How many times a different design has been asked for.

    Read rather than counted from the versions, because a reroll and a revision
    both add a version and only one of them is free.
    """
    init()
    try:
        with _cursor() as cur:
            cur.execute(
                _q("SELECT rerolls FROM site_projects WHERE project_id = %s;"),
                (project_id,),
            )
            row = cur.fetchone()
        return int(row[0] or 0) if row else 0
    except Exception:
        # Not knowing has to mean charging rather than giving one away, since
        # the free one is the thing this number guards.
        return 1


def next_direction(project_id) -> int:
    """
    Move to the next design and return which one to use.

    Walks rather than rehashes, so rerolling never lands back on the design
    somebody has just rejected until every other one has been seen. Rehashing
    with a salt could return the same look twice in a row, which reads as the
    button being broken.
    """
    init()
    with _cursor(commit=True) as cur:
        cur.execute(
            _q("SELECT direction_offset FROM site_projects WHERE project_id = %s;"),
            (project_id,),
        )
        row = cur.fetchone()
        offset = int((row[0] if row and row[0] is not None else 0)) + 1

        cur.execute(
            _q("UPDATE site_projects SET direction_offset = %s, "
               "rerolls = COALESCE(rerolls, 0) + 1, updated_at = %s "
               "WHERE project_id = %s;"),
            (offset, time.time(), project_id),
        )
    return offset


def get(project_id) -> dict | None:
    """One project, with its versions newest first."""
    init()
    with _cursor() as cur:
        cur.execute(
            _q("""
                SELECT project_id, wallet, name, symbol, mint, direction,
                       details, created_at, updated_at
                FROM site_projects WHERE project_id = %s;
            """),
            (project_id,),
        )
        row = cur.fetchone()
        if not row:
            return None

        cur.execute(
            _q("""
                SELECT asset_id, version, notes, filename, status, created_at
                FROM site_versions WHERE project_id = %s
                ORDER BY version DESC;
            """),
            (project_id,),
        )
        versions = [
            {"asset_id": v[0], "version": v[1], "notes": v[2],
             "filename": v[3], "status": v[4], "created_at": v[5]}
            for v in cur.fetchall()
        ]

    try:
        details = json.loads(row[6]) if row[6] else {}
    except (TypeError, ValueError):
        details = {}

    return {
        "project_id": row[0], "wallet": row[1], "name": row[2],
        "symbol": row[3], "mint": row[4], "direction": row[5],
        "details": details, "created_at": row[7], "updated_at": row[8],
        "versions": versions,
    }


def latest_file(project_id) -> str | None:
    """
    The most recent successfully built file for a project.

    A revision is applied to this rather than generated from scratch, which is
    the whole reason a revision costs less than a build and comes back looking
    like the page it is meant to be changing.
    """
    init()
    with _cursor() as cur:
        cur.execute(
            _q("""
                SELECT filename FROM site_versions
                WHERE project_id = %s AND status = %s AND filename IS NOT NULL
                ORDER BY version DESC LIMIT 1;
            """),
            (project_id, "complete"),
        )
        row = cur.fetchone()
    return row[0] if row else None


def for_wallet(wallet, limit=25) -> list:
    """
    Every site this wallet has built, most recently touched first.

    Returns nothing for an empty wallet rather than everything. A missing wallet
    on this query would otherwise hand one buyer somebody else's projects.
    """
    init()
    if not wallet:
        return []

    with _cursor() as cur:
        cur.execute(
            _q("""
                SELECT project_id, name, symbol, mint, direction, updated_at
                FROM site_projects WHERE wallet = %s
                ORDER BY updated_at DESC LIMIT %s;
            """),
            (wallet, limit),
        )
        rows = cur.fetchall()

    projects = []
    for row in rows:
        project_id = row[0]
        with _cursor() as cur:
            cur.execute(
                _q("""
                    SELECT asset_id, version, filename, status, created_at
                    FROM site_versions WHERE project_id = %s
                    ORDER BY version DESC;
                """),
                (project_id,),
            )
            versions = [
                {"asset_id": v[0], "version": v[1], "filename": v[2],
                 "status": v[3], "created_at": v[4]}
                for v in cur.fetchall()
            ]

        projects.append({
            "project_id": project_id, "name": row[1], "symbol": row[2],
            "mint": row[3], "direction": row[4], "updated_at": row[5],
            "versions": versions,
        })

    return projects


def owned_by(project_id, wallet) -> bool:
    """
    Whether this wallet may revise this project.

    Checked before a revision is priced, not after it is built. Without it any
    wallet could pay the revision price and edit somebody else's page, which is
    cheaper than buying one and is not theirs to change.
    """
    project = get(project_id)
    return bool(project and wallet and project["wallet"] == wallet)
