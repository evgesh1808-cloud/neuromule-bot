from platforms.build_info import get_build_info_text, is_gate_bypass_command, slash_command_base


def test_slash_command_base() -> None:
    assert slash_command_base("/version") == "/version"
    assert slash_command_base("/version@NeuroMuleBot") == "/version"
    assert slash_command_base("hello") is None


def test_gate_bypass_includes_version() -> None:
    assert is_gate_bypass_command("/start")
    assert is_gate_bypass_command("/version")
    assert not is_gate_bypass_command("/help")


def test_build_info_text_contains_rev() -> None:
    text = get_build_info_text()
    assert "NeuroMule" in text
    assert "<code>" in text
    assert "CFO" in text
    assert "Fact-Based Audit Build" in text
