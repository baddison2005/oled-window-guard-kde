"""Exercise crash evidence, collector failure and socket-path separation."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import direct_recording as recording
import direct_warning
import run_input_direct as runner


class RecordingTests(unittest.TestCase):
    def test_durable_checkpoint_survives_worker_sigkill(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            recording.atomic_json(directory / 'recording.json', {'schema': 1})
            code = (
                'import os, signal, sys\n'
                'from pathlib import Path\n'
                'from direct_recording import checkpoint\n'
                'from direct_warning import publish_instruction\n'
                'directory = Path(sys.argv[1])\n'
                'publish_instruction(directory, "NOW RECONNECT DP-1")\n'
                'checkpoint(directory, "hotplug-progress.json", {"stage": "reconnect"})\n'
                'os.kill(os.getpid(), signal.SIGKILL)\n')
            process = subprocess.run([sys.executable, '-c', code, temporary],
                                     cwd=Path(__file__).parent, timeout=5)
            self.assertEqual(process.returncode, -signal.SIGKILL)
            self.assertEqual(json.loads((directory / 'hotplug-progress.json').read_text()),
                             {'stage': 'reconnect'})
            rows = [json.loads(line) for line in (directory / 'steps.jsonl').read_text().splitlines()]
            self.assertEqual(rows[0]['message'], 'NOW RECONNECT DP-1')
            self.assertEqual(rows[1]['evidence']['stage'], 'reconnect')
            self.assertLess(rows[0]['monotonicNs'], rows[1]['monotonicNs'])
            self.assertFalse((directory / 'result.json').exists())

    def test_prompt_is_not_published_if_disk_flush_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            recording.atomic_json(directory / 'recording.json', {})
            with patch.object(recording.os, 'fsync', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    direct_warning.publish_instruction(directory, 'NOW DISCONNECT DP-1')
            self.assertFalse((directory / 'instructions.txt').exists())

    def test_repeated_prompt_has_only_one_timestamp(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            recording.atomic_json(directory / 'recording.json', {})
            for _ in range(3):
                direct_warning.publish_instruction(directory, 'NOW DISCONNECT DP-1')
            self.assertEqual(len((directory / 'steps.jsonl').read_text().splitlines()), 1)

    def test_short_runtime_separate_from_long_artifact_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / ('long-record-path-' * 8)
            directory.mkdir()
            runtime = Path(temporary) / 'runtime'
            runtime.mkdir(mode=0o700)
            env = runner.private_environment(directory, Path('/tmp/probe'), runtime_dir=runtime)
            self.assertEqual(env['XDG_RUNTIME_DIR'], str(runtime))
            self.assertEqual(env['XDG_CONFIG_HOME'], str(directory / 'config'))
            self.assertLessEqual(len(os.fsencode(runtime / 'owg-direct-0')) + 1, 108)
            self.assertFalse((directory / 'runtime').exists())

    def test_long_legacy_socket_refused_before_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / ('long-record-path-' * 8)
            directory.mkdir()
            with self.assertRaisesRegex(RuntimeError, '108 bytes'):
                runner.private_environment(directory, Path('/tmp/probe'))

    def test_volatile_record_directory_refused(self):
        for path in ('/tmp/owg-test', '/run/owg-test', '/dev/shm/owg-test'):
            with self.subTest(path=path), self.assertRaisesRegex(RuntimeError, 'persistent storage'):
                recording.create_directory(Path(path))

    def test_recorded_cli_forwards_durable_artifacts_and_short_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            recording.atomic_json(directory / 'build-info.json', {})
            with patch.object(sys, 'argv', ['runner', '--hotplug', '--hotplug-connector', 'DP',
                                           '--record-dir', '/permanent/records', '--run']), \
                    patch.object(runner, 'preflight', return_value=(directory, 3)), \
                    patch.object(recording, 'recording_preflight'), \
                    patch.object(recording, 'create_directory', return_value=directory), \
                    patch.object(recording, 'Recorder'), \
                    patch.object(runner, 'run_private_session', return_value=0) as launch:
                (directory / 'runner.log').write_text('DIRECT_RESULT: PASS\n')
                self.assertEqual(runner.main(), 0)
                command = launch.call_args.args[0]
                self.assertEqual(command[command.index('--worker') + 1], temporary)
                runtime = Path(command[command.index('--runtime-dir') + 1])
                self.assertTrue(runtime.is_relative_to('/tmp'))
                self.assertLessEqual(len(os.fsencode(runtime / 'owg-direct-0')) + 1, 108)
                self.assertEqual(command[command.index('--hotplug-connector') + 1], 'DP')
                self.assertFalse(runtime.exists())

    def test_kernel_journal_permission_failure_refuses_recording(self):
        result = subprocess.CompletedProcess([], 0, '-- No entries --\n', '')
        with patch.object(recording.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(RuntimeError, 'cannot read the kernel journal'):
                recording.recording_preflight()

    def test_background_recording_stays_live_during_worker_and_timeout(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            recorder = recording.Recorder(directory, {'scope': 'harmless test'})
            original_start = recorder.start
            def harmless_start(name, command):
                original_start(name, [sys.executable, '-u', '-c',
                                     'import time; print("collector-ready"); time.sleep(10)'])
            with patch.object(recorder, 'start', side_effect=harmless_start), recorder:
                command = [sys.executable, '-u', '-c',
                           'import time; print("worker-ready"); time.sleep(10)']
                result = runner.run_private_session(command, os.environ.copy(), 1.4, recorder)
                self.assertEqual(result, 124)
                self.assertIn('worker-ready', (directory / 'runner.log').read_text())
                rows = [json.loads(line) for line in (directory / 'heartbeat.jsonl').read_text().splitlines()]
                self.assertGreaterEqual(len(rows), 2)
                self.assertIn('collector-ready', (directory / 'host-journal.log').read_text())
            self.assertEqual(json.loads((directory / 'steps.jsonl').read_text().splitlines()[-1])['event'],
                             'recorder-stopped')

    def test_dead_collector_refuses_before_worker_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            recorder = recording.Recorder(Path(temporary), {})
            original_start = recorder.start
            with patch.object(recorder, 'start', side_effect=lambda name, command:
                              original_start(name, [sys.executable, '-c', 'pass'])):
                with self.assertRaisesRegex(RuntimeError, 'collector exited'):
                    with recorder:
                        self.fail('A dead collector must not allow launching the worker')

    def test_recording_disk_failure_still_stops_worker(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as temporary:
            recorder = Mock(directory=Path(temporary))
            recorder.check.side_effect = RuntimeError('disk failure')
            original_popen = subprocess.Popen
            processes = []
            def launch(*args, **kwargs):
                process = original_popen(*args, **kwargs)
                processes.append(process)
                return process
            with patch.object(runner.subprocess, 'Popen', side_effect=launch), \
                    patch.object(recording, 'event', side_effect=OSError('disk failure')):
                result = runner.run_private_session(
                    [sys.executable, '-c', 'import time; time.sleep(10)'],
                    os.environ.copy(), 2, recorder)
            self.assertEqual(result, 124)
            self.assertIsNotNone(processes[0].poll())


if __name__ == '__main__':
    unittest.main()
