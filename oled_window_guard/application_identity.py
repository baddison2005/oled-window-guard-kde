"""Desktop-file aliases for selected application exclusions; never launches apps."""
import configparser
import os
from pathlib import Path


def exclusion_aliases(application_ids):
    selected = set(application_ids)
    result = {}
    home = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))).expanduser()
    roots = [home / 'applications'] if home.is_absolute() else []
    roots += [Path(p) / 'applications' for p in os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share').split(':') if Path(p).is_absolute()]
    for root in dict.fromkeys(roots):
        for path in root.rglob('*.desktop'):
            app_id = str(path.relative_to(root).with_suffix('')).replace('/', '-')
            if app_id not in selected or app_id in result:
                continue
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                parser.read(path, encoding='utf-8')
                alias = parser['Desktop Entry'].get('StartupWMClass', '')
                if alias and len(alias) <= 512:
                    result[app_id] = [alias]
            except (OSError, KeyError, configparser.Error):
                continue
    return result
