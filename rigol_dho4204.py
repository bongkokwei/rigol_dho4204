"""
Rigol DHO4204 Oscilloscope Control Script
==========================================
Requires: pip install pyvisa pyvisa-py numpy matplotlib

Connection options:
  - USB:   USB0::0x1AB1::0x0588::DS4A...::INSTR
  - LAN:   TCPIP0::192.168.1.100::INSTR
  - LAN (raw): TCPIP0::192.168.1.100::5555::SOCKET
"""

import pyvisa
import numpy as np
import time
import struct
import csv
from pathlib import Path


class DHO4204:
    """Control interface for Rigol DHO4204 4-channel oscilloscope."""

    CHANNELS = (1, 2, 3, 4)

    def __init__(self, resource_string: str | None = None, timeout_ms: int = 10_000):
        self.rm = pyvisa.ResourceManager()
        if resource_string is None:
            resource_string = self._auto_detect()
        self.inst = self.rm.open_resource(resource_string)
        self.inst.timeout = timeout_ms
        # For raw socket connections
        if "SOCKET" in resource_string.upper():
            self.inst.read_termination = "\n"
            self.inst.write_termination = "\n"
        # Reset USB/VISA pipe in case previous session crashed mid-transfer
        try:
            self.inst.clear()
        except Exception:
            pass
        time.sleep(0.3)
        print(f"Connected: {self.idn()}")

    def _auto_detect(self) -> str:
        """Auto-detect the first Rigol instrument on USB/LAN."""
        resources = self.rm.list_resources()
        for r in resources:
            if "1AB1" in r.upper():  # Rigol USB vendor ID
                return r
        if resources:
            return resources[0]
        raise ConnectionError("No VISA instruments found. Check USB/LAN connection.")

    # ── Core VISA helpers ──────────────────────────────────────────────

    def write(self, cmd: str):
        self.inst.write(cmd)

    def query(self, cmd: str) -> str:
        return self.inst.query(cmd).strip()

    def query_safe(self, cmd: str, default: str = "") -> str:
        """Query with timeout protection — returns default on failure."""
        try:
            return self.inst.query(cmd).strip()
        except Exception:
            return default

    def idn(self) -> str:
        return self.query("*IDN?")

    def reset(self):
        self.write("*RST")
        time.sleep(2)

    def system_restart(self):
        self.write(":SYSTem:RESet")
        time.sleep(15)

    def auto_scale(self):
        self.write(":AUToset")
        time.sleep(3)

    # ── Channel configuration ──────────────────────────────────────────

    def channel_enable(self, ch: int, on: bool = True):
        self._check_ch(ch)
        self.write(f":CHANnel{ch}:DISPlay {'ON' if on else 'OFF'}")

    def channel_scale(self, ch: int, volts_per_div: float):
        """Set vertical scale (V/div)."""
        self._check_ch(ch)
        self.write(f":CHANnel{ch}:SCALe {volts_per_div}")

    def channel_offset(self, ch: int, volts: float):
        self._check_ch(ch)
        self.write(f":CHANnel{ch}:OFFSet {volts}")

    def channel_coupling(self, ch: int, mode: str = "DC"):
        """Set coupling: DC, AC, or GND."""
        self._check_ch(ch)
        self.write(f":CHANnel{ch}:COUPling {mode.upper()}")

    def channel_probe(self, ch: int, ratio: float = 10.0):
        """Set probe attenuation ratio (1, 10, 100, etc.)."""
        self._check_ch(ch)
        self.write(f":CHANnel{ch}:PROBe {ratio}")

    def channel_bwlimit(self, ch: int, on: bool = True):
        """Enable 20 MHz bandwidth limit."""
        self._check_ch(ch)
        self.write(f":CHANnel{ch}:BWLimit {'20M' if on else 'OFF'}")

    def get_channel_config(self, ch: int) -> dict:
        self._check_ch(ch)
        return {
            "display": self.query(f":CHANnel{ch}:DISPlay?"),
            "scale_V": float(self.query(f":CHANnel{ch}:SCALe?")),
            "offset_V": float(self.query(f":CHANnel{ch}:OFFSet?")),
            "coupling": self.query(f":CHANnel{ch}:COUPling?"),
            "probe": float(self.query(f":CHANnel{ch}:PROBe?")),
        }

    # ── Timebase ───────────────────────────────────────────────────────

    def timebase_scale(self, seconds_per_div: float):
        self.write(f":TIMebase:MAIN:SCALe {seconds_per_div}")

    def timebase_offset(self, seconds: float):
        self.write(f":TIMebase:MAIN:OFFSet {seconds}")

    def get_timebase(self) -> dict:
        return {
            "scale_s": float(self.query(":TIMebase:MAIN:SCALe?")),
            "offset_s": float(self.query(":TIMebase:MAIN:OFFSet?")),
        }

    # ── Trigger ────────────────────────────────────────────────────────

    def trigger_edge(self, ch: int = 1, level: float = 0.0, slope: str = "POS"):
        """Configure edge trigger. slope: POS, NEG, RFAL."""
        self._check_ch(ch)
        self.write(":TRIGger:MODE EDGE")
        self.write(f":TRIGger:EDGE:SOURce CHANnel{ch}")
        self.write(f":TRIGger:EDGE:SLOPe {slope.upper()}")
        self.write(f":TRIGger:EDGE:LEVel {level}")

    def trigger_level(self, level: float):
        self.write(f":TRIGger:EDGE:LEVel {level}")

    def trigger_single(self):
        self.write(":SINGle")

    def trigger_force(self):
        self.write(":TFORce")

    def trigger_status(self) -> str:
        return self.query(":TRIGger:STATus?")

    # ── Run/Stop ───────────────────────────────────────────────────────

    def run(self):
        self.write(":RUN")

    def stop(self):
        self.write(":STOP")

    # ── Measurements ───────────────────────────────────────────────────

    def measure(self, ch: int, item: str) -> float:
        """
        Read a measurement.  Common items:
          VPP, VMAX, VMIN, VAMP, VTOP, VBAS, VAVG, VRMS,
          FREQ, PER, PDUT, NDUT, RISE, FALL, PWIDth, NWIDth
        """
        self._check_ch(ch)
        # DHO4000 syntax: first enable, then query
        self.write(f":MEASure:ITEM {item.upper()},CHANnel{ch}")
        time.sleep(0.3)
        val = self.query_safe(
            f":MEASure:ITEM? {item.upper()},CHANnel{ch}", default="9.9E37"
        )
        # If that fails, try the direct query form
        if val in ("", "9.9E37"):
            val = self.query_safe(
                f":MEASure:{item.upper()}? CHANnel{ch}", default="9.9E37"
            )
        try:
            return float(val)
        except ValueError:
            return float("nan")

    def measure_all(self, ch: int) -> dict:
        """Grab common measurements for a channel."""
        items = ["VPP", "VMAX", "VMIN", "VRMS", "FREQ", "PER", "RISE", "FALL"]
        return {item: self.measure(ch, item) for item in items}

    # ── Waveform data acquisition ──────────────────────────────────────

    def get_waveform(
        self, ch: int, mode: str = "NORMal", points: int = 1000
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Download waveform data from a channel.

        Args:
            ch:     Channel number (1-4).
            mode:   NORMal (screen), MAXimum (full memory), RAW.
            points: Number of points to request (NORMal max: 1000).

        Returns:
            (time_array, voltage_array) as numpy arrays.

        Notes:
            Uses a manual chunked read_raw loop as a fallback for libusb0 on
            Windows, which times out on large single bulk reads via
            query_binary_values.
        """
        self._check_ch(ch)

        # Scope must be stopped for reliable waveform reads on DHO4000
        self.stop()
        time.sleep(0.5)

        self.write(f":WAVeform:SOURce CHANnel{ch}")
        self.write(f":WAVeform:MODE {mode.upper()}")
        self.write(":WAVeform:FORMat BYTE")

        # Clamp points to valid range for NORMal mode (1–1000)
        if mode.upper() in ("NORMAL", "NORM"):
            points = min(max(1, points), 1000)
            self.write(f":WAVeform:POINts {points}")

        # Sync — wait for scope to acknowledge settings
        self.query("*OPC?")
        time.sleep(0.3)

        old_timeout = self.inst.timeout
        old_chunk = self.inst.chunk_size

        self.inst.timeout = 1000
        self.inst.chunk_size = 4096

        try:
            # Read preamble for scaling
            preamble = self.query(":WAVeform:PREamble?").split(",")
            x_inc = float(preamble[4])
            x_orig = float(preamble[5])
            y_inc = float(preamble[7])
            y_orig = float(preamble[8])
            y_ref = float(preamble[9])

            # Send the data request, then read raw in small chunks.
            # This sidesteps the libusb0 bulk-read timeout on Windows.
            self.write(":WAVeform:DATA?")
            time.sleep(0.5)  # let scope prepare the response

            raw = b""
            while True:
                try:
                    chunk = self.inst.read_raw(self.inst.chunk_size)
                    raw += chunk
                    if len(chunk) < self.inst.chunk_size:
                        break  # last (short) chunk — transfer complete
                except Exception:
                    break  # timeout on an empty read means we're done

            # Parse TMC/IEEE 488.2 block header: #N<N digits of length><data>
            if raw and chr(raw[0]) == "#":
                n_digits = int(chr(raw[1]))
                data_len = int(raw[2 : 2 + n_digits])
                raw = raw[2 + n_digits : 2 + n_digits + data_len]

            data = np.frombuffer(raw, dtype=np.uint8)

            # Scale to physical units
            voltage = (data.astype(float) - y_ref) * y_inc + y_orig
            time_arr = np.arange(len(voltage)) * x_inc + x_orig

            return time_arr, voltage
        finally:
            self.inst.timeout = old_timeout
            self.inst.chunk_size = old_chunk

    def plot_waveform(self, ch: int, save_path: str | None = None):
        """Capture and plot a waveform using matplotlib."""
        import matplotlib.pyplot as plt

        t, v = self.get_waveform(ch)

        fig, ax = plt.subplots(figsize=(12, 5))
        ax.plot(t * 1e6, v, linewidth=0.7, rasterized=True)
        ax.set_xlabel("Time (µs)")
        ax.set_ylabel("Voltage (V)")
        ax.set_title(f"DHO4204 — Channel {ch}")
        ax.grid(True, alpha=0.3)

        if save_path:
            fig.savefig(save_path, dpi=300, bbox_inches="tight")
            print(f"Saved plot: {save_path}")
        else:
            plt.show()
        plt.close(fig)

    def get_waveforms(
        self, channels: list[int] | None = None, mode: str = "NORMal", points: int = 1000
    ) -> dict[int, tuple[np.ndarray, np.ndarray]]:
        """
        Download waveform data from multiple channels.

        Args:
            channels:   List of channel numbers (1-4). If None, use all active channels.
            mode:       NORMal (screen), MAXimum (full memory), RAW.
            points:     Number of points to request (NORMal max: 1000).

        Returns:
            Dictionary {channel: (time_array, voltage_array)} for each channel.
        """
        if channels is None:
            channels = list(self.CHANNELS)

        waveforms = {}
        for ch in channels:
            try:
                t, v = self.get_waveform(ch, mode=mode, points=points)
                waveforms[ch] = (t, v)
                print(f"Fetched waveform from Channel {ch}")
            except Exception as e:
                print(f"Failed to fetch waveform from Channel {ch}: {e}")

        return waveforms

    def save_waveform_csv(
        self, filepath: str, time_data: np.ndarray, voltage_data: np.ndarray, ch: int = 1
    ):
        """
        Save waveform data to CSV file.

        Args:
            filepath:     Path to save CSV file.
            time_data:    Time array (x-axis).
            voltage_data: Voltage array (y-axis).
            ch:           Channel number (for reference in file).
        """
        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([f"Channel {ch} Waveform"])
            writer.writerow(["Time (s)", "Voltage (V)"])
            for t, v in zip(time_data, voltage_data):
                writer.writerow([f"{t:.15e}", f"{v:.6f}"])
        print(f"Saved waveform CSV: {filepath}")

    def save_waveforms_csv(self, filepath: str, waveforms: dict[int, tuple[np.ndarray, np.ndarray]]):
        """
        Save multiple waveforms to a single CSV file.

        Args:
            filepath:   Path to save CSV file.
            waveforms:  Dictionary {channel: (time_array, voltage_array)}.
        """
        if not waveforms:
            print("No waveforms to save.")
            return

        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Multi-channel Waveform Data"])
            writer.writerow(["Time (s)"] + [f"CH{ch} (V)" for ch in sorted(waveforms.keys())])

            # Assume all channels have the same time array (use the first one)
            time_data = waveforms[sorted(waveforms.keys())[0]][0]
            for i, t in enumerate(time_data):
                row = [f"{t:.15e}"]
                for ch in sorted(waveforms.keys()):
                    _, voltage_data = waveforms[ch]
                    row.append(f"{voltage_data[i]:.6f}")
                writer.writerow(row)

        print(f"Saved multi-channel waveforms CSV: {filepath}")

    def plot_waveforms(
        self,
        channels: list[int] | None = None,
        mode: str = "NORMal",
        points: int = 1000,
        save_path: str | None = None,
    ):
        """
        Capture and plot waveforms from multiple channels.

        Args:
            channels:   List of channel numbers (1-4). If None, use all active channels.
            mode:       NORMal (screen), MAXimum (full memory), RAW.
            points:     Number of points to request (NORMal max: 1000).
            save_path:  Optional path to save the figure.
        """
        import matplotlib.pyplot as plt

        waveforms = self.get_waveforms(channels=channels, mode=mode, points=points)

        if not waveforms:
            print("No waveforms to plot.")
            return

        fig, ax = plt.subplots(figsize=(14, 6))

        colors = ["C0", "C1", "C2", "C3"]  # matplotlib default colors
        for idx, (ch, (t, v)) in enumerate(sorted(waveforms.items())):
            ax.plot(
                t * 1e6,
                v,
                linewidth=0.7,
                rasterized=True,
                label=f"CH{ch}",
                color=colors[idx % len(colors)],
            )

        ax.set_xlabel("Time (µs)")
        ax.set_ylabel("Voltage (V)")
        ax.set_title("DHO4204 — Multi-channel Waveforms")
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper right")

        if save_path:
            fig.savefig(save_path, dpi=300, bbox_inches="tight")
            print(f"Saved plot: {save_path}")
        else:
            plt.show()
        plt.close(fig)

    # ── Screenshot ─────────────────────────────────────────────────────

    def screenshot(self, filepath: str = "screenshot.png"):
        """Download a screenshot from the oscilloscope."""
        self.write(":DISPlay:DATA? PNG")
        raw = self.inst.read_raw()
        # Strip TMC header
        header_len = 2 + int(chr(raw[1]))
        img_data = raw[header_len:]
        # Remove trailing newline if present
        if img_data[-1:] == b"\n":
            img_data = img_data[:-1]
        Path(filepath).write_bytes(img_data)
        print(f"Screenshot saved: {filepath}")

    # ── Cursor measurements ────────────────────────────────────────────

    def cursor_manual(self, ch: int, xa: float, xb: float):
        """Set manual time cursors."""
        self._check_ch(ch)
        self.write(":CURSor:MODE MANual")
        self.write(f":CURSor:MANual:SOURce CHANnel{ch}")
        self.write(":CURSor:MANual:TYPE X")
        self.write(f":CURSor:MANual:CAX {xa}")
        self.write(f":CURSor:MANual:CBX {xb}")

    # ── Math / FFT ─────────────────────────────────────────────────────

    def math_fft(self, ch: int = 1, math_ch: int = 1):
        """Enable FFT on a channel."""
        self._check_ch(ch)
        if math_ch not in (1, 2, 3, 4):
            raise ValueError(f"Invalid math channel {math_ch}. Must be 1-4.")
        self.write(f":MATH{math_ch}:DISPlay ON")
        self.write(f":MATH{math_ch}:OPERator FFT")
        self.write(f":MATH{math_ch}:SOURce1 CHANnel{ch}")

    # ── Save/Recall ────────────────────────────────────────────────────

    def save_setup(self) -> bytes:
        """Read the current setup as a binary data blob (to save externally)."""
        old_timeout = self.inst.timeout
        self.inst.timeout = 15_000
        try:
            return self.inst.query_binary_values(
                ":SYSTem:SETup?", datatype="B", container=bytes, header_fmt="ieee"
            )
        finally:
            self.inst.timeout = old_timeout

    def recall_setup(self, setup_data: bytes):
        """Restore a setup from a binary data blob previously obtained via save_setup()."""
        # Build TMC/IEEE 488.2 block header: #N<length_digits><data>
        length_str = str(len(setup_data))
        header = f"#{ len(length_str)}{length_str}".encode()
        self.inst.write_raw(b":SYSTem:SETup " + header + setup_data)
        time.sleep(1)

    # ── Utility ────────────────────────────────────────────────────────

    def _check_ch(self, ch: int):
        if ch not in self.CHANNELS:
            raise ValueError(f"Invalid channel {ch}. Must be one of {self.CHANNELS}")

    def close(self):
        """Release the VISA session cleanly. Safe to call multiple times."""
        if self.inst is None:
            return
        for action in (self.inst.clear, self.inst.close, self.rm.close):
            try:
                action()
            except Exception:
                pass
        self.inst = None
        self.rm = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ══════════════════════════════════════════════════════════════════════
# Example usage
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":

    with DHO4204() as scope:
        # Basic setup
        scope.channel_enable(1, True)
        scope.channel_coupling(1, "DC")
        scope.channel_scale(1, 0.5)
        scope.channel_offset(1, 0.0)
        scope.timebase_scale(500e-9)
        scope.trigger_edge(ch=1, level=1.0, slope="POS")

        # Let it trigger
        scope.run()
        time.sleep(1)
        scope.stop()

        # Read measurements
        measurements = scope.measure_all(1)
        print("\n── Measurements (CH1) ──")
        for k, v in measurements.items():
            print(f"  {k:>6s}: {v:.4g}")

        scope.system_restart()
        print("\nScope restarted and ready.")

        # # Download and plot waveform
        # scope.plot_waveform(1, save_path="figures/awg_multitone_3tones_python.png")

        # # Multi-channel waveform examples:
        # waveforms = scope.get_waveforms(channels=[1, 2, 3], points=500)
        # scope.plot_waveforms(channels=[1, 2, 3], save_path="figures/multichannel.png")
        # scope.save_waveforms_csv("waveforms_multichannel.csv", waveforms)

        # # Single channel CSV export:
        # t, v = scope.get_waveform(1)
        # scope.save_waveform_csv("waveform_ch1.csv", t, v, ch=1)

        # # Save a screenshot
        # scope.screenshot("screen.png")

        print("\nDone with oscilloscope demo.")
