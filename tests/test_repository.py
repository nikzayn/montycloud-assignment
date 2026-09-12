"""Edge cases of the DynamoDB layer that are hard to reach through the API."""
from image_service import repository


def test_status_update_on_a_vanished_record_reports_false(api):
    repo = api.service._repo
    assert repo.mark_active("does-not-exist", size_bytes=1, uploaded_at="now") is False
    assert repo.mark_rejected("does-not-exist", reason="x", expires_at=1) is False


def test_read_budget_caps_work_per_request_and_returns_a_resume_token(api, monkeypatch):
    for i in range(6):
        api.upload(tags=["rare"] if i == 0 else ["common"])    # the only match is the oldest
    monkeypatch.setattr(repository, "MAX_READS_PER_PAGE", 1)

    page = api.list(tag="rare", limit=2)                       # one read of 2 items: no match yet

    assert page.status == 200
    assert page.body["items"] == []
    assert page.body["next_token"]                             # ...but the client can continue
