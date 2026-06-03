from datetime import datetime, timezone
from pathlib import Path

import pytest

from loader import load_plm_items, read_source_rows, to_plm_item
from state import get_task_map_entry
from sync import run_sync

DATA = Path(__file__).resolve().parent.parent / "data" / "input"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
T0 = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _folder_settings(monkeypatch):
    # Prefix -> folder id mapping is now static config (WRIKE_FOLDER_*), not discovery.
    monkeypatch.setenv("WRIKE_FOLDER_WP", "fold-wp")
    monkeypatch.setenv("WRIKE_FOLDER_AW", "fold-aw")
    monkeypatch.setenv("WRIKE_FOLDER_LT", "fold-lt")


class FakeWrike:
    """In-memory stand-in for one Wrike identity (one API token)."""

    def __init__(self, token_ref, tasks=None):
        self.token_ref = token_ref
        self.created, self.updated, self.comments = [], [], []
        self.tasks = tasks if tasks is not None else {}  # shared account across identities
        self._n = 0

    def resolve_folder_id(self, folder_id):
        return folder_id  # test folder ids are already "v4" (no numeric permalink resolution)

    @staticmethod
    def _cf_list(cf):
        # Wrike stores/returns custom fields as [{"id", "value"}] with string values.
        return [{"id": fid, "value": "" if v is None else str(v)}
                for fid, v in (cf or {}).items()]

    def create_task(self, folder_id, payload):
        self._n += 1
        tid = f"{self.token_ref}:{self._n}"
        self.created.append((folder_id, payload))
        self.tasks[tid] = {
            "title": payload.get("title"),
            "description": payload.get("description", ""),
            "parentIds": [folder_id],
            "customFields": self._cf_list(payload.get("customFields")),
        }
        return {"id": tid, "permalink": f"http://wrike/{tid}"}

    def find_task_by_title_prefix(self, prefix, folder_ids):
        allowed = set(folder_ids)
        for tid, task in self.tasks.items():
            if not allowed.intersection(task.get("parentIds") or []):
                continue  # only adopt cards inside the configured folders
            if (task.get("title") or "").startswith(prefix):
                return tid
        return None

    def get_task(self, task_id):
        return self.tasks.get(task_id, {"description": "", "parentIds": []})

    def update_task(self, task_id, payload):
        self.updated.append((task_id, payload))
        task = self.tasks.setdefault(task_id, {})
        if "description" in payload:
            task["description"] = payload["description"]
        if payload.get("customFields"):  # merge changed fields into the live card
            by_id = {c["id"]: c for c in task.setdefault("customFields", [])}
            for fid, val in payload["customFields"].items():
                sval = "" if val is None else str(val)
                if fid in by_id:
                    by_id[fid]["value"] = sval
                else:
                    task["customFields"].append({"id": fid, "value": sval})
        return {"id": task_id}

    def add_comment(self, task_id, text):
        self.comments.append((task_id, text))
        return {"id": "c"}


class Registry:
    """make_client(token_ref) factory that retains one client per identity."""

    def __init__(self):
        self.clients = {}
        self.tasks = {}  # shared account store, seen by every identity's client

    def _make(self, token_ref):
        return FakeWrike(token_ref, self.tasks)

    def __call__(self, token_ref):
        return self.clients.setdefault(token_ref, self._make(token_ref))


class FlakyWrike(FakeWrike):
    """Fails create for any family whose title starts with `fail_prefix`."""

    def __init__(self, token_ref, fail_prefix, tasks=None):
        super().__init__(token_ref, tasks)
        self.fail_prefix = fail_prefix

    def create_task(self, folder_id, payload):
        if payload["title"].startswith(self.fail_prefix):
            raise RuntimeError("boom")
        return super().create_task(folder_id, payload)


class FlakyRegistry(Registry):
    def __init__(self, fail_prefix):
        super().__init__()
        self.fail_prefix = fail_prefix

    def _make(self, token_ref):
        return FlakyWrike(token_ref, self.fail_prefix, self.tasks)


def _load(conn, now=T0):
    rows = read_source_rows(DATA / "winnie_the_pooh_input.xlsx",
                            DATA / "synthetic_jesse_confection.csv")
    load_plm_items(conn, [to_plm_item(r, now=now) for r in rows])


def test_first_run_creates_each_family_under_correct_author(pg_conn):
    _load(pg_conn)
    reg = Registry()
    summary = run_sync(pg_conn, reg, now=T0, since=EPOCH)
    assert summary["created"] == 11
    assert len(reg.clients["WRIKE_TOKEN_PRAVEEN"].created) == 6  # WP: BAKING + HOT DRINKS
    assert len(reg.clients["WRIKE_TOKEN_JESSE"].created) == 5    # LT: CONFECTION (Lindt)
    assert get_task_map_entry(pg_conn, "WP-71511")["wrike_task_id"].startswith("WRIKE_TOKEN_PRAVEEN")
    assert get_task_map_entry(pg_conn, "LT-68102")["wrike_task_id"].startswith("WRIKE_TOKEN_JESSE")


def test_new_cards_stamped_with_retail_item_type(pg_conn, monkeypatch):
    # PRD: every new Wrike card is created with Item Type = "Retail Item".
    monkeypatch.setenv("WRIKE_RETAIL_ITEM_TYPE_ID", "ITYPE_RETAIL")
    _load(pg_conn)
    reg = Registry()
    run_sync(pg_conn, reg, now=T0, since=EPOCH)
    created = (reg.clients["WRIKE_TOKEN_PRAVEEN"].created
               + reg.clients["WRIKE_TOKEN_JESSE"].created)
    assert created and all(p["customItemTypeId"] == "ITYPE_RETAIL" for _folder, p in created)


def test_updates_do_not_carry_item_type(pg_conn, monkeypatch):
    monkeypatch.setenv("WRIKE_RETAIL_ITEM_TYPE_ID", "ITYPE_RETAIL")
    _load(pg_conn)
    reg = Registry()
    run_sync(pg_conn, reg, now=T0, since=EPOCH)          # create
    with pg_conn.cursor() as cur:                        # a real field change so updates actually fire
        cur.execute("UPDATE plm_item SET season = season || ' R2', modified_at = %s", (T1,))
    run_sync(pg_conn, reg, now=T1, since=EPOCH)          # -> updates
    updated = (reg.clients["WRIKE_TOKEN_PRAVEEN"].updated
               + reg.clients["WRIKE_TOKEN_JESSE"].updated)
    assert updated and all("customItemTypeId" not in p for _tid, p in updated)


def test_second_run_without_changes_is_noop(pg_conn):
    _load(pg_conn)
    reg = Registry()
    run_sync(pg_conn, reg, now=T0, since=EPOCH)
    summary = run_sync(pg_conn, reg, now=T0)  # watermark from prior run -> nothing newer
    assert summary["created"] == 0 and summary["updated"] == 0


def test_material_change_updates_merges_description_and_comments(pg_conn):
    _load(pg_conn)
    reg = Registry()
    run_sync(pg_conn, reg, now=T0, since=EPOCH)
    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE plm_item SET material_codes = 'NEW MAT CODE', modified_at = %s "
            "WHERE family_id = 'WP-71511'", (T1,))
    summary = run_sync(pg_conn, reg, now=T1)
    assert summary["updated"] == 1
    pr = reg.clients["WRIKE_TOKEN_PRAVEEN"]
    task_id, payload = pr.updated[-1]
    assert "NEW MAT CODE" in payload["description"]          # merged into existing description
    assert "title" not in payload                            # create-only field not written
    assert any("Material Codes" in c[1] for c in pr.comments)  # change comment posted


def test_title_change_alerts_without_writing_title(pg_conn):
    _load(pg_conn)
    reg = Registry()
    run_sync(pg_conn, reg, now=T0, since=EPOCH)
    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE plm_item SET title = 'CHANGED TITLE', modified_at = %s "
            "WHERE family_id = 'WP-71511'", (T1,))
    summary = run_sync(pg_conn, reg, now=T1)
    pr = reg.clients["WRIKE_TOKEN_PRAVEEN"]
    # Title is alert-only and no written field actually changed -> nothing is PUT...
    assert summary["updated"] == 0 and summary["unchanged"] == 1
    assert all("title" not in p for _tid, p in pr.updated)
    # ...but the alert comment is still posted off the snapshot diff.
    assert summary["alerts"] == 1
    assert any("Title" in c[1] for c in pr.comments)


def test_rerun_with_unchanged_fields_writes_nothing(pg_conn):
    # Client requirement: incoming PLM values are validated against the live card and
    # an unchanged family is left untouched (no redundant PUT, no version-history noise).
    _load(pg_conn)
    reg = Registry()
    run_sync(pg_conn, reg, now=T0, since=EPOCH)             # create everything
    summary = run_sync(pg_conn, reg, now=T0, since=EPOCH)   # re-evaluate the same data
    assert summary["created"] == 0
    assert summary["updated"] == 0          # every field already matches the card
    assert summary["unchanged"] == 11
    assert all(not c.updated for c in reg.clients.values())  # no PUT issued at all


def test_update_pushes_only_the_changed_field(pg_conn):
    # Only the field whose incoming value differs from the live card is sent, even for a
    # field the snapshot diff does not track (season). The rest are dropped.
    from mapping import CUSTOM_FIELDS
    _load(pg_conn)
    reg = Registry()
    run_sync(pg_conn, reg, now=T0, since=EPOCH)
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE plm_item SET season = '2027 SPRING', modified_at = %s "
                    "WHERE family_id = 'WP-71511'", (T1,))
    summary = run_sync(pg_conn, reg, now=T1)
    pr = reg.clients["WRIKE_TOKEN_PRAVEEN"]
    _tid, payload = pr.updated[-1]
    season_fid = CUSTOM_FIELDS["season"][0]
    assert set(payload["customFields"]) == {season_fid}      # only season pushed, nothing else
    assert payload["customFields"][season_fid] == "2027 SPRING"
    assert summary["updated"] == 1


def test_lost_map_finds_existing_via_search_no_duplicate(pg_conn):
    _load(pg_conn)
    reg = Registry()
    run_sync(pg_conn, reg, now=T0, since=EPOCH)  # creates 9 cards + populates map
    created_after_first = sum(len(c.created) for c in reg.clients.values())
    assert created_after_first == 11
    # Simulate a lost map (e.g. wiped state): the cards still exist in Wrike.
    with pg_conn.cursor() as cur:
        cur.execute("TRUNCATE wrike_task_map, sync_watermark")
    summary = run_sync(pg_conn, reg, now=T0, since=EPOCH)
    assert summary["created"] == 0          # found by title search -> no duplicates
    assert summary["updated"] == 11
    assert summary["comments"] == 0          # adopted cards have no baseline -> no spurious comments
    assert sum(len(c.created) for c in reg.clients.values()) == 11  # still 11, none re-created
    assert get_task_map_entry(pg_conn, "WP-71511") is not None     # map repaired


def test_update_refused_when_mapped_card_is_outside_configured_folders(pg_conn):
    # Safety guard: a mapped/adopted card living outside the WRIKE_FOLDER_* folders
    # (e.g. a real production card created by an earlier run) must NOT be updated.
    _load(pg_conn)
    reg = Registry()
    run_sync(pg_conn, reg, now=T0, since=EPOCH)              # create everything in-scope
    tid = get_task_map_entry(pg_conn, "WP-71511")["wrike_task_id"]
    reg.tasks[tid]["parentIds"] = ["fold-OUT-OF-SCOPE"]      # relocate to an unconfigured folder
    with pg_conn.cursor() as cur:                            # make WP-71511 eligible for update
        cur.execute("UPDATE plm_item SET material_codes='NEW MAT', modified_at=%s "
                    "WHERE family_id='WP-71511'", (T1,))
    summary = run_sync(pg_conn, reg, now=T1)
    pr = reg.clients["WRIKE_TOKEN_PRAVEEN"]
    assert all(t_id != tid for t_id, _ in pr.updated)        # the out-of-scope card was NOT updated
    assert not any(c[0] == tid for c in pr.comments)         # and no comment posted to it
    assert summary["updated"] == 0 and summary["failed"] == 1
    with pg_conn.cursor() as cur:
        cur.execute("SELECT reason FROM sync_dlq WHERE family_id = 'WP-71511'")
        rows = cur.fetchall()
    assert len(rows) == 1 and "WRIKE_FOLDER" in rows[0][0]    # dead-lettered with a clear reason


def test_search_does_not_adopt_a_same_title_card_outside_configured_folders(pg_conn):
    # On a map miss, a matching-title card in an UNCONFIGURED folder must be ignored;
    # a fresh card is created in the configured folder instead of adopting the decoy.
    _load(pg_conn)
    reg = Registry()
    reg.tasks["decoy:1"] = {"title": "WP-71511 WINNIE-THE-POOH PANCAKE PAN",
                            "description": "", "parentIds": ["fold-OUT-OF-SCOPE"]}
    summary = run_sync(pg_conn, reg, now=T0, since=EPOCH)
    assert summary["created"] == 11                                  # WP-71511 created fresh
    assert get_task_map_entry(pg_conn, "WP-71511")["wrike_task_id"] != "decoy:1"  # decoy ignored


def test_new_item_with_no_matching_folder_is_queued_not_created(pg_conn, monkeypatch):
    # No WRIKE_FOLDER_* configured for any prefix -> every family defers.
    monkeypatch.delenv("WRIKE_FOLDER_WP", raising=False)
    monkeypatch.delenv("WRIKE_FOLDER_AW", raising=False)
    monkeypatch.delenv("WRIKE_FOLDER_LT", raising=False)
    _load(pg_conn)
    reg = Registry()
    summary = run_sync(pg_conn, reg, now=T0, since=EPOCH)
    assert summary["created"] == 0
    assert summary["deferred"] == 11
    assert sum(len(c.created) for c in reg.clients.values()) == 0  # nothing created
    from state import get_watermark
    assert get_watermark(pg_conn) == EPOCH  # held back -> retried next sync
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM sync_dlq")
        assert cur.fetchone()[0] == 0  # deferred, not dead-lettered


def test_failing_family_is_dead_lettered_and_others_proceed(pg_conn):
    _load(pg_conn)
    reg = FlakyRegistry(fail_prefix="WP-71512")  # one HOT DRINKS family fails to create
    summary = run_sync(pg_conn, reg, now=T0, since=EPOCH)
    assert summary["created"] == 10
    assert summary["failed"] == 1
    with pg_conn.cursor() as cur:
        cur.execute("SELECT family_id FROM sync_dlq")
        assert ("WP-71512",) in cur.fetchall()


def test_watermark_not_advanced_past_a_failure(pg_conn):
    # All rows share modified_at; if one fails, the watermark must not skip it.
    _load(pg_conn)
    reg = FlakyRegistry(fail_prefix="WP-71512")
    run_sync(pg_conn, reg, now=T0, since=EPOCH)
    from state import get_watermark
    assert get_watermark(pg_conn) == EPOCH  # held back so the failed row is retried next run
