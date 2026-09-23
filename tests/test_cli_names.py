import contextlib, io
import pytest
from torque import cli


@pytest.mark.parametrize("route", sorted(cli.PUBLIC_ROUTES))
def test_public_route_help_names_torque(route):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
        cli.main([route, "--help"])
    text = out.getvalue()
    assert text.startswith(f"usage: torque {route}")
    assert "jsc" not in text.lower()


@pytest.mark.parametrize("route", ["qa", "logs", "probes", "advisory", "meeting", "lesson"])
def test_delegate_help_names_torque(route):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
        cli.main([route, "--help"])
    assert out.getvalue().startswith(f"usage: torque {route}")
