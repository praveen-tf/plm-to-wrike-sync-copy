from conftest import seed_folder_map

from folder_sync import sync_folders


class FakeWrikeClient:
    """Stand-in for WrikeClient: serves a fixed top-level folder list and records any
    _PENDING_REVIEW creation (no HTTP, no live Wrike)."""

    def __init__(self, folders, created_folder=None):
        self._folders = folders
        self._created_folder = created_folder
        self.create_calls = []

    def list_top_level_folders(self, space_id):
        return self._folders

    def create_folder(self, parent_folder_id, title):
        self.create_calls.append((parent_folder_id, title))
        return self._created_folder


def _folder_rows(conn):
    """All wrike_folder_map rows (prefix, folder id, full name, brand, space), by prefix."""
    with conn.cursor() as cur:
        cur.execute("SELECT prefix, wrike_folder_id, full_folder_name, brand, space_id "
                    "FROM wrike_folder_map ORDER BY prefix, brand")
        return cur.fetchall()


def test_parses_prefix_brand_and_skips_non_conforming(pg_conn):
    folders = [
        {"id": "F_WP", "title": "WP - Winnie-the-Pooh", "childIds": []},
        {"id": "F_LT", "title": "LT - Lindt", "childIds": []},
        {"id": "F_BAD", "title": "NoSeparatorHere", "childIds": []},   # no " - " -> skipped
        {"id": "F_PR", "title": "_PENDING_REVIEW", "childIds": []},
    ]
    client = FakeWrikeClient(folders)

    summary = sync_folders(pg_conn, client, space_id="ISPACE")

    assert summary == {"found": 4, "inserted": 2, "skipped": 1,
                       "pending_review_created": False}
    assert client.create_calls == []  # _PENDING_REVIEW already existed in the space
    assert _folder_rows(pg_conn) == [
        ("*",  "F_PR", "_PENDING_REVIEW",      "",                "ISPACE"),
        ("LT", "F_LT", "LT - Lindt",           "Lindt",           "ISPACE"),
        ("WP", "F_WP", "WP - Winnie-the-Pooh", "Winnie-the-Pooh", "ISPACE"),
    ]


def test_creates_pending_review_when_absent(pg_conn):
    folders = [{"id": "F_WP", "title": "WP - Winnie-the-Pooh", "childIds": []}]
    client = FakeWrikeClient(
        folders, created_folder={"id": "F_PR_NEW", "title": "_PENDING_REVIEW"})

    summary = sync_folders(pg_conn, client, space_id="ISPACE")

    assert summary["pending_review_created"] is True
    assert client.create_calls == [("ISPACE", "_PENDING_REVIEW")]  # parent = space id
    star = next(r for r in _folder_rows(pg_conn) if r[0] == "*")
    assert star == ("*", "F_PR_NEW", "_PENDING_REVIEW", "", "ISPACE")


def test_rebuild_drops_stale_rows(pg_conn):
    seed_folder_map(pg_conn, {"OLD": "F_OLD"})  # a leftover row from a previous space
    folders = [{"id": "F_WP", "title": "WP - Winnie-the-Pooh", "childIds": []},
               {"id": "F_PR", "title": "_PENDING_REVIEW", "childIds": []}]

    sync_folders(pg_conn, client=FakeWrikeClient(folders), space_id="ISPACE")

    assert [r[0] for r in _folder_rows(pg_conn)] == ["*", "WP"]  # OLD wiped


def test_keeps_same_prefix_with_different_brands(pg_conn):
    # Two brands can share a prefix (MB - MR BEAST vs MB - MEAT BOARDS): keep BOTH rows.
    folders = [{"id": "F_MB1", "title": "MB - MR BEAST", "childIds": []},
               {"id": "F_MB2", "title": "MB - MEAT BOARDS", "childIds": []},
               {"id": "F_PR", "title": "_PENDING_REVIEW", "childIds": []}]

    summary = sync_folders(pg_conn, client=FakeWrikeClient(folders), space_id="ISPACE")

    assert (summary["inserted"], summary["skipped"]) == (2, 0)
    mb = sorted((r[3], r[1]) for r in _folder_rows(pg_conn) if r[0] == "MB")
    assert mb == [("MEAT BOARDS", "F_MB2"), ("MR BEAST", "F_MB1")]  # both stored, keyed by brand


def test_skips_exact_duplicate_prefix_and_brand(pg_conn):
    # Same prefix AND brand twice is a true duplicate folder -> keep the first only.
    folders = [{"id": "F_WP1", "title": "WP - Pooh", "childIds": []},
               {"id": "F_WP2", "title": "WP - Pooh", "childIds": []},  # exact dup
               {"id": "F_PR", "title": "_PENDING_REVIEW", "childIds": []}]

    summary = sync_folders(pg_conn, client=FakeWrikeClient(folders), space_id="ISPACE")

    assert (summary["inserted"], summary["skipped"]) == (1, 1)
    wp = [r for r in _folder_rows(pg_conn) if r[0] == "WP"]
    assert len(wp) == 1 and wp[0][1] == "F_WP1"  # the first one wins


def test_idempotent_across_runs(pg_conn):
    folders = [{"id": "F_WP", "title": "WP - Winnie-the-Pooh", "childIds": []},
               {"id": "F_PR", "title": "_PENDING_REVIEW", "childIds": []}]
    client = FakeWrikeClient(folders)

    first = sync_folders(pg_conn, client, space_id="ISPACE")
    rows_first = _folder_rows(pg_conn)
    second = sync_folders(pg_conn, client, space_id="ISPACE")

    assert first == second
    assert rows_first == _folder_rows(pg_conn)
