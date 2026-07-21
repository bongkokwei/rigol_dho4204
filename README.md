# Rigol DHO4204 Python Control Interface

Python class for controlling the Rigol DHO4204 4-channel oscilloscope via VISA (USB or LAN). Provides an API for channel configuration, waveform acquisition, measurements, plotting, and data export.

## Installation

```bash
pip install pyvisa pyvisa-py numpy matplotlib
```

## Basic Usage

```python
from rigol_dho4204 import DHO4204

with DHO4204() as scope:
    # Configure channel
    scope.channel_enable(1, True)
    scope.channel_coupling(1, "DC")
    scope.channel_scale(1, 0.5)  # 500 mV/div
    scope.timebase_scale(500e-9)  # 500 ns/div
    scope.trigger_edge(ch=1, level=1.0, slope="POS")

    # Capture
    scope.run()
    import time
    time.sleep(1)
    scope.stop()

    # Get waveform data
    t, v = scope.get_waveform(1, points=1000)
    
    # Or plot directly
    scope.plot_waveform(1, save_path="waveform.png")

    # Get measurements
    measurements = scope.measure_all(1)
    print(measurements)
```

## Multi-channel Waveform Acquisition

Acquire and export waveforms from multiple channels:

```python
with DHO4204() as scope:
    # Get waveforms from channels 1, 2, 3
    waveforms = scope.get_waveforms(channels=[1, 2, 3], points=500)
    
    # Plot all channels together
    scope.plot_waveforms(channels=[1, 2, 3], save_path="multichannel.png")
    
    # Save to CSV (all channels in one file)
    scope.save_waveforms_csv("data.csv", waveforms)
    
    # Or save individual channel CSVs
    for ch, (t, v) in waveforms.items():
        scope.save_waveform_csv(f"ch{ch}.csv", t, v, ch=ch)
```

## Increasing Waveform Resolution & Range

`NORMal` mode is capped at 1000 points (the scope's screen buffer). For higher resolution or a longer capture window, set the memory depth and use `MAXimum`/`RAW` mode — points are now clamped to the actual memory depth instead of being silently ignored:

```python
with DHO4204() as scope:
    scope.acquire_memory_depth("10M")     # raise record length
    print(scope.get_acquire_config())     # {'memory_depth': ..., 'sample_rate_Sps': ...}

    t, v = scope.get_waveform(1, mode="RAW", points=1_000_000)
```

## Trigger Holdoff

Use holdoff to stably trigger complex repetitive waveforms (e.g. pulse trains):

```python
with DHO4204() as scope:
    scope.trigger_holdoff(2e-7)         # 200 ns
    print(scope.get_trigger_holdoff())
```

## Connection Options

Pass a VISA resource string to connect:

```python
# USB (auto-detected if None)
scope = DHO4204("USB0::0x1AB1::0x0588::DS4A...::INSTR")

# LAN (VXI-11)
scope = DHO4204("TCPIP0::192.168.1.100::INSTR")

# LAN (raw socket)
scope = DHO4204("TCPIP0::192.168.1.100::5555::SOCKET")
```

## API Reference

### Channel Control

- `channel_enable(ch, on)` - Enable/disable channel
- `channel_scale(ch, volts_per_div)` - Set vertical scale
- `channel_offset(ch, volts)` - Set vertical offset
- `channel_coupling(ch, mode)` - Set coupling (DC/AC/GND)
- `channel_probe(ch, ratio)` - Set probe attenuation
- `channel_bwlimit(ch, on)` - Enable 20 MHz BW limit
- `get_channel_config(ch)` - Get channel settings

### Timebase & Trigger

- `timebase_scale(seconds_per_div)` - Set horizontal scale
- `timebase_offset(seconds)` - Set horizontal offset
- `trigger_edge(ch, level, slope)` - Configure edge trigger
- `trigger_level(level)` - Set trigger level
- `single_trigger_with_verify(timeout, poll_interval)` - Arm a single acquisition, retrying until the sweep mode is confirmed SINGle via read-back (works around silently dropped writes)
- `trigger_force()` - Force trigger
- `trigger_status()` - Get trigger status
- `trigger_holdoff(seconds)` - Set trigger holdoff time (8 ns to 10 s)
- `get_trigger_holdoff()` - Get trigger holdoff time
- `wait_for_trigger_stop(timeout, poll_interval)` - Block until trigger_status() reports STOP; on timeout, runs system_restart() and raises TimeoutError

### Run/Stop

- `run()` - Start acquisition
- `stop()` - Stop acquisition

### Measurements

- `measure(ch, item)` - Read single measurement (VPP, VMAX, VMIN, VRMS, FREQ, PER, RTIM, FTIM, etc.)
- `measure_all(ch)` - Get all common measurements for a channel

### Acquisition Configuration

- `acquire_memory_depth(depth)` - Set memory depth (e.g. `1000`, `"1M"`, `"10M"`, `"AUTO"`)
- `get_memory_depth()` - Get current memory depth in points (NaN if AUTO)
- `sample_rate()` - Get current sample rate in samples/sec (read-only)
- `get_acquire_config()` - Get memory depth and sample rate together

### Waveform Acquisition

- `get_waveform(ch, mode="NORMal", points=1000)` - Get waveform data from one channel, returns (time_array, voltage_array). `points` is clamped to 1000 in `NORMal` mode, or to the current memory depth in `MAXimum`/`RAW` mode
- `get_waveforms(channels=None, mode="NORMal", points=1000)` - Get waveforms from multiple channels, returns {channel: (time, voltage)}

### Data Export

- `save_waveform_csv(filepath, time_data, voltage_data, ch=1)` - Save single channel to CSV
- `save_waveforms_csv(filepath, waveforms)` - Save multiple channels to CSV

### Plotting

- `plot_waveform(ch, save_path=None)` - Plot single channel (show or save)
- `plot_waveforms(channels=None, mode="NORMal", points=1000, save_path=None)` - Plot multiple channels

### Utilities

- `idn()` - Get instrument ID
- `reset()` - Reset instrument
- `system_restart()` - Full system restart
- `auto_scale()` - Auto scale all channels
- `screenshot(filepath)` - Save screenshot as PNG
- `save_setup()` - Save scope configuration
- `recall_setup(data)` - Restore scope configuration

## Notes

- Waveform acquisition in Normal mode is limited to 1000 points; use `MAXimum`/`RAW` mode plus `acquire_memory_depth()` for more
- The script automatically stops acquisition before reading waveforms for reliable data
- Waveform reads prefer a single bulk transfer and only fall back to chunked reads for USB backends (e.g. libusb0 on Windows) that time out on large reads
- Use context manager (`with DHO4204() as scope:`) for safe cleanup
