"""Tests for duration-driven capture(): the point-count -> hardware memory
depth snapping, which is the only non-trivial new logic (everything else in
capture() is orchestration over already-tested methods)."""

from unittest.mock import MagicMock, patch

import numpy as np

from rigol_dho4204 import DHO4204, _nearest_memory_depth


def make_scope():
    """A DHO4204 with the VISA layer mocked out (mirrors test_write_verified)."""
    with patch("rigol_dho4204.pyvisa.ResourceManager") as mock_rm:
        mock_resource = MagicMock()
        mock_resource.query.return_value = "RIGOL TECHNOLOGIES,DHO4204,SN,00.02.13"
        mock_rm.return_value.open_resource.return_value = mock_resource
        scope = DHO4204("USB0::0x1AB1::0x0610::TEST::INSTR")
    return scope


def test_exact_match_returns_same_depth():
    assert _nearest_memory_depth(1_000_000, 1) == 1_000_000


def test_snaps_to_nearest_by_absolute_difference():
    # 600k is nearer 1M (400k away) than 100k (500k away)
    assert _nearest_memory_depth(600_000, 1) == 1_000_000
    # 300k is nearer 100k (200k away) than 1M (700k away)
    assert _nearest_memory_depth(300_000, 1) == 100_000


def test_single_channel_can_reach_500M():
    assert _nearest_memory_depth(500_000_000, 1) == 500_000_000


def test_two_channels_cap_at_250M():
    assert _nearest_memory_depth(500_000_000, 2) == 250_000_000


def test_three_or_four_channels_cap_at_125M():
    assert _nearest_memory_depth(500_000_000, 3) == 125_000_000
    assert _nearest_memory_depth(500_000_000, 4) == 125_000_000


def _stub_capture_deps(scope, canned):
    """Replace capture()'s hardware-facing collaborators with mocks; return
    canned {ch: (t, v)} from get_waveforms."""
    scope.channel_enable = MagicMock()
    scope.timebase_scale = MagicMock()
    scope.get_timebase = MagicMock(return_value={"scale_s": 0.05})  # 0.5 s window
    scope.acquire_memory_depth = MagicMock()
    scope.single_trigger_with_verify = MagicMock(return_value=True)
    scope.wait_for_trigger_stop = MagicMock()
    scope.trigger_force = MagicMock()
    scope.get_waveforms = MagicMock(return_value=canned)
    scope.save_waveforms_csv = MagicMock()
    scope.sample_rate = MagicMock(return_value=2e6)


def test_capture_snaps_depth_and_returns_shared_time_axis():
    scope = make_scope()
    t = np.linspace(0, 0.5, 4)
    canned = {1: (t, np.arange(4.0)), 2: (t, np.arange(4.0) * 2)}
    _stub_capture_deps(scope, canned)

    # 900k points -> nearest depth is 1M; two channels enabled
    time_axis, data = scope.capture(duration=0.5, points=900_000, channels=[1, 2])

    scope.acquire_memory_depth.assert_called_once_with(1_000_000)
    scope.get_waveforms.assert_called_once_with([1, 2], mode="RAW", points=1_000_000)
    assert np.array_equal(time_axis, t)
    assert set(data) == {1, 2}
    assert np.array_equal(data[2], np.arange(4.0) * 2)


def test_capture_forces_trigger_by_default_and_waits_when_asked():
    scope = make_scope()
    t = np.array([0.0, 1.0])
    _stub_capture_deps(scope, {1: (t, t)})

    scope.capture(duration=0.5, points=1000, channels=[1])
    scope.trigger_force.assert_called_once()
    scope.wait_for_trigger_stop.assert_not_called()

    _stub_capture_deps(scope, {1: (t, t)})
    scope.capture(duration=0.5, points=1000, channels=[1], wait_for_trigger=True, trigger_timeout=3.0)
    scope.wait_for_trigger_stop.assert_called_once_with(3.0)
    scope.trigger_force.assert_not_called()


def test_capture_writes_csv_only_when_out_given():
    scope = make_scope()
    t = np.array([0.0, 1.0])
    _stub_capture_deps(scope, {1: (t, t)})

    scope.capture(duration=0.5, points=1000, channels=[1])
    scope.save_waveforms_csv.assert_not_called()

    scope.capture(duration=0.5, points=1000, channels=[1], out="rec.csv")
    scope.save_waveforms_csv.assert_called_once_with("rec.csv", {1: (t, t)})
