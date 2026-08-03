"""
Rigol DHO4204 Oscilloscope Control Script
==========================================
Requires: pip install pyvisa pyvisa-py numpy matplotlib

Connection options:
  - USB:   USB0::0x1AB1::0x0588::DS4A...::INSTR
  - LAN:   TCPIP0::192.168.1.100::INSTR
  - LAN (raw): TCPIP0::192.168.1.100::5555::SOCKET

Logging:
  This module logs to the "rigol_dho4204" logger. Call
  `logging.basicConfig(level=logging.DEBUG)` in your script to see the
  per-call trace; by default nothing is emitted.
"""

import pyvisa
import numpy as np
import time
import struct
import csv
import logging
from pathlib import Path

__version__ = "0.1.0"
__all__ = ["DHO4204"]

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def _numeric_match(expected: float, tol: float = 0.5):
    """Build a `write_verified()` match callable for a numeric read-back."""
    logger.debug("_numeric_match(expected=%r, tol=%r)", expected, tol)

    def _match(readback: str) -> bool:
        logger.debug("_match(readback=%r) against %r", readback, expected)
        try:
            return abs(float(readback) - expected) <= tol
        except ValueError:
            return False

    return _match


class DHO4204:
    """Control interface for Rigol DHO4204 4-channel oscilloscope."""

    CHANNELS = (1, 2, 3, 4)
    NUM_HORIZONTAL_DIVS = 10  # DHO4204 screen width, in horizontal divisions

    def __init__(self, resource_string: str | None = None, timeout_ms: int = 10_000):
        logger.debug("__init__(resource_string=%r, timeout_ms=%r)", resource_string, timeout_ms)
        self.rm = pyvisa.ResourceManager()
        self.inst = None
        if resource_string is None:
            resource_string = self._auto_detect()
        self._resource_string = resource_string
        self._timeout_ms = timeout_ms
        try:
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
            logger.info("Connected: %s", self.idn())
        except pyvisa.errors.VisaIOError:
            # Stale USBTMC session lock from a prior process that hasn't
            # cleared yet (Windows/NI-VISA) — force a full teardown/reopen,
            # retrying with backoff until the device actually responds.
            logger.warning("Initial open failed, reconnecting: %s", resource_string)
            logger.info("Connected: %s", self.reconnect())

    def _auto_detect(self) -> str:
        """Auto-detect the first Rigol instrument on USB/LAN."""
        logger.debug("_auto_detect()")
        resources = self.rm.list_resources()
        logger.debug("VISA resources found: %r", resources)
        for r in resources:
            if "1AB1" in r.upper():  # Rigol USB vendor ID
                return r
        if resources:
            return resources[0]
        raise ConnectionError("No VISA instruments found. Check USB/LAN connection.")

    # ── Core VISA helpers ──────────────────────────────────────────────

    def write(self, cmd: str):
        logger.debug("write(%r)", cmd)
        self.inst.write(cmd)

    def query(self, cmd: str) -> str:
        logger.debug("query(%r)", cmd)
        response = self.inst.query(cmd).strip()
        logger.debug("query(%r) -> %r", cmd, response)
        return response

    def query_safe(self, cmd: str, default: str = "") -> str:
        """Query with timeout protection — returns default on failure."""
        logger.debug("query_safe(%r, default=%r)", cmd, default)
        try:
            return self.inst.query(cmd).strip()
        except Exception as exc:
            logger.debug("query_safe(%r) failed (%s), returning default", cmd, exc)
            return default

    def write_verified(
        self,
        cmd: str,
        query_cmd: str,
        match,
        timeout: float = 5.0,
        poll_interval: float = 0.2,
    ) -> bool:
        """Write a command, retrying until a read-back query confirms it took effect.

        Works around a DHO4204 firmware characteristic confirmed on hardware:
        a write issued while the scope is still busy (e.g. mid-acquisition on
        a large timebase/deep memory setup) can be silently dropped with no
        SCPI error surfaced (`:SYSTem:ERRor?` still reports "No error"). A
        single write-and-trust is therefore not reliable for settings whose
        effect matters downstream; this re-issues the write and re-queries
        until confirmed or the timeout elapses.

        Args:
            cmd:       The SCPI write command, e.g. ":TRIGger:SWEep SINGle".
            query_cmd: The query to read back the setting, e.g. ":TRIGger:SWEep?".
            match:     Either the expected readback string (case-insensitive,
                       matched against SCPI abbreviated forms too, e.g. "SINGLE"
                       matches a readback of "SING"), or a callable
                       `(readback: str) -> bool` for custom comparisons (e.g.
                       numeric tolerance).
            timeout:       Max seconds to keep retrying.
            poll_interval: Seconds to wait between attempts.

        Returns:
            True once confirmed, False if the timeout elapsed without confirmation.
        """
        logger.debug("write_verified(cmd=%r, query_cmd=%r, timeout=%r)", cmd, query_cmd, timeout)
        if isinstance(match, str):
            expected = match.strip().upper()

            def match(readback: str, _expected=expected) -> bool:
                return readback == _expected or _expected.startswith(readback)

        deadline = time.time() + timeout
        attempts = 0
        readback = ""
        while time.time() < deadline:
            attempts += 1
            self.write(cmd)
            readback = self.query_safe(query_cmd).strip().upper()
            if match(readback):
                logger.debug("write_verified(%r) confirmed after %d attempt(s)", cmd, attempts)
                return True
            time.sleep(poll_interval)
        # Include the last read-back: when the scope is clamping a request to a
        # legal value (rather than dropping the write while busy) that value is
        # the whole diagnosis, and every attempt returns the same one.
        logger.warning(
            "write_verified(%r) NOT confirmed after %d attempt(s) — %s last read back %r",
            cmd,
            attempts,
            query_cmd,
            readback,
        )
        return False

    def idn(self) -> str:
        logger.debug("idn()")
        return self.query("*IDN?")

    def reset(self):
        logger.debug("reset()")
        self.write("*RST")
        time.sleep(2)

    def system_restart(self, timeout_s: float = 30):
        logger.debug("system_restart(timeout_s=%r)", timeout_s)
        logger.info("Starting reset...")
        self.write(":SYSTem:RESet")
        time.sleep(timeout_s)
        logger.info("Done reset.")

    def auto_scale(self):
        logger.debug("auto_scale()")
        self.write(":AUToset")
        time.sleep(3)

    # ── Channel configuration ──────────────────────────────────────────

    def channel_enable(self, ch: int, on: bool = True):
        logger.debug("channel_enable(ch=%r, on=%r)", ch, on)
        self._check_ch(ch)
        self.write(f":CHANnel{ch}:DISPlay {'ON' if on else 'OFF'}")

    def channel_scale(self, ch: int, volts_per_div: float):
        """Set vertical scale (V/div)."""
        logger.debug("channel_scale(ch=%r, volts_per_div=%r)", ch, volts_per_div)
        self._check_ch(ch)
        self.write(f":CHANnel{ch}:SCALe {volts_per_div}")

    def channel_offset(self, ch: int, volts: float):
        logger.debug("channel_offset(ch=%r, volts=%r)", ch, volts)
        self._check_ch(ch)
        self.write(f":CHANnel{ch}:OFFSet {volts}")

    def channel_coupling(self, ch: int, mode: str = "DC"):
        """Set coupling: DC, AC, or GND."""
        logger.debug("channel_coupling(ch=%r, mode=%r)", ch, mode)
        self._check_ch(ch)
        self.write(f":CHANnel{ch}:COUPling {mode.upper()}")

    def channel_probe(self, ch: int, ratio: float = 10.0):
        """Set probe attenuation ratio (1, 10, 100, etc.)."""
        logger.debug("channel_probe(ch=%r, ratio=%r)", ch, ratio)
        self._check_ch(ch)
        self.write(f":CHANnel{ch}:PROBe {ratio}")

    def channel_bwlimit(self, ch: int, on: bool = True):
        """Enable 20 MHz bandwidth limit."""
        logger.debug("channel_bwlimit(ch=%r, on=%r)", ch, on)
        self._check_ch(ch)
        self.write(f":CHANnel{ch}:BWLimit {'20M' if on else 'OFF'}")

    def get_channel_config(self, ch: int) -> dict:
        logger.debug("get_channel_config(ch=%r)", ch)
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
        logger.debug("timebase_scale(seconds_per_div=%r)", seconds_per_div)
        self.write(f":TIMebase:MAIN:SCALe {seconds_per_div}")

    def timebase_offset(self, seconds: float):
        logger.debug("timebase_offset(seconds=%r)", seconds)
        self.write(f":TIMebase:MAIN:OFFSet {seconds}")

    def get_timebase(self) -> dict:
        logger.debug("get_timebase()")
        return {
            "scale_s": float(self.query(":TIMebase:MAIN:SCALe?")),
            "offset_s": float(self.query(":TIMebase:MAIN:OFFSet?")),
        }

    # ── Trigger ────────────────────────────────────────────────────────

    def trigger_edge(self, ch: int = 1, level: float = 0.0, slope: str = "POS"):
        """Configure edge trigger. slope: POS, NEG, RFAL."""
        logger.debug("trigger_edge(ch=%r, level=%r, slope=%r)", ch, level, slope)
        self._check_ch(ch)
        self.write(":TRIGger:MODE EDGE")
        self.write(f":TRIGger:EDGE:SOURce CHANnel{ch}")
        self.write(f":TRIGger:EDGE:SLOPe {slope.upper()}")
        self.write(f":TRIGger:EDGE:LEVel {level}")

    def trigger_level(self, level: float):
        logger.debug("trigger_level(level=%r)", level)
        self.write(f":TRIGger:EDGE:LEVel {level}")

    def single_trigger_with_verify(self, timeout: float = 30.0, poll_interval: float = 0.2) -> bool:
        """Arm a single acquisition, confirming the sweep mode actually latches.

        Setting :TRIGger:SWEep SINGle both pins the sweep mode and arms the
        single acquisition — no separate :SINGle is needed. But the write
        doesn't reliably land in one shot: if the scope is still busy (e.g.
        settling from a large timebase/deep memory acquisition), it can be
        silently dropped with no SCPI error, leaving :TRIGger:SWEep? reading
        AUTO. Confirmed on hardware: without retrying, trigger_status() got
        stuck cycling TD -> AUTO -> RUN instead of latching to STOP after one
        trigger.

        Returns:
            True once :TRIGger:SWEep SINGle is confirmed via read-back, False
            if it never confirmed within `timeout` seconds.
        """
        logger.debug(
            "single_trigger_with_verify(timeout=%r, poll_interval=%r)", timeout, poll_interval
        )
        deadline = time.time() + timeout
        attempts = 0
        while time.time() < deadline:
            attempts += 1
            self.write(":TRIGger:SWEep SINGle")
            mode = self.query_safe(":TRIGger:SWEep?").strip().upper()
            if mode == "SING":
                logger.info("sweep mode confirmed SINGle after %d attempt(s)", attempts)
                return True
            time.sleep(poll_interval)

        logger.warning(
            "TIMED OUT after %d attempt(s) — sweep mode never confirmed SINGle", attempts
        )
        return False

    def trigger_force(self):
        logger.debug("trigger_force()")
        self.write(":TFORce")

    def trigger_status(self) -> str:
        logger.debug("trigger_status()")
        return self.query(":TRIGger:STATus?")

    def wait_for_trigger_stop(self, timeout: float = 30.0, poll_interval: float = 0.05) -> None:
        """Block until trigger_status() reports STOP.

        On timeout, this issues a full system_restart() (a disruptive scope
        reset) before raising — matches the stuck-trigger recovery procedure
        validated on bench hardware, where a hung acquisition wouldn't clear
        on its own.

        Raises:
            TimeoutError: if STOP is not reached within `timeout` seconds
                (after triggering a system_restart()).
        """
        logger.debug("wait_for_trigger_stop(timeout=%r, poll_interval=%r)", timeout, poll_interval)
        deadline = time.monotonic() + timeout
        while self.trigger_status() != "STOP":
            if time.monotonic() > deadline:
                logger.error("no trigger within %s s — restarting scope", timeout)
                self.system_restart(timeout_s=timeout)
                raise TimeoutError(f"no trigger within {timeout} s")
            time.sleep(poll_interval)

    def trigger_holdoff(self, seconds: float):
        """Set trigger holdoff time in seconds (range: 8 ns to 10 s, default 8 ns).

        Holdoff is the time the scope waits before re-arming the trigger
        after a trigger event — useful for stably triggering complex
        repetitive waveforms. Not available for Video, Timeout, Setup&Hold,
        Nth Edge, RS232, I2C, SPI, CAN, FlexRay, LIN, I2S, or 1553B triggers.
        """
        logger.debug("trigger_holdoff(seconds=%r)", seconds)
        self.write(f":TRIGger:HOLDoff {seconds}")

    def get_trigger_holdoff(self) -> float:
        """Query the current trigger holdoff time in seconds."""
        logger.debug("get_trigger_holdoff()")
        return float(self.query(":TRIGger:HOLDoff?"))

    # ── Run/Stop ───────────────────────────────────────────────────────

    def run(self):
        logger.debug("run()")
        self.write(":RUN")

    def stop(self):
        logger.debug("stop()")
        self.write(":STOP")

    # ── Measurements ───────────────────────────────────────────────────

    def measure(self, ch: int, item: str) -> float:
        """
        Read a measurement.  Common items:
          VPP, VMAX, VMIN, VAMP, VTOP, VBAS, VAVG, VRMS,
          FREQ, PER, PDUT, NDUT, RTIM, FTIM, PWIDth, NWIDth
        """
        logger.debug("measure(ch=%r, item=%r)", ch, item)
        self._check_ch(ch)
        # DHO4000 syntax: first enable, then query
        self.write(f":MEASure:ITEM {item.upper()},CHANnel{ch}")
        time.sleep(0.3)
        val = self.query_safe(
            f":MEASure:ITEM? {item.upper()},CHANnel{ch}", default="9.9E37"
        )
        try:
            return float(val)
        except ValueError:
            logger.warning("measure(ch=%r, item=%r) got non-numeric %r", ch, item, val)
            return float("nan")

    def measure_all(self, ch: int) -> dict:
        """Grab common measurements for a channel."""
        logger.debug("measure_all(ch=%r)", ch)
        items = ["VPP", "VMAX", "VMIN", "VRMS", "FREQ", "PER", "RTIM", "FTIM"]
        return {item: self.measure(ch, item) for item in items}

    # ── Acquisition (memory depth / sample rate) ────────────────────────

    def acquire_memory_depth(self, depth, timeout: float = 5.0) -> bool:
        """Set memory (record) depth, e.g. 1000, '1M', '10M', or 'AUTO'.

        Verified via read-back, because the scope only accepts a discrete set
        of depths (and the legal maximum halves as more channels are turned
        on). An out-of-range or non-discrete request is snapped to a legal
        value silently, with no SCPI error — and every downstream
        :WAVeform:POINts/:STOP request is then capped at whatever actually
        took effect. Returns True if the read-back confirms the request.
        """
        logger.debug("acquire_memory_depth(depth=%r, timeout=%r)", depth, timeout)
        try:
            match = _numeric_match(float(depth))
        except (TypeError, ValueError):
            match = str(depth)  # e.g. "AUTO", or a suffixed form like "10M"
        return self.write_verified(
            f":ACQuire:MDEPth {depth}", ":ACQuire:MDEPth?", match, timeout=timeout
        )

    def get_memory_depth(self) -> float:
        """Query current memory depth in points. Returns NaN if the scope reports AUTO."""
        logger.debug("get_memory_depth()")
        val = self.query_safe(":ACQuire:MDEPth?", default="")
        try:
            return float(val)
        except ValueError:
            logger.debug("get_memory_depth() non-numeric %r — returning NaN", val)
            return float("nan")

    def sample_rate(self) -> float:
        """Query the current sample rate (samples/sec). Read-only — derived from timebase and memory depth."""
        logger.debug("sample_rate()")
        return float(self.query(":ACQuire:SRATe?"))

    def get_acquire_config(self) -> dict:
        logger.debug("get_acquire_config()")
        return {
            "memory_depth": self.get_memory_depth(),
            "sample_rate_Sps": self.sample_rate(),
        }

    # ── Waveform data acquisition ──────────────────────────────────────

    def _acquisition_settle_s(self) -> float:
        """Seconds to wait for the scope to finish processing an acquisition.

        Confirmed on hardware: even after trigger_status() reports STOP, the
        scope isn't actually done — it needs roughly one full capture window
        (timebase scale x NUM_HORIZONTAL_DIVS) to finish processing the
        acquisition internally before the waveform buffer is ready to read
        out. Reading too soon returns an empty array for every channel, with
        no exception raised anywhere in the chain. This is a property of the
        shared acquisition/memory buffer, not of any one channel, so callers
        reading multiple channels from the same acquisition only need to pay
        this once.
        """
        logger.debug("_acquisition_settle_s()")
        timebase_scale_s = self.get_timebase()["scale_s"]
        settle_s = max(0.5, timebase_scale_s * self.NUM_HORIZONTAL_DIVS)
        logger.debug("_acquisition_settle_s() -> %r s", settle_s)
        return settle_s

    def get_waveform(
        self,
        ch: int,
        mode: str = "NORMal",
        points: int = 1000,
        setup_timeout: float = 10.0,
        settle: bool = True,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Download waveform data from a channel.

        Args:
            ch:     Channel number (1-4).
            mode:   NORMal (screen, max 1000 pts), MAXimum/RAW (full memory,
                    clamped to current memory depth — see acquire_memory_depth()).
            points: Number of points to request.
            setup_timeout: Max seconds to retry each :WAVeform:* setup write
                    until its read-back confirms it took effect (see notes).
            settle: If True (default), sleep for _acquisition_settle_s()
                    after stopping before reading. Pass False when the
                    caller (e.g. get_waveforms()) has already settled once
                    for the same acquisition — the delay isn't per-channel.

        Returns:
            (time_array, voltage_array) as numpy arrays.

        Notes:
            For MAXimum/RAW mode, :WAVeform:STARt/:STOP are set to span the
            requested points — per the programming guide's documented read
            procedure, these (not POINts alone) define the memory window
            :WAVeform:DATA? actually returns. Without them, a stale
            STARt/STOP range left over from a previous session can cause
            DATA? to return empty with no exception raised.

            Every :WAVeform:* setup write (SOURce, MODE, POINts, STARt, STOP)
            is issued via write_verified() rather than a plain write().
            Confirmed on hardware: this scope can silently drop a write while
            it's still busy settling from a previous operation (e.g. a large
            timebase/deep-memory acquisition), with no SCPI error surfaced —
            a dropped POINts/STARt/STOP here leaves the read window
            misconfigured and :WAVeform:DATA? comes back empty with no
            exception. The trailing *OPC? sync happens after all setup
            writes, which doesn't help if an earlier write in the sequence
            was already dropped.

            See _acquisition_settle_s() for why a post-stop settle delay is
            needed at all.

            Prefers a single query_binary_values() bulk read, which parses the
            IEEE-488.2 block header length up front and works over USB and
            LAN/socket alike. Falls back to a manual chunked read_raw loop
            only if that fails — a workaround for libusb0 on Windows, which
            times out on large single bulk reads.
        """
        logger.debug(
            "get_waveform(ch=%r, mode=%r, points=%r, setup_timeout=%r, settle=%r)",
            ch,
            mode,
            points,
            setup_timeout,
            settle,
        )
        self._check_ch(ch)

        # Scope must be stopped for reliable waveform reads on DHO4000
        self.stop()
        if settle:
            time.sleep(self._acquisition_settle_s())

        mode_upper = mode.upper()
        self.write_verified(
            f":WAVeform:SOURce CHANnel{ch}", ":WAVeform:SOURce?", f"CHAN{ch}", timeout=setup_timeout
        )
        self.write_verified(
            f":WAVeform:MODE {mode_upper}", ":WAVeform:MODE?", mode_upper, timeout=setup_timeout
        )
        self.write(":WAVeform:FORMat BYTE")

        # Clamp points to the mode's valid range: NORMal is capped at the
        # 1000-point screen buffer; MAXimum/RAW are capped at memory depth.
        if mode_upper in ("NORMAL", "NORM"):
            points = min(max(1, points), 1000)
        else:
            max_depth = self.get_memory_depth()
            points = max(1, points) if np.isnan(max_depth) else min(max(1, points), int(max_depth))
        logger.debug("get_waveform: clamped points=%r for mode %r", points, mode_upper)
        self.write_verified(
            f":WAVeform:POINts {points}",
            ":WAVeform:POINts?",
            _numeric_match(points),
            timeout=setup_timeout,
        )

        # RAW/MAX reads pull from internal memory — STARt/STOP (not POINts)
        # define the actual returned data window, per the programming manual's
        # documented read procedure.
        if mode_upper not in ("NORMAL", "NORM"):
            self.write_verified(
                ":WAVeform:STARt 1", ":WAVeform:STARt?", _numeric_match(1), timeout=setup_timeout
            )
            self.write_verified(
                f":WAVeform:STOP {points}",
                ":WAVeform:STOP?",
                _numeric_match(points),
                timeout=setup_timeout,
            )

        # Sync — wait for scope to acknowledge settings
        self.query("*OPC?")
        time.sleep(0.3)

        old_timeout = self.inst.timeout
        old_chunk = self.inst.chunk_size

        # Generous enough for the preamble/data-request round trip on large
        # MAXimum/RAW transfers, which are slower for the scope to prepare.
        self.inst.timeout = 5000
        self.inst.chunk_size = 4096

        try:
            # Read preamble for scaling
            preamble = self.query(":WAVeform:PREamble?").split(",")
            x_inc = float(preamble[4])
            x_orig = float(preamble[5])
            y_inc = float(preamble[7])
            y_orig = float(preamble[8])
            y_ref = float(preamble[9])

            try:
                data = self.inst.query_binary_values(
                    ":WAVeform:DATA?", datatype="B", container=np.array, header_fmt="ieee"
                )
            except Exception as exc:
                # Fallback: pull the data in small chunks. Needed for
                # backends (e.g. libusb0 on Windows) that time out on
                # large single bulk reads.
                logger.warning("bulk read failed (%s) — falling back to chunked read", exc)
                try:
                    self.inst.clear()
                except Exception:
                    pass
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

            logger.debug("get_waveform(ch=%r) -> %d points", ch, len(voltage))
            if len(voltage) < points:
                # Usually means the record in memory is shorter than the
                # requested window — memory depth didn't take, or the
                # acquisition was stopped part-way through its sweep.
                logger.warning(
                    "get_waveform(ch=%d): requested %d points, scope returned %d",
                    ch,
                    points,
                    len(voltage),
                )
            return time_arr, voltage
        finally:
            self.inst.timeout = old_timeout
            self.inst.chunk_size = old_chunk

    def plot_waveform(self, ch: int, save_path: str | None = None):
        """Capture and plot a waveform using matplotlib."""
        logger.debug("plot_waveform(ch=%r, save_path=%r)", ch, save_path)
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
            logger.info("Saved plot: %s", save_path)
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
        logger.debug("get_waveforms(channels=%r, mode=%r, points=%r)", channels, mode, points)
        if channels is None:
            channels = list(self.CHANNELS)

        # Settle once for the whole acquisition rather than per channel —
        # see _acquisition_settle_s(); it's a property of the shared memory
        # buffer, not any individual channel.
        self.stop()
        time.sleep(self._acquisition_settle_s())

        waveforms = {}
        for ch in channels:
            try:
                t, v = self.get_waveform(ch, mode=mode, points=points, settle=False)
                waveforms[ch] = (t, v)
                logger.info("Fetched waveform from Channel %d (%d points)", ch, len(v))
            except Exception as e:
                logger.error("Failed to fetch waveform from Channel %d: %s", ch, e)

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
        logger.debug("save_waveform_csv(filepath=%r, ch=%r)", filepath, ch)
        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([f"Channel {ch} Waveform"])
            writer.writerow(["Time (s)", "Voltage (V)"])
            for t, v in zip(time_data, voltage_data):
                writer.writerow([f"{t:.15e}", f"{v:.6f}"])
        logger.info("Saved waveform CSV: %s", filepath)

    def save_waveforms_csv(self, filepath: str, waveforms: dict[int, tuple[np.ndarray, np.ndarray]]):
        """
        Save multiple waveforms to a single CSV file.

        Args:
            filepath:   Path to save CSV file.
            waveforms:  Dictionary {channel: (time_array, voltage_array)}.
        """
        logger.debug(
            "save_waveforms_csv(filepath=%r, channels=%r)", filepath, sorted(waveforms.keys())
        )
        if not waveforms:
            logger.warning("No waveforms to save.")
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

        logger.info("Saved multi-channel waveforms CSV: %s", filepath)

    def plot_waveforms(
        self,
        channels: list[int] | None = None,
        mode: str = "NORMal",
        points: int = 1000,
        save_path: str | None = None,
        normalise: bool = False,
    ):
        """
        Capture and plot waveforms from multiple channels.

        Args:
            channels:   List of channel numbers (1-4). If None, use all active channels.
            mode:       NORMal (screen), MAXimum (full memory), RAW.
            points:     Number of points to request (NORMal max: 1000).
            save_path:  Optional path to save the figure.
            normalise:  If True, scale each channel by its own max absolute
                        voltage so every trace peaks at ±1.
        """
        logger.debug(
            "plot_waveforms(channels=%r, mode=%r, points=%r, save_path=%r, normalise=%r)",
            channels,
            mode,
            points,
            save_path,
            normalise,
        )
        import matplotlib.pyplot as plt

        waveforms = self.get_waveforms(channels=channels, mode=mode, points=points)

        if not waveforms:
            logger.warning("No waveforms to plot.")
            return

        fig, ax = plt.subplots(figsize=(14, 6))

        colours = ["C0", "C1", "C2", "C3"]  # matplotlib default colours
        for idx, (ch, (t, v)) in enumerate(sorted(waveforms.items())):
            if normalise:
                max_v = np.max(np.abs(v))
                v = v / max_v if max_v > 0 else v
            ax.plot(
                t * 1e6,
                v,
                linewidth=0.7,
                rasterized=True,
                label=f"CH{ch}",
                color=colours[idx % len(colours)],
            )

        ax.set_xlabel("Time (µs)")
        ax.set_ylabel("Normalised Voltage" if normalise else "Voltage (V)")
        ax.set_title("DHO4204 — Multi-channel Waveforms")
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper right")

        if save_path:
            fig.savefig(save_path, dpi=300, bbox_inches="tight")
            logger.info("Saved plot: %s", save_path)
        else:
            plt.show()
        plt.close(fig)

    # ── Screenshot ──────────────────────────────────────────────────

    def screenshot(self, filepath: str = "screenshot.png"):
        """Download a screenshot from the oscilloscope."""
        logger.debug("screenshot(filepath=%r)", filepath)
        self.write(":DISPlay:DATA? PNG")
        raw = self.inst.read_raw()
        # Strip TMC header
        header_len = 2 + int(chr(raw[1]))
        img_data = raw[header_len:]
        # Remove trailing newline if present
        if img_data[-1:] == b"\n":
            img_data = img_data[:-1]
        Path(filepath).write_bytes(img_data)
        logger.info("Screenshot saved: %s", filepath)

    # ── Cursor measurements ──────────────────────────────────────────

    def cursor_manual(self, ch: int, xa: float, xb: float):
        """Set manual time cursors."""
        logger.debug("cursor_manual(ch=%r, xa=%r, xb=%r)", ch, xa, xb)
        self._check_ch(ch)
        self.write(":CURSor:MODE MANual")
        self.write(f":CURSor:MANual:SOURce CHANnel{ch}")
        self.write(":CURSor:MANual:TYPE TIME")
        self.write(f":CURSor:MANual:CAX {xa}")
        self.write(f":CURSor:MANual:CBX {xb}")

    # ── Math / FFT ─────────────────────────────────────────────────

    def math_fft(self, ch: int = 1, math_ch: int = 1):
        """Enable FFT on a channel."""
        logger.debug("math_fft(ch=%r, math_ch=%r)", ch, math_ch)
        self._check_ch(ch)
        if math_ch not in (1, 2, 3, 4):
            raise ValueError(f"Invalid math channel {math_ch}. Must be 1-4.")
        self.write(f":MATH{math_ch}:DISPlay ON")
        self.write(f":MATH{math_ch}:OPERator FFT")
        self.write(f":MATH{math_ch}:SOURce1 CHANnel{ch}")

    # ── Save/Recall ────────────────────────────────────────────────

    def save_setup(self) -> bytes:
        """Read the current setup as a binary data blob (to save externally)."""
        logger.debug("save_setup()")
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
        logger.debug("recall_setup(%d bytes)", len(setup_data))
        # Build TMC/IEEE 488.2 block header: #N<length_digits><data>
        length_str = str(len(setup_data))
        header = f"#{ len(length_str)}{length_str}".encode()
        self.inst.write_raw(b":SYSTem:SETup " + header + setup_data)
        time.sleep(1)

    # ── Utility ──────────────────────────────────────────────────────

    def _check_ch(self, ch: int):
        logger.debug("_check_ch(ch=%r)", ch)
        if ch not in self.CHANNELS:
            raise ValueError(f"Invalid channel {ch}. Must be one of {self.CHANNELS}")

    def close(self):
        """Release the VISA session cleanly. Safe to call multiple times."""
        logger.debug("close()")
        if self.inst is None:
            return
        for action in (self.inst.clear, self.inst.close, self.rm.close):
            try:
                action()
            except Exception:
                pass
        # Let the USBTMC endpoint fully release before the process exits —
        # on Windows/NI-VISA it doesn't drop the lock immediately, which
        # otherwise causes VI_ERROR_RSRC_NFOUND on the next open_resource().
        time.sleep(1)
        self.inst = None
        self.rm = None
        logger.info("VISA session closed")

    def reconnect(self, retries: int = 5, base_delay: float = 1.0) -> str:
        """Force a full VISA session teardown and reopen, retrying with
        backoff until the device responds to *IDN? or the retry budget runs out.

        Used when open_resource() (or a query right after it) fails with a
        stale USBTMC session lock (VI_ERROR_RSRC_NFOUND / VI_ERROR_IO) left
        behind by a previous session — the OS-level release isn't always
        done clearing after a single settle delay, so a single retry isn't
        reliable. Uses the resource string and timeout stored at __init__
        time. Returns the *IDN? response on success.
        """
        logger.debug("reconnect(retries=%r, base_delay=%r)", retries, base_delay)
        if self.inst is not None:
            try:
                self.inst.clear()
            except Exception:
                pass
            try:
                self.inst.close()
            except Exception:
                pass
        if self.rm is not None:
            try:
                self.rm.close()
            except Exception:
                pass
        self.inst = None
        self.rm = None

        last_exc = None
        for attempt in range(1, retries + 1):
            logger.debug("reconnect attempt %d/%d", attempt, retries)
            time.sleep(base_delay * attempt)
            try:
                self.rm = pyvisa.ResourceManager()
                self.inst = self.rm.open_resource(self._resource_string)
                self.inst.timeout = self._timeout_ms
                if "SOCKET" in self._resource_string.upper():
                    self.inst.read_termination = "\n"
                    self.inst.write_termination = "\n"
                try:
                    self.inst.clear()
                except Exception:
                    pass
                return self.idn()
            except pyvisa.errors.VisaIOError as exc:
                logger.warning("reconnect attempt %d failed: %s", attempt, exc)
                last_exc = exc
                try:
                    if self.inst is not None:
                        self.inst.close()
                except Exception:
                    pass
                try:
                    if self.rm is not None:
                        self.rm.close()
                except Exception:
                    pass
                self.inst = None
                self.rm = None

        raise ConnectionError(
            f"Could not reconnect to {self._resource_string} after {retries} attempts"
        ) from last_exc

    def __enter__(self):
        logger.debug("__enter__()")
        return self

    def __exit__(self, *_):
        logger.debug("__exit__()")
        self.close()
