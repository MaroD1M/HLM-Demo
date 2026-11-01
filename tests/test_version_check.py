from app import _is_remote_newer


def test_remote_newer_is_detected():
    assert _is_remote_newer('v0.5.0', 'v0.5.1') is True
    assert _is_remote_newer('v0.5.0', 'v0.6.0') is True
    assert _is_remote_newer('v0.5.0', 'v1.0.0') is True


def test_same_or_older_remote_is_not_an_update():
    assert _is_remote_newer('v0.5.0', 'v0.5.0') is False
    assert _is_remote_newer('v0.5.0', 'v0.4.0') is False
    assert _is_remote_newer('v0.5.0', '-') is False
