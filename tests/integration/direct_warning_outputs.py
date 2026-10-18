"""Sequential two-output physical warning checks, with fresh per-output input."""
import hashlib
import json
import math
import time

import direct_output_scope as placement
from direct_warning import HIDDEN, PREFIX, SHOWN, SKIP, WarningCheck, publish_instruction
from run_input_direct import KINDS, verify_continuity, verify_environment, verify_result


def warning_placement(log, output, outputs):
    def record(kind):
        marker = PREFIX + kind + '_JSON '
        rows = [line.split(marker, 1)[1] for line in log.splitlines() if marker in line]
        if len(rows) != 1:
            raise RuntimeError('Missing or duplicate warning output/placement record')
        return json.loads(rows[0])
    actual_output, frame = record('OUTPUT'), record('PLACEMENT')
    if actual_output != output:
        raise RuntimeError('Warning appeared on the wrong output, geometry or scale')
    desired = {'x': math.floor(output['x'] + (output['width'] - 390) / 2 + .5),
               'y': math.floor(output['y'] + 12 + .5), 'width': 390, 'height': 126}
    if (any(type(frame.get(k)) not in (int, float) or not math.isfinite(frame[k])
            or abs(frame[k] - desired[k]) > 1 for k in desired) or
            placement.intersections(frame, outputs) != {output['id']}):
        raise RuntimeError('Warning frame is not contained at its expected output position')
    return frame


class StageWarning(WarningCheck):
    def __init__(self, directory, env, index, name):
        self.label = 'Stage ' + str(index + 1) + '/2: ' + name
        offset = len((directory / 'kwin.log').read_text(errors='replace'))
        after_skip = 'After Skip, the owned window moves to the other monitor.' if index == 0 else 'The test closes automatically afterward.'
        super().__init__(directory, env, log_start=offset, after_skip=after_skip)

    def instruction(self, message):
        publish_instruction(self.directory, self.label + '\n' + message)


def client_counts(directory):
    log = (directory / 'client.log').read_text(errors='replace')
    return {kind: log.count('CLIENT_EVENT ' + kind) for kind in KINDS}


def complete_input(before, current, origin_before, origin, counts, held, released):
    # Do not arm on a read that saw only the client/origin side of a new event.
    return (held and released and current.get('buttonKnown') is True and current.get('buttonsDown') is False
            and all(current[k] > before[k] and origin['libinput'][k] > origin_before['libinput'][k]
                    and counts[k] > 0 for k in KINDS))


def validate_totals(log, stages):
    if len(stages) != 2 or len({stage['output']['id'] for stage in stages}) != 2:
        raise RuntimeError('Two distinct warning stages must pass')
    if PREFIX + 'FAIL ' in log or any(log.count(marker) != 2 for marker in (SHOWN, SKIP, HIDDEN)):
        raise RuntimeError('Two-output warning lifecycle is incomplete, duplicated or failed')
    ordered = sorted((index, marker) for marker in (SHOWN, SKIP, HIDDEN)
                     for index in (log.index(marker), log.rindex(marker)))
    if [marker for _, marker in ordered] != [SHOWN, SKIP, HIDDEN] * 2:
        raise RuntimeError('Two warning lifecycles overlapped or were reordered')
    for stage in stages:
        warning = stage['warning']
        if (stage.get('placementScriptUnloaded') is not True or
                warning.get('warningCount') != 1 or warning.get('hiddenSurfaceCount') != 0 or
                any(warning.get(k) is not True for k in ('outsideInputDelivered', 'pointerSkipDelivered', 'focusUnchanged', 'scriptUnloaded'))):
            raise RuntimeError('Incomplete per-output warning acceptance')


def observe(directory, env, kwin, client, seconds, vt):
    end = time.monotonic() + seconds
    def alive():
        if kwin.poll() is not None or client.poll() is not None:
            raise RuntimeError('Two-output warning test aborted or compositor/client exited')
        if time.monotonic() >= end:
            raise RuntimeError('Physical two-output warning sequence did not complete before the deadline')
        log = (directory / 'kwin.log').read_text(errors='replace')
        if PREFIX + 'FAIL ' in log:
            raise RuntimeError('Warning fixture failed during two-output check')
    def sample():
        while True:
            alive()
            state = placement.coherent_state(env)
            if state is not None:
                current, facts, geometry = state
                verify_environment(facts, vt)
                return current, facts, geometry
            time.sleep(.04)
    root, facts, outputs = sample()
    verify_continuity(root, root)
    stages = placement.contained_stages(outputs, centered=True, top_inset=200)
    results = []
    for index, stage in enumerate(stages):
        publish_instruction(directory, 'Moving the owned window to ' + stage['name'] + '.\nWait for the warning and the input instructions.')
        package_id = placement.load_stage(directory, env, index, stage, outputs)
        ready = None
        while ready is None:
            alive()
            ready = placement.check_log((directory / 'kwin.log').read_text(errors='replace'), index)
            time.sleep(.08)
        owned_frame = placement.validate_frame(ready, stage, outputs)
        check = StageWarning(directory, env, index, stage['name'])
        check.start(kwin, client)
        output = next(row for row in outputs if row['id'] == stage['name'])
        warning_frame = warning_placement(check.log(), output, outputs)
        baseline, origin_before, geometry = sample()
        verify_continuity(root, baseline)
        if geometry != outputs:
            raise RuntimeError('Outputs changed before a warning input baseline')
        before = client_counts(directory)
        check.ready()
        print('DIRECT_WARNING_OUTPUT_READY:', stage['name'], flush=True)
        held = released = False
        outside_evidence = None
        while not check.finished:
            alive()
            placement.check_log((directory / 'kwin.log').read_text(errors='replace'), index)
            current, origin, geometry = sample()
            verify_continuity(root, current)
            if geometry != outputs:
                raise RuntimeError('Output geometry/scale changed during warning input')
            total = client_counts(directory)
            counts = {kind: total[kind] - before[kind] for kind in KINDS}
            held |= current['buttonKnown'] and current['buttonsDown'] and current['button'] > baseline['button']
            released |= held and current['buttonKnown'] and not current['buttonsDown']
            complete = complete_input(baseline, current, origin_before, origin, counts, held, released)
            # Always call advance: an early Skip must fail even with incomplete input.
            check.advance(counts if complete or check.armed else {k: 0 for k in KINDS},
                          held, released, current, origin, origin_before)
            if check.armed and outside_evidence is None:
                outside_evidence = dict(snapshot=current, origin=origin, client=counts,
                                        skipButtonBaseline=check.skip_origin)
            progress = dict(stage=stage['name'], baseline=baseline, originBefore=origin_before,
                            current=current, origin=origin, client=counts, outsideEvidence=outside_evidence,
                            outputs=outputs, completed=results)
            temporary = directory / 'warning-outputs-progress.tmp'
            temporary.write_text(json.dumps(progress, indent=2) + '\n')
            temporary.replace(directory / 'warning-outputs-progress.json')
            time.sleep(.08)
        warning = check.finish()
        current, origin, geometry = sample()
        verify_continuity(root, current)
        if geometry != outputs:
            raise RuntimeError('Outputs changed during warning teardown')
        total = client_counts(directory)
        counts = {kind: total[kind] - before[kind] for kind in KINDS}
        evidence = verify_result(baseline, current, origin_before, origin, counts, held, released)
        placement.check_log((directory / 'kwin.log').read_text(errors='replace'), index)
        placement.unload_stage(env, package_id)
        copied = directory / 'warning-package/contents/ui'
        results.append(dict(output=output, ownedFrame=owned_frame, warningFrame=warning_frame,
                            warning=warning, placementScriptUnloaded=True, baseline=baseline,
                            originBefore=origin_before, final=current, originAfter=origin,
                            outsideEvidence=outside_evidence,
                            warningSourceSHA256=hashlib.sha256((copied/'WarningSurface.qml').read_bytes()).hexdigest(),
                            fixtureSHA256=hashlib.sha256((copied/'main.qml').read_bytes()).hexdigest(), **evidence))
        print('DIRECT_WARNING_OUTPUT_STAGE: PASS', stage['name'], flush=True)
    validate_totals((directory / 'kwin.log').read_text(errors='replace'), results)
    return {'warningOutputs': {'outputs': outputs, 'stages': results, 'sequentialWarnings': True},
            'environment': origin}
