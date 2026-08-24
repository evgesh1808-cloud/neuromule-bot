"""Last animate request cache for regenerate button."""

from services.last_animate_request import clear, get, remember


def test_last_animate_request_remember_and_get() -> None:
    uid = 88002
    clear(uid)
    remember(uid, source_file_id="AgAC_src", motion_prompt=None)
    entry = get(uid)
    assert entry is not None
    assert entry.source_file_id == "AgAC_src"
    clear(uid)
    assert get(uid) is None
