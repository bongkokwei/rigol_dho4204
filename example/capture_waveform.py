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
    scope.channel_enable(1, True)
    scope.channel_enable(2, True)
    scope.channel_scale(1, 1.0)
    scope.channel_scale(2, 2.0)

    t, data = scope.capture(
        duration=50.0,
        points=10_000_000,
        channels=[1, 2],
        wait_for_trigger=False,
        trigger_timeout=600,
    )
    scope.screenshot("screenshot.png")
    scope.system_restart()

fig, ax = plt.subplots(len(data), 1, sharex=True)

for i, (ch, y) in enumerate(data.items()):
    ax[i].plot(t, y)
    ax[i].set_ylabel(f"Channel {ch}")
    ax[i].grid(True)
plt.xlabel("Time (s)")
plt.show()
