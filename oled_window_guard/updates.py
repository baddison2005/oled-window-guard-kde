"""Explicit GitHub release checks and verified Fedora RPM downloads."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import time
from typing import Callable, Sequence
import urllib.request

PACKAGE = 'oled-window-guard'
REPOSITORY = 'https://github.com/baddison2005/oled-window-guard-kde'
LATEST_API = 'https://api.github.com/repos/baddison2005/oled-window-guard-kde/releases/latest'
MAX_RPM_BYTES = 64 * 1024 * 1024
VERSION = r'(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})'
EVR = re.compile(rf'({VERSION})-([1-9][0-9]{{0,5}}\.fc44)\Z')


def _run(argv: Sequence[str]) -> str:
    return subprocess.run(argv, capture_output=True, text=True, check=True, timeout=10).stdout.strip()


def package_update_status(*, runner: Callable[[Sequence[str]], str] = _run) -> dict:
    try:
        installed = runner(['rpm', '-q', '--qf', '%{VERSION}-%{RELEASE}', PACKAGE])
    except (OSError, subprocess.SubprocessError):
        installed = ''
    if installed:
        return dict(installed=True, version=installed, state='unchecked', offer=None,
                    message=f'Installed RPM: {installed}. Select Check for updates to check GitHub.')
    return dict(installed=False, version=None, state='unavailable', offer=None,
                message='Development checkout: no installed RPM. Install the Fedora RPM to use in-app upgrades.')


class GitHubRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        check_https_url(newurl)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def check_https_url(url):
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.hostname not in
            ('api.github.com', 'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com')
            or parts.username or parts.password or parts.port not in (None, 443)):
        raise ValueError('Unexpected release download location')


def _open(url):
    check_https_url(url)
    request = urllib.request.Request(url, headers={'User-Agent': 'OLED-Window-Guard-KDE',
        'Accept': 'application/vnd.github+json' if url == LATEST_API else 'application/octet-stream'})
    return urllib.request.build_opener(GitHubRedirects()).open(request, timeout=5)


def fetch_latest():
    with _open(LATEST_API) as response:
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError('GitHub release metadata is too large')
    return json.loads(data)


def validate_offer(offer):
    if not isinstance(offer, dict) or set(offer) != {'version', 'release', 'name', 'url', 'sha256', 'size'}:
        raise ValueError('Invalid upgrade offer')
    if not isinstance(offer['version'], str) or not re.fullmatch(VERSION, offer['version']):
        raise ValueError('Invalid release version')
    if not isinstance(offer['release'], str) or not re.fullmatch(r'[1-9][0-9]{0,5}\.fc44', offer['release']):
        raise ValueError('Unsupported RPM release')
    name = f"{PACKAGE}-{offer['version']}-{offer['release']}.x86_64.rpm"
    if offer['name'] != name or offer['url'] != f"{REPOSITORY}/releases/download/v{offer['version']}/{name}":
        raise ValueError('Unexpected release asset')
    if not isinstance(offer['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', offer['sha256']):
        raise ValueError('The release has no valid SHA-256 digest')
    if type(offer['size']) is not int or not 0 < offer['size'] <= MAX_RPM_BYTES:
        raise ValueError('Invalid RPM download size')
    return dict(offer)


def newer(offer, installed):
    import rpm
    match = EVR.fullmatch(installed)
    if not match:
        raise ValueError('Unsupported installed package version')
    return rpm.labelCompare(('0', offer['version'], offer['release']), ('0', *match.groups())) > 0


def check_for_updates(*, runner=_run, fetch=fetch_latest, machine=None, os_release=None):
    result = package_update_status(runner=runner)
    if not result['installed']:
        return result
    try:
        system = platform.freedesktop_os_release() if os_release is None else os_release
        if (machine or platform.machine()) != 'x86_64' or system.get('ID') != 'fedora' or system.get('VERSION_ID') != '44':
            raise ValueError('In-app upgrades currently support Fedora 44 x86_64 only')
        release = fetch()
        if (not isinstance(release, dict) or release.get('draft') is not False
                or release.get('prerelease') is not False or not isinstance(release.get('tag_name'), str)
                or not re.fullmatch('v' + VERSION, release['tag_name'])):
            raise ValueError('GitHub did not return a stable release')
        version = release['tag_name'][1:]
        assets = release.get('assets')
        if not isinstance(assets, list) or len(assets) > 128:
            raise ValueError('Invalid release assets')
        candidates = []
        for asset in assets:
            if not isinstance(asset, dict) or not isinstance(asset.get('name'), str):
                continue
            match = re.fullmatch(re.escape(f'{PACKAGE}-{version}-') + r'([1-9][0-9]{0,5}\.fc44)\.x86_64\.rpm', asset['name'])
            if match:
                digest = asset.get('digest')
                candidates.append(validate_offer(dict(version=version, release=match[1], name=asset['name'],
                    url=asset.get('browser_download_url'), sha256=digest[7:] if isinstance(digest, str) and digest.startswith('sha256:') else None,
                    size=asset.get('size'))))
        if len(candidates) != 1:
            raise ValueError('This release has no single compatible Fedora 44 x86_64 RPM')
        offer = candidates[0]
        available = newer(offer, result['version'])
        result.update(state='available' if available else 'current', latestVersion=version,
            offer=offer if available else None,
            message=(f"Version {version} is available on GitHub. Installed: {result['version']}."
                if available else f"No newer release is available. Installed: {result['version']}; latest GitHub release: {version}."))
    except Exception as error:
        # A failed check must never be presented as "up to date".
        result.update(state='error', offer=None, message=f"Could not check GitHub for updates: {str(error)[:240]}. Try again.")
    return result


def validate_rpm(folder, *, runner=_run):
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir() or folder.stat().st_uid != os.getuid() or folder.stat().st_mode & 0o077:
        raise ValueError('Upgrade directory must be private and owned by you')
    metadata = folder / 'offer.json'
    if metadata.is_symlink() or metadata.stat().st_size > 4096:
        raise ValueError('Invalid staged upgrade metadata')
    offer = validate_offer(json.loads(metadata.read_text()))
    path = folder / offer['name']
    if path.is_symlink() or not path.is_file() or path.stat().st_size != offer['size']:
        raise ValueError('RPM download size changed')
    if hashlib.sha256(path.read_bytes()).hexdigest() != offer['sha256']:
        raise ValueError('RPM checksum verification failed')
    fields = runner(['rpm', '-qp', '--qf', '%{NAME}\n%{VERSION}\n%{RELEASE}\n%{ARCH}\n%{EPOCHNUM}', str(path)]).splitlines()
    if fields != [PACKAGE, offer['version'], offer['release'], 'x86_64', '0']:
        raise ValueError('Downloaded RPM identity does not match the release')
    installed = package_update_status(runner=runner)
    if not installed['installed'] or not newer(offer, installed['version']):
        raise ValueError('This RPM is not newer than the installed package')
    requirements = runner(['rpm', '-qp', '--requires', str(path)]).splitlines()
    for name in ('kwin', 'qt6-qtbase', 'qt6-qtdeclarative'):
        values = [line.removeprefix(name + ' = ') for line in requirements if line.startswith(name + ' = ')]
        if len(values) != 1 or values[0] != runner(['rpm', '-q', '--qf', '%{VERSION}', name]):
            raise ValueError(f'This update requires a different or unsupported {name} version')
    return offer, path


def download_update(offer, *, opener=_open, runner=_run, cancel=None):
    offer = validate_offer(offer)
    folder = Path(tempfile.mkdtemp(prefix='owg-upgrade-'))
    try:
        target = folder / offer['name']
        size = 0
        start = time.monotonic()
        with opener(offer['url']) as response, target.open('xb') as output:
            while True:
                if cancel is not None and cancel.is_set():
                    raise ValueError('Download cancelled')
                block = response.read(65536)
                if not block:
                    break
                size += len(block)
                if size > offer['size'] or time.monotonic() - start > 90:
                    raise ValueError('RPM download exceeded its size or time limit')
                output.write(block)
        if size != offer['size']:
            raise ValueError('RPM download was incomplete')
        target.chmod(0o600)
        (folder / 'offer.json').write_text(json.dumps(offer))
        validate_rpm(folder, runner=runner)
        return folder
    except Exception:
        import shutil
        shutil.rmtree(folder)
        raise


def read_upgrade_result(path):
    try:
        path = Path(path)
        if path.is_symlink() or path.stat().st_size > 4096:
            return ''
        result = json.loads(path.read_text())
        if isinstance(result, dict) and result.get('result') in ('success', 'cancelled', 'failed') and isinstance(result.get('message'), str):
            return result['message'][:1024]
    except (OSError, ValueError, TypeError):
        pass
    return ''
