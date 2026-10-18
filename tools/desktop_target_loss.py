"""Close only disposable fixtures, with no operator input during the warning."""
import json
import os
import time
import uuid

FIXTURE_RECEIPT="/tmp/owg-desktop-target-loss-2026-10-09.jsonl"


def check_fixture_scope(receipt,output):
    rows=[json.loads(line) for line in receipt.read_text().splitlines()]
    windows=rows[-1]["windows"]
    if sum(w.get("visible") is True and w.get("screen")==output for w in windows)!=2:
        raise RuntimeError("Expected two visible disposable fixtures on target output")
    if sum(w.get("visible") is True and w.get("screen")!=output for w in windows)!=2:
        raise RuntimeError("Expected two visible disposable fixtures on other output")


def close_targets(receipt,output,record,clock=time.monotonic,sleep=time.sleep):
    request_id=uuid.uuid4().hex
    command=receipt.with_suffix(".close-output")
    ack=receipt.with_suffix(".close-ack")
    # Exclusive creation prevents replacing a pending fixture command.
    temporary=command.with_name(command.name+"."+request_id)
    try:
        with temporary.open("x") as stream:
            json.dump({"requestId":request_id,"output":output},stream)
        os.link(temporary,command)
    finally:
        temporary.unlink(missing_ok=True)
    record("target-close-request",{"requestId":request_id,"output":output})
    deadline=clock()+2
    while clock()<deadline:
        if ack.exists():
            result=json.loads(ack.read_text())
            if result.get("requestId")==request_id:
                record("target-close-ack",result)
                if result.get("ok") is not True or result.get("closed")!=2 or result.get("output")!=output:
                    raise RuntimeError("Disposable target closure refused")
                return
        sleep(.1)
    raise RuntimeError("Disposable target closure acknowledgement timed out")
