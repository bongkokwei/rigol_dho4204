# Duration-driven waveform capture — design

## Goal

Add a single method, `DHO4204.capture()`, that records one contiguous
waveform of a **user-specified duration** and **user-specified point count**,
across one or more channels, and optionally writes it to CSV.

The scope captures a fixed-size record per trigger (not a continuous stream),
so "how long to record" maps to the timebase, and "how many points" maps to
memory depth. Sample rate is derived: `sample_rate = memory_depth / duration`.

## Public API

```python
def capture(
    self,
    duration: float,                 # seconds of signal to record
    points: int,                     # desired total samples (snapped to hardware)
    channels: Sequence[int] = (1,),  # one or more channels, one shared acquisition
    out: str | None = None,          # CSV path; None = return only
    wait_for_trigger: bool = False,  # False: grab now; True: wait for configured edge
    trigger_timeout: float = 10.0,   # seconds, only used when wait_for_trigger=True
) -> tuple[np.ndarray, dict[int, np.ndarray]]:
    """Record one duration-long acquisition and return (time, {ch: voltage})."""
```

Example:

```python
with DHO4204() as scope:
    scope.trigger_edge(ch=1, level=1.0)          # optional, if waiting
    t, data = scope.capture(duration=0.5, points=1_000_000,
                            channels=[1, 2], out="rec.csv")
```

## Flow

1. Enable each requested channel (`channel_enable`).
2. `timebase_scale(duration / NUM_HORIZONTAL_DIVS)`. Read timebase back; if the
   scope clamped it (duration out of the instrument's range), log a warning with
   the actual window achieved.
3. Set memory depth to `_nearest_memory_depth(points, len(channels))` via
   `acquire_memory_depth`. Read back with `get_memory_depth()` for the real value.
4. Arm the acquisition with `single_trigger_with_verify()` (verified
   `:TRIGger:SWEep SINGle`), then:
   - `wait_for_trigger=False` (default): `trigger_force()` so the record always
     completes immediately regardless of the input signal.
   - `wait_for_trigger=True`: `wait_for_trigger_stop(trigger_timeout)` — records
     the window around the user-configured edge, raises `TimeoutError` if it
     never fires.
5. `waveforms = get_waveforms(channels, mode="RAW", points=<actual depth>)`
   — existing method; reads every requested channel from the one stopped
   acquisition, settling once.
6. Reshape `{ch: (t, v)}` → shared `t` + `{ch: v}` (all channels share one time
   axis). If `out` given, `save_waveforms_csv(out, waveforms)` (existing).
7. Log actual points and `sample_rate()`; return `(t, {ch: v})`.

## New helper (the only genuinely new logic)

```python
# Available memory depths depend on how many channels are enabled
# (Programming Manual, :ACQuire:MDEPth). Values in points.
_MEMORY_DEPTHS = {
    1: [1e3, 1e4, 1e5, 1e6, 1e7, 2.5e7, 5e7, 1e8, 1.25e8, 2e8, 2.5e8, 5e8],
    2: [1e3, 1e4, 1e5, 1e6, 1e7, 2.5e7, 5e7, 1e8, 1.25e8, 2.5e8],
    # 3 or 4 channels share the same, smaller ceiling:
    "many": [1e3, 1e4, 1e5, 1e6, 1e7, 2.5e7, 5e7, 1e8, 1.25e8],
}

def _nearest_memory_depth(points: int, n_channels: int) -> int:
    """Snap a requested point count to the nearest hardware memory depth
    available for the given number of active channels."""
```

Nearest by absolute difference. `n_channels >= 3` uses the `"many"` list.

## Reuse (not rebuilt)

- `get_waveforms` — multi-channel read from one acquisition, settles once.
- `save_waveforms_csv` — writes `Time (s), CH1 (V), CH2 (V), …`.
- `single_trigger_with_verify`, `wait_for_trigger_stop`, `trigger_force`,
  `trigger_edge`, `channel_enable`, `timebase_scale`, `acquire_memory_depth`,
  `get_memory_depth`, `sample_rate`.

## Out of scope

- Continuous / streaming capture over minutes (single capture only).
- New output formats beyond the existing CSV.
- Re-implementing waveform download, settle timing, or the chunked-read fallback.

## Testing

- `_nearest_memory_depth` is pure → `assert`-based self-check covering: exact
  match, snap-up, snap-down, and the channel-count ceiling (3–4 ch caps at 125M).
- No hardware mock for `capture()` itself; it is orchestration over methods that
  already have their own coverage.
