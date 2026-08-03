"""Unit tests for the write-verified retry logic added to work around the
DHO4204 silently dropping SCPI writes issued while it's still busy settling
from a previous operation (confirmed on hardware — see git history)."""

from unittest.mock import MagicMock, patch

import pytest

from rigol_dho4204 import DHO4204, _numeric_match


def make_scope():
    """Return a DHO4204 with the VISA layer mocked out."""
    with patch("rigol_dho4204.pyvisa.ResourceManager") as mock_rm:
        mock_resource = MagicMock()
        mock_resource.query.return_value = "RIGOL TECHNOLOGIES,DHO4204,SN123,00.02.13"
        mock_rm.return_value.open_resource.return_value = mock_resource
        scope = DHO4204("USB0::0x1AB1::0x0610::TEST::INSTR")
    return scope, mock_resource


def test_write_verified_retries_until_confirmed():
    scope, resource = make_scope()
    resource.query.side_effect = ["AUTO", "SING"]

    confirmed = scope.write_verified(
        ":TRIGger:SWEep SINGle", ":TRIGger:SWEep?", "SINGLE", timeout=5, poll_interval=0
    )

    assert confirmed is True
    assert resource.write.call_count == 2


def test_write_verified_times_out_when_never_confirmed():
    scope, resource = make_scope()
    resource.query.return_value = "AUTO"

    confirmed = scope.write_verified(
        ":TRIGger:SWEep SINGle", ":TRIGger:SWEep?", "SINGLE", timeout=0.05, poll_interval=0.01
    )

    assert confirmed is False


def test_single_trigger_with_verify_confirms_sweep_mode():
    scope, resource = make_scope()
    resource.query.side_effect = ["SING"]

    confirmed = scope.single_trigger_with_verify(timeout=1, poll_interval=0)

    assert confirmed is True
    resource.write.assert_any_call(":TRIGger:SWEep SINGle")


def test_wait_for_trigger_stop_returns_once_stopped():
    scope, resource = make_scope()
    resource.query.side_effect = ["RUN", "STOP"]

    scope.wait_for_trigger_stop(timeout=5, poll_interval=0)  # should not raise


def test_wait_for_trigger_stop_restarts_and_raises_on_timeout():
    scope, resource = make_scope()
    resource.query.return_value = "RUN"

    with pytest.raises(TimeoutError):
        scope.wait_for_trigger_stop(timeout=0.05, poll_interval=0.01)

    resource.write.assert_any_call(":SYSTem:RESet")


@pytest.mark.parametrize(
    "readback, expected",
    [("1000", True), ("1000.0", True), ("999", False), ("garbage", False)],
)
def test_numeric_match_tolerance(readback, expected):
    match = _numeric_match(1000, tol=0.5)
    assert match(readback) is expected
