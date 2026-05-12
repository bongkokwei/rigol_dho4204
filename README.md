
# Rigol DHO4204 Python Control Interface

A robust Python class for controlling and automating the **Rigol DHO4204** 4-channel oscilloscope. It communicates via VISA (USB or LAN) using SCPI commands and provides an easy-to-use API for configuration, measurement extraction, waveform data acquisition, and plotting.

## Features

* **Auto-Discovery**: Automatically finds and connects to the first available Rigol instrument if no connection string is provided.
* **Robust Waveform Acquisition**: Implements chunked reading for raw waveform data to bypass common `libusb0` bulk-read timeouts on Windows.
* **Data Processing**: Converts raw oscilloscope TMC/IEEE 488.2 block data directly into scaled `numpy` time and voltage arrays.
* **Built-in Plotting**: Uses `matplotlib` to quickly plot or save captured waveforms.
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
* `get_waveform(ch, mode, points)`: Downloads the trace. Returns `(time_array, voltage_array)`.
* `screenshot(filepath)`: Downloads the current screen buffer as a PNG file.
