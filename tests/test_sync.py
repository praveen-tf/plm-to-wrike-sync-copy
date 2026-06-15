from datetime import datetime, timezone

from conftest import seed_folder_map, task_map_entry, unmapped_log
from fixtures_mapping import (
    CORE,
    HTML_CORE,
    INTL_CUSTOMER,
    LT_FOLDER,
    STAGING_FOLDER,
    WP_FOLDER,
    load_sample_items,
    plm_row,
    wrike_card,
)
from loader import load_plm_items
from state import get_task_map_entry_by_plm_id
from sync import load_sync_context, reconcile_folders, run_sync

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
T0 = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)


class FakeWrike:
    """In-memory stand-in for one Wrike identity (one API token)."""

    def __init__(self, token_ref, tasks=None):
        self.token_ref = token_ref
        self.created, self.updated, self.comments = [], [], []
        self.searches = []  # (field_id, value, folder_id) calls - id->id hits never search
        self.tasks = tasks if tasks is not None else {}  # shared account across identities
        self._n = 0

    def resolve_folder_id(self, folder_id):
        return folder_id  # test folder ids are already "v4" (no numeric permalink resolution)

    def resolve_folder_ids(self, folder_ids):
        return None  # test ids are already "v4"; nothing to batch-resolve (see WrikeClient)

    @staticmethod
    def _cf_list(cf):
        # Wrike stores/returns custom fields as [{"id", "value"}] with string values.
        return [{"id": fid, "value": "" if v is None else str(v)}
                for fid, v in (cf or {}).items()]

    @staticmethod
    def _cf_value(task, fid):
        for c in task.get("customFields") or []:
            if c["id"] == fid:
                return c.get("value")
        return None

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

    def find_tasks_by_custom_field(self, field_id, value, folder_id):
        self.searches.append((field_id, value, folder_id))
        return [{"id": tid, **task} for tid, task in self.tasks.items()
                if folder_id in (task.get("parentIds") or [])
                and self._cf_value(task, field_id) == value]

    def list_folder_tasks(self, folder_id):
        return [{"id": tid, **task} for tid, task in self.tasks.items()
                if folder_id in (task.get("parentIds") or [])]

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
    load_sample_items(conn, now=now)
    # '*' is the staging-fallback row: unmapped prefixes route there.
    seed_folder_map(conn, {"WP": WP_FOLDER, "LT": LT_FOLDER, "*": STAGING_FOLDER})


def test_first_run_creates_each_family_under_correct_author(pg_conn):
    _load(pg_conn)
    reg = Registry()
    summary = run_sync(pg_conn, reg, now=T0, since=EPOCH)
    assert summary["created"] == 11
    assert len(reg.clients["WRIKE_TOKEN_PRAVEEN"].created) == 6  # WP: BAKING + HOT DRINKS
    assert len(reg.clients["WRIKE_TOKEN_JESSE"].created) == 5    # LT: CONFECTION (Lindt)
    assert task_map_entry(pg_conn, "WP-71511")["wrike_task_id"].startswith("WRIKE_TOKEN_PRAVEEN")
    assert task_map_entry(pg_conn, "LT-68102")["wrike_task_id"].startswith("WRIKE_TOKEN_JESSE")


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


def test_mapped_record_updates_by_id_without_searching(pg_conn):
    # The 1:1 map is the primary path: once a record is mapped, future syncs go
    # straight plm_internal_id -> wrike_task_id with NO item-number search.
    _load(pg_conn)
    reg = Registry()
    run_sync(pg_conn, reg, now=T0, since=EPOCH)          # creates + populates the map
    for client in reg.clients.values():
        client.searches.clear()
    with pg_conn.cursor() as cur:
        cur.execute("UPDATE plm_item SET season = '2027 SPRING', modified_at = %s "
                    "WHERE family_id = 'WP-71511'", (T1,))
    summary = run_sync(pg_conn, reg, now=T1)
    assert summary["updated"] == 1
    assert all(not client.searches for client in reg.clients.values())


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


def test_lost_map_recovers_via_item_number_search_no_duplicate(pg_conn):
    _load(pg_conn)
    reg = Registry()
    run_sync(pg_conn, reg, now=T0, since=EPOCH)  # creates 11 cards + populates map
    assert sum(len(c.created) for c in reg.clients.values()) == 11
    # Simulate a lost map (e.g. wiped state): the cards still exist in Wrike and the
    # "PLM - Item #" + Customer on them give exactly one exact match per record.
    with pg_conn.cursor() as cur:
        cur.execute("TRUNCATE wrike_task_map, sync_watermark")
    summary = run_sync(pg_conn, reg, now=T0, since=EPOCH)
    assert summary["created"] == 0          # found by item-number search -> no duplicates
    assert summary["updated"] == 11
    assert summary["comments"] == 0          # adopted cards have no baseline -> no spurious comments
    assert sum(len(c.created) for c in reg.clients.values()) == 11  # still 11, none re-created
    assert task_map_entry(pg_conn, "WP-71511") is not None     # map repaired
    assert unmapped_log(pg_conn) == []                            # clean matches: nothing logged


def test_existing_exact_match_is_adopted_and_updated(pg_conn):
    # First sync against a folder that already holds the canonical card (hand-made
    # with a clean item# + customer): exactly one exact match -> update, not create.
    _load(pg_conn)
    reg = Registry()
    reg.tasks["hand:core"] = wrike_card("WP-71511-006-319", CORE, folder=WP_FOLDER)
    summary = run_sync(pg_conn, reg, now=T0, since=EPOCH)
    assert summary["created"] == 10 and summary["updated"] == 1
    assert task_map_entry(pg_conn, "WP-71511")["wrike_task_id"] == "hand:core"
    assert unmapped_log(pg_conn) == []


def test_exact_match_plus_variant_updates_exact_and_logs_extra(pg_conn):
    # The real MGF shape: a *CORE card AND a hand-made customer-variant card share
    # one item number. The exact match is updated; the variant is review-logged and
    # never touched.
    _load(pg_conn)
    reg = Registry()
    reg.tasks["hand:core"] = wrike_card("WP-71511-006-319", CORE, folder=WP_FOLDER)
    reg.tasks["hand:intl"] = wrike_card(
        "WP-71511-006-319", INTL_CUSTOMER, folder=WP_FOLDER,
        title="WP-71511 WINNIE-THE-POOH PANCAKE PAN ---INTL")
    summary = run_sync(pg_conn, reg, now=T0, since=EPOCH)
    assert summary["created"] == 10 and summary["updated"] == 1
    assert task_map_entry(pg_conn, "WP-71511")["wrike_task_id"] == "hand:core"
    pr = reg.clients["WRIKE_TOKEN_PRAVEEN"]
    assert all(tid != "hand:intl" for tid, _p in pr.updated)   # variant card untouched
    assert unmapped_log(pg_conn) == [
        ("WP-71511-006-319", INTL_CUSTOMER, "hand:intl", "WP", "non_identical_extra")]


def test_only_non_identical_match_is_logged_and_nothing_written(pg_conn):
    # Search returned cards but none matches item#+customer exactly: never guess -
    # log for human review, create/update nothing (creation only happens on zero hits).
    _load(pg_conn)
    reg = Registry()
    reg.tasks["hand:intl"] = wrike_card("WP-71511-006-319", INTL_CUSTOMER, folder=WP_FOLDER)
    summary = run_sync(pg_conn, reg, now=T0, since=EPOCH)
    assert summary["created"] == 10 and summary["logged"] == 1
    assert task_map_entry(pg_conn, "WP-71511") is None     # no map row written
    pr = reg.clients["WRIKE_TOKEN_PRAVEEN"]
    assert all(tid != "hand:intl" for tid, _p in pr.updated)
    assert unmapped_log(pg_conn) == [
        ("WP-71511-006-319", INTL_CUSTOMER, "hand:intl", "WP", "no_exact_match")]
    # The logged record is parked for a human; an unchanged rerun does not re-log it.
    summary = run_sync(pg_conn, reg, now=T0)
    assert len(unmapped_log(pg_conn)) == 1


def test_html_polluted_customer_fails_raw_match_and_is_logged(pg_conn):
    # Raw exact matching is deliberate: a card whose Customer holds a pasted HTML
    # anchor must fail the match and surface in the review log (data quality).
    _load(pg_conn)
    reg = Registry()
    reg.tasks["hand:html"] = wrike_card("WP-71511-006-319", HTML_CORE, folder=WP_FOLDER)
    summary = run_sync(pg_conn, reg, now=T0, since=EPOCH)
    assert summary["created"] == 10 and summary["logged"] == 1
    assert unmapped_log(pg_conn) == [
        ("WP-71511-006-319", HTML_CORE, "hand:html", "WP", "no_exact_match")]


def test_two_identical_cards_are_ambiguous_and_logged(pg_conn):
    # Two cards with the same item# AND customer: ambiguous - never auto-pick one.
    _load(pg_conn)
    reg = Registry()
    reg.tasks["hand:a"] = wrike_card("WP-71511-006-319", CORE, folder=WP_FOLDER)
    reg.tasks["hand:b"] = wrike_card("WP-71511-006-319", CORE, folder=WP_FOLDER)
    summary = run_sync(pg_conn, reg, now=T0, since=EPOCH)
    assert summary["created"] == 10 and summary["logged"] == 1
    assert task_map_entry(pg_conn, "WP-71511") is None
    assert {(r[2], r[4]) for r in unmapped_log(pg_conn)} == {
        ("hand:a", "multiple_exact_matches"), ("hand:b", "multiple_exact_matches")}


def test_search_ignores_same_item_number_card_in_unmanaged_folder(pg_conn):
    # The item-number search is scoped to the prefix's mapped folder: an identical
    # card sitting in an unmanaged folder is invisible -> a fresh card is created.
    _load(pg_conn)
    reg = Registry()
    reg.tasks["decoy:1"] = wrike_card("WP-71511-006-319", CORE, folder="fold-OUT-OF-SCOPE")
    summary = run_sync(pg_conn, reg, now=T0, since=EPOCH)
    assert summary["created"] == 11                                  # WP-71511 created fresh
    assert task_map_entry(pg_conn, "WP-71511")["wrike_task_id"] != "decoy:1"


def test_update_refused_when_mapped_card_is_outside_managed_folders(pg_conn):
    # Safety guard: a mapped card living outside the wrike_folder_map/staging folders
    # (e.g. moved into a real production space) must NOT be updated.
    _load(pg_conn)
    reg = Registry()
    run_sync(pg_conn, reg, now=T0, since=EPOCH)              # create everything in-scope
    tid = task_map_entry(pg_conn, "WP-71511")["wrike_task_id"]
    reg.tasks[tid]["parentIds"] = ["fold-OUT-OF-SCOPE"]      # relocate to an unmanaged folder
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
    assert len(rows) == 1 and "wrike_folder_map" in rows[0][0]  # dead-lettered with a clear reason


def test_unmapped_prefix_creates_in_staging_and_logs(pg_conn):
    # A prefix with no wrike_folder_map row: the card is still created - in the
    # staging folder - and the missed prefix is review-logged. A human adds the
    # folder row later (after client approval); the table itself is not touched.
    _load(pg_conn)
    load_plm_items(pg_conn, [plm_row("99", "ZZ-90001-006-319")])
    reg = Registry()
    summary = run_sync(pg_conn, reg, now=T0, since=EPOCH)
    assert summary["created"] == 12
    zz_entry = get_task_map_entry_by_plm_id(pg_conn, "99")
    assert zz_entry["wrike_task_id"] in reg.tasks
    assert reg.tasks[zz_entry["wrike_task_id"]]["parentIds"] == [STAGING_FOLDER]
    log = unmapped_log(pg_conn)
    assert [(r[0], r[3], r[4]) for r in log] == [("ZZ-90001-006-319", "ZZ", "unmapped_prefix")]
    with pg_conn.cursor() as cur:                       # folder table NOT self-populated
        cur.execute("SELECT count(*) FROM wrike_folder_map WHERE prefix = 'ZZ'")
        assert cur.fetchone()[0] == 0


def test_unmapped_prefix_without_staging_defers(pg_conn):
    # Safe degradation: no folder row AND no '*' staging row in the table -> defer.
    _load(pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM wrike_folder_map WHERE prefix = '*'")
    load_plm_items(pg_conn, [plm_row("99", "ZZ-90001-006-319")])
    reg = Registry()
    summary = run_sync(pg_conn, reg, now=T0, since=EPOCH)
    assert summary["created"] == 11 and summary["deferred"] == 1
    assert get_task_map_entry_by_plm_id(pg_conn, "99") is None


def test_reconcile_logs_unmapped_cards_including_hand_made(pg_conn):
    # The full-folder walk: every card that doesn't map to a plm_item row by
    # (item_number + raw customer) is review-logged - the Excel export source.
    _load(pg_conn)
    reg = Registry()
    reg.tasks["hand:ok"] = wrike_card("WP-71511-006-319", CORE, folder=WP_FOLDER)
    reg.tasks["hand:intl"] = wrike_card("WP-71511-006-319", INTL_CUSTOMER, folder=WP_FOLDER)
    reg.tasks["hand:blank"] = wrike_card("", "WALMART", folder=LT_FOLDER,
                                         title="LT-99999 HAND MADE")
    summary = reconcile_folders(pg_conn, reg, load_sync_context(pg_conn))
    assert summary == {"cards": 3, "mapped": 1, "logged": 2}
    assert {(r[2], r[4]) for r in unmapped_log(pg_conn)} == {
        ("hand:intl", "no_plm_match"), ("hand:blank", "no_plm_match")}


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
