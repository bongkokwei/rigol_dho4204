Here is a README file tailored for your Rigol DHO4204 control script.

---

# Rigol DHO4204 Python Control Interface

A robust Python class for controlling and automating the **Rigol DHO4204** 4-channel oscilloscope. It communicates via VISA (USB or LAN) using SCPI commands and provides an easy-to-use API for configuration, measurement extraction, waveform data acquisition, and plotting.

## Features

* **Auto-Discovery**: Automatically finds and connects to the first available Rigol instrument if no connection string is provided.
* **Robust Waveform Acquisition**: Implements chunked reading for raw waveform data to bypass common `libusb0` bulk-read timeouts on Windows.
* **Data Processing**: Converts raw oscilloscope TMC/IEEE 488.2 block data directly into scaled `numpy` time and voltage arrays.
* **Multi-channel Support**: Simultaneously acquire and plot waveforms from multiple channels.
* **CSV Export**: Save waveform data (single or multi-channel) to CSV files for analysis in other tools.
* **Built-in Plotting**: Uses `matplotlib` to quickly plot or save captured waveforms, with multi-channel visualization.
* **Comprehensive Control**:
  * Channel setup (Scale, Offset, Coupling, Probe ratio, Bandwidth limit)
  * Timebase & Trigger configuration
  * Run, Stop, Single, and Force Trigger actions
  * On-board hardware measurements (VPP, FREQ, VRMS, etc.)
  * Screenshot extraction (PNG)
  * Save/Recall complete scope setups as binary blobs
* **Context Manager Support**: Easily integrate into `with` blocks to ensure clean setup and teardown.

## Requirements

This script relies on standard scientific Python libraries and PyVISA.

```bash
pip install pyvisa pyvisa-py numpy matplotlib

```

*(Note: If you are connecting over USB on Windows, ensure you have the appropriate VISA backend installed, such as NI-VISA or configure `pyvisa-py` with `libusb`.)*

## Connection Options

The `DHO4204` class accepts standard VISA resource strings. If left blank, it will attempt to auto-detect a Rigol instrument.

* **USB**: `USB0::0x1AB1::0x0588::DS4A...::INSTR`
* **LAN (VXI-11)**: `TCPIP0::192.168.1.100::INSTR`
* **LAN (Raw Socket)**: `TCPIP0::192.168.1.100::5555::SOCKET`

## Quick Start Example

```python
import time
from rigol_dho4204 import DHO4204

# Use the context manager to ensure safe closing of the connection
with DHO4204() as scope:
    # 1. Basic Setup
    scope.channel_enable(1, True)
    scope.channel_coupling(1, "DC")
    scope.channel_scale(1, 0.5)    # 500 mV/div
    scope.channel_offset(1, 0.0)   # 0 V offset
    
    scope.timebase_scale(500e-9)   # 500 ns/div
    scope.trigger_edge(ch=1, level=1.0, slope="POS")

    # 2. Capture a signal
    scope.run()
    time.sleep(1)
    scope.stop()

    # 3. Read hardware measurements
    measurements = scope.measure_all(ch=1)
    print("\n── Measurements (CH1) ──")
    for k, v in measurements.items():
        print(f"{k:>6s}: {v:.4g}")

    # 4. Save a screenshot to disk
    scope.screenshot("screen.png")

    # 5. Extract and plot waveform data
    # (Extracts to numpy arrays natively, but plot_waveform is a handy wrapper)
    scope.plot_waveform(1, save_path="ch1_waveform.png")

    # 6. Multi-channel waveform acquisition and export
    waveforms = scope.get_waveforms(channels=[1, 2, 3], points=500)
    scope.plot_waveforms(channels=[1, 2, 3], save_path="multichannel.png")
    scope.save_waveforms_csv("waveforms.csv", waveforms)

```

## API Overview

### Core Functions

* `idn()`: Returns the instrument's identification string.
* `reset()` / `system_restart()`: Resets the instrument.
* `auto_scale()`: Triggers the oscilloscope's auto-set feature.
* `save_setup()` / `recall_setup(data)`: Save and load device configurations.

### Channel & Timebase

* `channel_enable(ch, on)`: Turn a channel on/off.
* `channel_scale(ch, volts_per_div)`: Set vertical scale.
* `channel_offset(ch, volts)`: Set vertical offset.
* `channel_coupling(ch, mode)`: Set coupling (`"DC"`, `"AC"`, `"GND"`).
* `timebase_scale(seconds_per_div)`: Set horizontal scale.

### Acquisition & Measurement

* `measure(ch, item)`: Query a specific measurement (e.g., `"VPP"`, `"FREQ"`).
* `measure_all(ch)`: Get all common measurements for a channel at once.
* `get_waveform(ch, mode, points)`: Download a single channel trace. Returns `(time_array, voltage_array)`.
* `get_waveforms(channels, mode, points)`: Download waveforms from multiple channels. Returns `{channel: (time_array, voltage_array)}` dict.
* `screenshot(filepath)`: Download the current screen buffer as a PNG file.

### Data Export

* `save_waveform_csv(filepath, time_data, voltage_data, ch)`: Save single-channel waveform to CSV file.
* `save_waveforms_csv(filepath, waveforms)`: Save multiple-channel waveforms to a single CSV file with aligned time data.

### Plotting

* `plot_waveform(ch, save_path)`: Capture and plot a single-channel waveform.
* `plot_waveforms(channels, mode, points, save_path)`: Capture and plot multiple-channel waveforms with legend.

## Multi-channel Operations

The DHO4204 class supports simultaneous acquisition, export, and visualization of waveforms from multiple channels.

### Example: Acquire and Export Multi-channel Data

```python
from rigol_dho4204 import DHO4204

with DHO4204() as scope:
    # Setup channels 1, 2, and 3
    for ch in [1, 2, 3]:
        scope.channel_enable(ch, True)
        scope.channel_coupling(ch, "DC")
        scope.channel_scale(ch, 0.5)
    
    # Acquire waveforms from multiple channels
    waveforms = scope.get_waveforms(channels=[1, 2, 3], points=500)
    
    # Export to CSV (all channels in one file)
    scope.save_waveforms_csv("multichannel_data.csv", waveforms)
    
    # Plot all channels together with legend
    scope.plot_waveforms(channels=[1, 2, 3], save_path="multichannel_plot.png")
    
    # Or save individual channel CSVs
    for ch, (t, v) in waveforms.items():
        scope.save_waveform_csv(f"ch{ch}_data.csv", t, v, ch=ch)
```

### CSV Output Format

**Single-channel CSV:**
```
Channel 1 Waveform
Time (s),Voltage (V)
0.000000e+00,0.123456
5.000000e-09,0.234567
...
```

**Multi-channel CSV:**
```
Multi-channel Waveform Data
Time (s),CH1 (V),CH2 (V),CH3 (V)
0.000000e+00,0.123456,0.456789,0.789012
5.000000e-09,0.234567,0.567890,0.890123
...
```