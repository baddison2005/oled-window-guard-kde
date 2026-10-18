"""Physical keyboard fallback with a visible non-focusing instruction client."""
import json
import time

import direct_output_scope as scope
from direct_warning import publish_instruction
from run_input_direct import verify_continuity, verify_environment


def prepare_fixture(directory):
    """A private, exact-app rule; Qt's client-side flag alone is insufficient."""
    config = directory / 'config'
    if not config.resolve().is_relative_to('/tmp'):
        raise RuntimeError('Unknown-focus fixture rules require a private /tmp configuration')
    config.mkdir(exist_ok=True)
    (config / 'kwinrulesrc').write_text(
        '[General]\ncount=1\nrules=1\n\n[1]\n'
        'Description=OWG private non-focusing instruction fixture\n'
        'wmclass=oled-window-guard-unknown-focus-test\nwmclassmatch=1\nwmclasscomplete=false\n'
        'acceptfocus=false\nacceptfocusrule=2\n')


def verify_focus(before, after):
    for state in (before, after):
        if (state.get('focusKnown') is not False or
                type(state.get('focusGeneration')) is not int or state['focusGeneration'] < 0 or
                type(state.get('unknownFocusKeyboard')) is not int or state['unknownFocusKeyboard'] < 0):
            raise RuntimeError('Unknown application focus was not established')
    if before['focusGeneration'] != after['focusGeneration']:
        raise RuntimeError('Application focus changed during the unknown-focus check')
    if after['unknownFocusKeyboard'] < before['unknownFocusKeyboard']:
        raise RuntimeError('Unknown-focus keyboard counter regressed')


def keyboard_result(before, after, origin_before, origin_after, client_keys):
    verify_focus(before, after)
    if type(client_keys) is not int or client_keys != 0:
        raise RuntimeError('Non-focusing instruction client unexpectedly received keyboard input')
    expected = list(scope.output_counts(before))
    result = scope.keyboard_result(before, after, origin_before, origin_after,
                                   client_keys, expected, require_client=False)
    if result is None:
        return None
    unknown = after['unknownFocusKeyboard'] - before['unknownFocusKeyboard']
    if unknown != result['observerKeyboard']:
        raise RuntimeError('Keyboard callbacks were not entirely observed without application focus')
    if result['observerKeyboard'] < 4:
        return None
    return dict(result, unknownFocusKeyboard=unknown, focusKnown=False,
                focusGeneration=after['focusGeneration'], clientDeliveryRequired=False)


def write_progress(directory, progress):
    temporary = directory / 'unknown-focus-progress.tmp'
    temporary.write_text(json.dumps(progress, indent=2) + '\n')
    temporary.replace(directory / 'unknown-focus-progress.json')


def observe(directory, env, kwin, client, seconds, vt):
    end = time.monotonic() + seconds

    def alive():
        if kwin.poll() is not None or client.poll() is not None:
            raise RuntimeError('Unknown-focus compositor/instruction client exited')
        if time.monotonic() >= end:
            raise RuntimeError('Physical unknown-focus keyboard check timed out')

    baseline = None
    while baseline is None:
        alive()
        sample = scope.coherent_state(env)
        if sample is None:
            time.sleep(.06)
            continue
        baseline, origin_before, outputs = sample
    progress = {'stage': 'initialization', 'baseline': baseline, 'originBefore': origin_before,
                'outputGeometries': outputs}
    try:
        verify_environment(origin_before, vt)
        verify_focus(baseline, baseline)
        scope.output_counts(baseline)
    except Exception as exc:
        progress.update(validation='failed', error=str(exc))
        write_progress(directory, progress)
        raise
    progress['validation'] = 'passed'
    write_progress(directory, progress)
    path = directory / 'client.log'
    keys_before = path.read_text(errors='replace').count('CLIENT_EVENT keyboard')
    root = baseline
    publish_instruction(directory, 'TYPE NOW: press and release an ordinary key twice.\n'
                        'Keep the mouse still; do not click.\n'
                        'This instruction window must stay unfocused.')
    print('DIRECT_UNKNOWN_FOCUS_READY: type two ordinary keys; do not click', flush=True)
    result = None
    while result is None:
        alive()
        sample = scope.coherent_state(env)
        if sample is None:
            time.sleep(.06)
            continue
        current, origin, geometry = sample
        keys = path.read_text(errors='replace').count('CLIENT_EVENT keyboard') - keys_before
        progress = {'baseline': baseline, 'current': current, 'originBefore': origin_before,
                    'origin': origin, 'outputGeometries': geometry, 'clientKeyboardNotifications': keys}
        try:
            verify_environment(origin, vt)
            verify_continuity(root, current)
            verify_focus(root, current)
            if geometry != outputs:
                raise RuntimeError('Output geometry changed during unknown-focus keyboard check')
            try:
                result = keyboard_result(baseline, current, origin_before, origin, keys)
            except scope.NonKeyboardInput:
                baseline, origin_before = current, origin
                keys_before += keys
                publish_instruction(directory, 'Mouse input detected: keep it still and do not click.\n'
                                    'TYPE NOW: press and release an ordinary key twice again.')
            progress.update(validation='passed', result=result)
        except Exception as exc:
            progress.update(validation='failed', error=str(exc))
            raise
        finally:
            write_progress(directory, progress)
        time.sleep(.08)
    publish_instruction(directory, 'Physical unknown-focus keyboard fallback passed. Checking cleanup.')
    return {'unknownFocus': dict(result, outputs=outputs), 'environment': origin}
