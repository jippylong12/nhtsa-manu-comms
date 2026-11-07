"""SQL for the users table."""

import asyncpg


async def upsert_user(
    conn: asyncpg.Connection,
    firebase_uid: str,
    email: str,
    display_name: str | None,
    is_admin: bool,
) -> dict:
    """Create the user on first sight, else refresh email, name and last_seen_at.

    Admin is sticky: a later sign-in never demotes an existing admin.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO users (firebase_uid, email, display_name, is_admin)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (firebase_uid) DO UPDATE SET
            email = EXCLUDED.email,
            display_name = EXCLUDED.display_name,
            last_seen_at = now(),
            is_admin = users.is_admin OR EXCLUDED.is_admin
        RETURNING *
        """,
        firebase_uid,
        email,
        display_name,
        is_admin,
    )
    return dict(row)


async def get_user_by_id(conn: asyncpg.Connection, user_id: int) -> dict | None:
    """One user by id, or None."""
    row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    return dict(row) if row else None
