"""Dictation settings ride on Preferences: same revision rules, no new route."""

from __future__ import annotations


def test_flow_settings_roundtrip(client) -> None:
    current = client.get("/v1/preferences").json()
    assert current["flow"]["dictionary"] == [] and current["flow"]["keep_history"] is False

    flow = {
        "dictionary": [{"term": "Cerno", "aliases": ["sirno"]}],
        "snippets": [{"trigger": "closing remark", "expansion": "Best regards"}],
        "app_styles": {"slack": "casual"},
    }
    r = client.patch("/v1/preferences", json={"expected_revision": current["revision"], "flow": flow})
    assert r.status_code == 200, r.text
    stored = client.get("/v1/preferences").json()["flow"]
    assert stored["dictionary"][0]["term"] == "Cerno"
    assert stored["app_styles"] == {"slack": "casual"}

    rev = r.json()["revision"]
    assert (
        client.patch(
            "/v1/preferences", json={"expected_revision": rev, "flow": {"keep_history": True}}
        ).status_code
        == 200
    )
    stale = client.patch("/v1/preferences", json={"expected_revision": rev, "flow": flow})
    assert stale.status_code == 409
    bad = client.patch(
        "/v1/preferences",
        json={
            "expected_revision": rev + 1,
            "flow": {"snippets": [{"trigger": "x", "expansion": ""}]},
        },
    )
    assert bad.status_code == 422
