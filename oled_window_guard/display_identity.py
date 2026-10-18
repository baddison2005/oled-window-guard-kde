"""Rebind saved selections only to an unambiguous serialized monitor identity."""
from __future__ import annotations
import copy
import json


def monitor_key(identity):
    try:
        fields = json.loads(identity)
    except (ValueError, TypeError):
        return None
    if (not isinstance(fields, list) or len(fields) != 4 or
            not all(isinstance(v, str) for v in fields) or not fields[1] or not fields[2] or
            fields[3].strip().lower() in ('', '0', 'unknown', 'none', 'n/a')):
        return None
    return tuple(fields[1:])


def rebind_displays(values, outputs):
    """Return copied settings and port changes; never infer identity from a port."""
    connected = {row['id'] for row in outputs}
    candidates = {}
    for identity in connected:
        key = monitor_key(identity)
        if key:
            candidates.setdefault(key, []).append(identity)
    known = set(values['movementDisplays'] + values['dimmingDisplays'])
    for kind in ('movementOverrides', 'dimmingOverrides'):
        known.update(values[kind])
    sources = {}
    for identity in known - connected:
        key = monitor_key(identity)
        if key:
            sources.setdefault(key, []).append(identity)
    mapping = {}
    for key, old in sources.items():
        new = candidates.get(key, [])
        if len(old) != 1 or len(new) != 1:
            continue
        previous, current = old[0], new[0]
        if any(previous in values[kind] and current in values[kind] and
               values[kind][previous] != values[kind][current]
               for kind in ('movementOverrides', 'dimmingOverrides')):
            continue
        mapping[previous] = current
    result = copy.deepcopy(values)
    if mapping:
        for kind in ('movementDisplays', 'dimmingDisplays'):
            result[kind] = list(dict.fromkeys(mapping.get(v, v) for v in result[kind]))
        for kind in ('movementOverrides', 'dimmingOverrides'):
            for old, new in mapping.items():
                if old in result[kind]:
                    result[kind][new] = result[kind].pop(old)
    return result, mapping
