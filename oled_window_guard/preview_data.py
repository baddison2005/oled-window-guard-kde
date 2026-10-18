"""Bounded drawing data and app labels; no window identities, titles or write authority."""
import math


def rectangle(value):
    if not isinstance(value, dict) or set(value) != {'x', 'y', 'width', 'height'}:
        raise ValueError('invalid-preview-rectangle')
    if any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1000000 for v in value.values()):
        raise ValueError('invalid-preview-rectangle')
    if value['width'] <= 0 or value['height'] <= 0:
        raise ValueError('invalid-preview-rectangle')
    return dict(value)


def drawing_preview(value, output_ids, move_count):
    if value is None:
        return None
    if not isinstance(value, dict) or value.get('outputId') not in output_ids:
        raise ValueError('invalid-preview-output')
    windows, moves = value.get('windows'), value.get('destinations')
    if (not isinstance(windows, list) or len(windows) > 32 or not isinstance(moves, list)
            or len(moves) != move_count or len(moves) > 12
            or type(value.get('totalWindows')) is not int or not len(windows) <= value['totalWindows'] <= 256):
        raise ValueError('invalid-preview-drawing')
    destinations = []
    for move in moves:
        if not isinstance(move, dict):
            raise ValueError('invalid-preview-drawing')
        destinations.append({'from': rectangle(move.get('from')), 'to': rectangle(move.get('to'))})
    result = {'outputId': value['outputId'], 'geometry': rectangle(value.get('geometry')),
            'windows': [rectangle(w) for w in windows], 'destinations': destinations,
            'totalWindows': value['totalWindows']}
    if 'windowInfo' in value:
        info = value['windowInfo']
        if not isinstance(info, list) or len(info) != len(windows):
            raise ValueError('invalid-preview-window-info')
        result['windowInfo'] = []
        for row in info:
            if (not isinstance(row, dict) or not isinstance(row.get('appId'), str) or len(row['appId']) > 128
                    or row.get('eligibility') not in ('eligible', 'excluded', 'unavailable')
                    or not isinstance(row.get('reason'), str) or len(row['reason']) > 64
                    or type(row.get('moveNumber')) is not int or not 0 <= row['moveNumber'] <= move_count):
                raise ValueError('invalid-preview-window-info')
            result['windowInfo'].append(dict(appId=''.join(c for c in row['appId'] if c.isprintable()),
                eligibility=row['eligibility'],reason=row['reason'],moveNumber=row['moveNumber']))
    return result
