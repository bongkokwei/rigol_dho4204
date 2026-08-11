from rigol_dho4204 import DHO4204
import matplotlib.pyplot as plt
import logging

RESOURCE = "USB0::0x1AB1::0x0610::HDO4A261600266::INSTR"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
log = logging.getLogger("rigol_measure")

with DHO4204(RESOURCE) as scope:
    scope.channel_enable(1, True)  # MZI
    scope.channel_enable(2, True)  # Gas cell
    scope.channel_enable(3, True)  # DUT
    scope.channel_enable(4, True)  # Trigger
    scope.channel_scale(1, 2.0)
    scope.channel_scale(2, 1.0)
    scope.channel_scale(3, 0.05)
    scope.channel_scale(4, 5.0)
    scope.channel_offset(1, -4.0)
    scope.channel_offset(2, -6.0)
    scope.channel_offset(3, -0.1)
    scope.channel_offset(4, 0.0)

    scope.trigger_edge(ch=4, slope="POS", level=0.5)

    t, data = scope.capture(
        duration=20.0,
        points=50_000_000,
        channels=[1, 2, 3, 4],
        wait_for_trigger=False,
        trigger_timeout=600,
        # out="example/capture_waveform.csv",
    )
    scope.screenshot("example/screenshot.png")
    scope.system_restart()

fig, ax = plt.subplots(len(data), 1, sharex=True)

for i, (ch, y) in enumerate(data.items()):
    ax[i].plot(t, y, color=f"C{i}")
    ax[i].set_ylabel(f"Channel {ch}")
    ax[i].grid(True)
plt.xlabel("Time (s)")
plt.show()
