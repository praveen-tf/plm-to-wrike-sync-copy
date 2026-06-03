from description import merge_description

# Real shape from the loaded data: headers <h5>Label:     </h5> with <br> separators.
EXISTING = (
    "<h5>Design Brief:     </h5>REF WP-68151<br><br>"
    "<h5>Material Codes:     </h5>OLD MAT<br>"
    "<h5>Contents:     </h5>OLD CONTENTS"
)


def test_merge_replaces_material_codes():
    out = merge_description(EXISTING, material_codes="NEW MAT")
    assert "Material Codes:     </h5>NEW MAT" in out
    assert "OLD MAT" not in out


def test_merge_replaces_contents():
    out = merge_description(EXISTING, contents="NEW CONTENTS")
    assert "Contents:     </h5>NEW CONTENTS" in out
    assert "OLD CONTENTS" not in out


def test_merge_preserves_design_brief_and_manual_notes():
    existing = EXISTING + "<br><h5>Notes:     </h5>hand typed note"
    out = merge_description(existing, material_codes="NEW MAT", contents="NEW CONTENTS")
    assert "Design Brief:     </h5>REF WP-68151" in out  # untouched section
    assert "Notes:     </h5>hand typed note" in out  # manual note preserved
    assert out.index("Material Codes") < out.index("Contents")  # order preserved


def test_merge_without_target_header_returns_unchanged():
    plain = "<p>free text, no sections</p>"
    assert merge_description(plain, material_codes="X", contents="Y") == plain


def test_merge_does_not_add_sync_marker_to_description():
    out = merge_description(EXISTING, material_codes="NEW MAT", contents="NEW CONTENTS")
    assert "updated by sync" not in out  # audit timestamp lives in the comment, not here
