"""SourceStore invariants: server-chosen keys, owner prefixes, real byte checks."""

from __future__ import annotations

import pytest
from metis_contracts import ErrorCode, MetisError, ObjectRef
from metis_state.object_store import LocalSourceStore, build_key, owner_prefix


@pytest.fixture()
def store(tmp_path) -> LocalSourceStore:
    return LocalSourceStore(str(tmp_path / "objects"))


def test_keys_are_namespaced_per_owner_and_do_not_leak_the_subject(store):
    key = build_key("user_a", "src_1", ".webm")
    assert key.startswith("users/")
    assert "user_a" not in key, "a raw subject must not appear in an object path"


def test_owner_prefixes_differ_between_users():
    assert owner_prefix("user_a") != owner_prefix("user_b")


def test_a_key_outside_the_owner_prefix_is_rejected(store):
    mine = build_key("user_a", "src_1", ".webm")
    theirs = build_key("user_b", "src_1", ".webm")
    assert store.owns_key("user_a", mine) is True
    assert store.owns_key("user_a", theirs) is False


def test_upload_target_picks_the_key_and_carries_the_byte_limit(store):
    ref, instructions = store.upload_target(
        "user_a", capture_id="src_1", content_type="audio/webm", max_bytes=1024, ttl_seconds=60
    )
    assert ref.key.endswith("src_1.webm")
    assert instructions["max_bytes"] == 1024
    assert instructions["object_key"] == ref.key


def test_head_reports_actual_stored_bytes_not_a_claim(store):
    ref = ObjectRef(bucket="local", key=build_key("user_a", "src_1", ".webm"), size_bytes=999_999)
    store.put_bytes(ref, b"four", content_type="audio/webm")
    stored = store.head(ref)
    assert stored is not None and stored.size_bytes == 4


def test_head_of_a_missing_object_is_none(store):
    ref = ObjectRef(bucket="local", key=build_key("user_a", "nope", ".webm"))
    assert store.head(ref) is None


def test_reading_a_missing_object_is_object_missing(store):
    ref = ObjectRef(bucket="local", key=build_key("user_a", "nope", ".webm"))
    with pytest.raises(MetisError) as exc:
        store.get_bytes(ref)
    assert exc.value.code is ErrorCode.OBJECT_MISSING


def test_a_traversal_key_is_forbidden(store):
    with pytest.raises(MetisError) as exc:
        store.get_bytes(ObjectRef(bucket="local", key="../../etc/passwd"))
    assert exc.value.code is ErrorCode.FORBIDDEN


def test_delete_prefix_removes_only_that_owners_objects(store):
    a = ObjectRef(bucket="local", key=build_key("user_a", "src_1", ".webm"))
    b = ObjectRef(bucket="local", key=build_key("user_b", "src_2", ".webm"))
    store.put_bytes(a, b"a", content_type="audio/webm")
    store.put_bytes(b, b"b", content_type="audio/webm")
    removed = store.delete_prefix(owner_prefix("user_a"))
    assert removed == 1
    assert store.head(a) is None
    assert store.head(b) is not None
