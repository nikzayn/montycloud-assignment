"""API 2: listing images with filters (user_id, tag) and pagination."""
from image_service.repository import _encode_token


def ids(response):
    return [item["image_id"] for item in response.body["items"]]


def collect_all_pages(api, **query):
    """Follow next_token until the end; return every image_id seen, in order."""
    seen, token = [], None
    while True:
        params = dict(query, **({"next_token": token} if token else {}))
        response = api.list(**params)
        assert response.status == 200, response.body
        seen += ids(response)
        token = response.body["next_token"]
        if not token:
            return seen


def test_empty_list(api):
    response = api.list()
    assert response.status == 200
    assert response.body == {"items": [], "count": 0, "next_token": None}


def test_lists_active_images_newest_first_with_download_urls(api):
    first = api.upload(title="first")
    second = api.upload(title="second")

    response = api.list()

    assert ids(response) == [second, first]
    assert response.body["count"] == 2
    assert all("download_url" in item for item in response.body["items"])


def test_pending_and_rejected_images_are_not_listed(api):
    active = api.upload()
    api.create()                                                 # PENDING forever
    rejected = api.create().body
    api.simulate_s3_upload(rejected["upload"]["fields"]["key"], b"not an image")

    assert ids(api.list()) == [active]
    assert ids(api.list(user_id="alice")) == [active]


def test_filter_by_user(api):
    alice_1 = api.upload(user="alice")
    bob_1 = api.upload(user="bob")
    alice_2 = api.upload(user="alice")

    assert ids(api.list(user_id="alice")) == [alice_2, alice_1]
    assert ids(api.list(user_id="bob")) == [bob_1]
    assert ids(api.list(user_id="carol")) == []


def test_filter_by_tag_is_case_insensitive(api):
    beach = api.upload(tags=["beach", "goa"])
    api.upload(tags=["mountains"])

    assert ids(api.list(tag="beach")) == [beach]
    assert ids(api.list(tag="#Beach")) == [beach]
    assert ids(api.list(tag="desert")) == []


def test_filters_can_be_combined(api):
    api.upload(user="alice", tags=["food"])
    alice_beach = api.upload(user="alice", tags=["beach"])
    api.upload(user="bob", tags=["beach"])

    assert ids(api.list(user_id="alice", tag="beach")) == [alice_beach]


def test_pagination_returns_every_image_exactly_once(api):
    uploaded = [api.upload(title=f"img {i}") for i in range(7)]

    first_page = api.list(limit=3)
    assert first_page.body["count"] == 3
    assert first_page.body["next_token"]

    assert collect_all_pages(api, limit=3) == list(reversed(uploaded))


def test_pagination_fills_pages_even_with_a_selective_filter(api):
    uploaded = [api.upload(tags=["rare"] if i in (1, 4, 5) else ["common"]) for i in range(8)]
    rare = [uploaded[5], uploaded[4], uploaded[1]]  # newest first

    page = api.list(tag="rare", limit=2)
    assert ids(page) == rare[:2]  # a full page, despite non-matching items in between

    assert collect_all_pages(api, tag="rare", limit=2) == rare


def test_pagination_with_user_filter(api):
    mine = [api.upload(user="alice") for _ in range(5)]
    api.upload(user="bob")

    assert collect_all_pages(api, user_id="alice", limit=2) == list(reversed(mine))


def test_invalid_limit_is_rejected(api):
    for bad in ("0", "101", "abc"):
        response = api.list(limit=bad)
        assert response.status == 400
        assert response.body["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_tag_or_user_filter_is_rejected(api):
    assert api.list(tag="no spaces allowed").status == 400
    assert api.list(user_id="bad user").status == 400


def test_garbage_next_token_is_rejected(api):
    response = api.list(next_token="%%%not-a-token%%%")
    assert response.status == 400
    assert "next_token" in response.body["error"]["message"]


def test_token_from_a_different_query_is_rejected(api):
    user_query_token = _encode_token({"image_id": "x", "user_id": "alice", "created_at": "2026"})
    assert api.list(next_token=user_query_token).status == 400        # used without user_id filter
    assert api.list(next_token=_encode_token(["not", "a", "dict"])).status == 400
