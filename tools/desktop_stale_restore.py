"""Wait for explicit operator geometry change before testing one-shot Restore."""
import time


def request_stale_restore(status,intent,record,confirmation,clock=time.monotonic,sleep=time.sleep,change="move"):
    if change not in ("move","resize"):
        raise ValueError("Unknown manual frame change")
    instruction=("Resize ONE target to a visibly different size, then click 'I resized one window — check Restore'."
                 if change=="resize" else
                 "Drag ONE target to a visibly different position, then click 'I moved one window — check Restore'.")
    record("operator-move-window",instruction)
    deadline=clock()+120
    while not confirmation.exists():
        if clock()>=deadline:
            raise RuntimeError("Operator confirmation timed out; Restore not requested")
        sleep(.1)
    record("operator-frame-change-confirmed",True)
    # Allow the five-second native quiet period after the confirming click.
    sleep(6)
    current=status()
    if current.get("transactionActive") is not False or current.get("restoreAvailable") is not True:
        raise RuntimeError("Restore unavailable after manual movement; stale-record refusal not exercised")
    record("before-stale-restore",current)
    intent("Restore")
