from services.replicate_client import extract_output_url


def test_extract_output_url_string():
    assert extract_output_url("https://replicate.delivery/out.mp4") == "https://replicate.delivery/out.mp4"


def test_extract_output_url_list():
    assert extract_output_url(["https://a.com/v.mp4"]) == "https://a.com/v.mp4"


def test_suno_parse_track_list():
    from services.suno_client import _parse_track_payload

    data = [{"audio_url": "https://cdn.example/track.mp3", "title": "Космос"}]
    assert _parse_track_payload(data) == ("https://cdn.example/track.mp3", "Космос")
