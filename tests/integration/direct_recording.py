"""Disk-backed diagnostic recording, independent of the private KWin worker."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    sync_directory(path.parent)


def timestamp():
    return {'utc': datetime.now(timezone.utc).isoformat(),
            'monotonicNs': time.monotonic_ns()}


def event(directory, name, **facts):
    # The worker writes steps; the supervisor writes its own heartbeat file.
    if not (directory / 'recording.json').is_file():
        return
    with (directory / 'steps.jsonl').open('a') as stream:
        stream.write(json.dumps({**timestamp(), 'event': name, **facts}) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def checkpoint(directory, name, value):
    if (directory / 'recording.json').is_file():
        event(directory, name, evidence=value)
        atomic_json(directory / name, value)
    else:
        temporary = directory / (name + '.tmp')
        temporary.write_text(json.dumps(value, indent=2) + '\n')
        temporary.replace(directory / name)


def create_directory(base):
    base = base.expanduser().resolve()
    if any(base.is_relative_to(path) for path in
           (Path('/tmp'), Path('/run'), Path('/dev/shm'))):
        raise RuntimeError('Record directory must be on persistent storage, outside /tmp and /run')
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    filesystem = subprocess.run(['findmnt', '-T', str(base), '-n', '-o', 'FSTYPE'],
                                check=True, capture_output=True, text=True, timeout=3)
    if filesystem.stdout.strip() in ('tmpfs', 'ramfs'):
        raise RuntimeError('Record directory is on a volatile memory filesystem')
    directory = Path(tempfile.mkdtemp(prefix='owg-input-direct-', dir=base))
    sync_directory(base)
    atomic_json(base / 'latest-run.json', {'directory': str(directory), **timestamp()})
    return directory


def recording_preflight():
    # Refuse before taking DRM if kernel logs cannot be captured by this login.
    result = subprocess.run(['journalctl', '--boot', '--no-pager', '--lines=1',
                             '_TRANSPORT=kernel'], capture_output=True, text=True, timeout=5)
    if (result.returncode or not result.stdout.strip() or '-- No entries --' in result.stdout or
            'not seeing messages' in result.stderr.lower()):
        raise RuntimeError('This login cannot read the kernel journal; recorded test refused')
    for command in (['udevadm', '--version'], ['stdbuf', '--version']):
        subprocess.run(command, check=True, capture_output=True, timeout=3)


class Recorder:
    """Flush logs every 250 ms; heartbeat remains alive if worker/D-Bus stalls."""
    def __init__(self, directory, metadata):
        self.directory = directory
        self.stop = threading.Event()
        self.error = None
        self.processes = []
        self.streams = []
        self.thread = None
        atomic_json(directory / 'recording.json', {
            'schema': 1, **timestamp(), **metadata,
            'logSyncIntervalSeconds': .25, 'heartbeatIntervalSeconds': 1,
            'limitations': 'fsync reduces loss; hardware/power failure may still lose final records',
        })

    def start(self, name, command):
        stream = (self.directory / name).open('wb', buffering=0)
        self.streams.append(stream)
        process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT)
        self.processes.append((name, process))

    def __enter__(self):
        try:
            journal = ['stdbuf', '-oL', '-eL', 'journalctl', '--boot', '--follow',
                       '--no-pager', '--lines=100', '--output=short-monotonic',
                       '_TRANSPORT=kernel', '+', '_COMM=kwin_wayland', '+',
                       '_SYSTEMD_USER_UNIT=plasma-kwin_wayland.service', '+',
                       '_SYSTEMD_UNIT=systemd-logind.service', '+',
                       'COREDUMP_EXE=/usr/bin/kwin_wayland', '+', '_COMM=kscreen_backend']
            self.start('host-journal.log', journal)
            self.start('drm-udev.log', ['stdbuf', '-oL', '-eL', 'udevadm', 'monitor',
                                     '--kernel', '--udev', '--property', '--subsystem-match=drm'])
            self.flush_logs()
            self.thread = threading.Thread(target=self.monitor, daemon=True)
            self.thread.start()
            # Startup is bounded and happens before any private compositor launch.
            time.sleep(.3)
            self.check()
            event(self.directory, 'collectors-ready')
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def flush_logs(self):
        for path in self.directory.glob('*.log'):
            with path.open('rb') as stream:
                os.fsync(stream.fileno())
        sync_directory(self.directory)

    def check(self):
        if self.error:
            raise RuntimeError('Diagnostic recorder failed: ' + self.error)
        for name, process in self.processes:
            if process.poll() is not None:
                raise RuntimeError('Diagnostic collector exited: ' + name)

    def monitor(self):
        next_heartbeat = 0
        try:
            while not self.stop.is_set():
                self.check()
                self.flush_logs()
                now = time.monotonic()
                if now >= next_heartbeat:
                    with (self.directory / 'heartbeat.jsonl').open('a') as stream:
                        stream.write(json.dumps({**timestamp(), 'event': 'recorder-alive'}) + '\n')
                        stream.flush()
                        os.fsync(stream.fileno())
                    next_heartbeat = now + 1
                self.stop.wait(.25)
        except Exception as exc:
            self.error = str(exc)

    def __exit__(self, exc_type, exc, traceback):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=3)
        for _, process in self.processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        self.flush_logs()
        for stream in self.streams:
            stream.close()
        event(self.directory, 'recorder-stopped', error=self.error,
              exception=str(exc) if exc else None)
