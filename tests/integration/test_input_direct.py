"""Refusal and evidence checks for the test-only direct-device runner."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_input_direct as runner


def login():
    return {"Type": "tty", "Class": "user", "Active": "yes", "Remote": "no",
            "Seat": "seat0", "User": "1000", "VTNr": "3"}


def backend():
    return {"schema": 1, "outputBackend": "KWin::DrmBackend", "sessionBackend": "KWin::LogindSession",
            "sessionActive": True, "sessionTerminal": 3, "directMode": True, "injectionEnabled": False,
            "libinput": {kind: 0 for kind in runner.KINDS}}


def evidence():
    before = {"schema": 2, "epoch": "one", "topologyGeneration": 0, "sessionGeneration": 0,
              "outputIdentityComplete": True, "outputs": [{"id": "DP-1", "events": 0}],
              "lockState": "unknown", "sleepState": "unknown", "sessionActivity": "unknown",
              "buttonKnown": False, "buttonsDown": False, **{kind: 0 for kind in runner.KINDS}}
    final = dict(before, buttonKnown=True, **{kind: 4 for kind in runner.KINDS})
    origin_before, origin_after = backend(), backend()
    origin_after["libinput"] = {kind: 4 for kind in runner.KINDS}
    return [before, final, origin_before, origin_after, {kind: 2 for kind in runner.KINDS}, True, True]


class DirectInputTests(unittest.TestCase):
    def test_hotplug_connector_reaches_private_worker_command(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sys, 'argv', ['runner', '--hotplug', '--hotplug-connector', 'DP', '--run']), \
                    patch.object(runner, 'preflight', return_value=(Path('/tmp/probe'), 3)), \
                    patch.object(runner.tempfile, 'mkdtemp', return_value=directory), \
                    patch.object(runner, 'run_private_session', return_value=0) as launch:
                self.assertEqual(runner.main(), 0)
                command = launch.call_args.args[0]
                self.assertEqual(command[command.index('--hotplug-connector') + 1], 'DP')
                self.assertIn('--hotplug', command)

    def test_connector_option_requires_hotplug_before_preflight(self):
        with patch.object(sys, 'argv', ['runner', '--hotplug-connector', 'DP']), \
                patch.object(runner, 'preflight') as preflight, \
                patch('sys.stderr'):
            with self.assertRaises(SystemExit) as failure:
                runner.main()
            self.assertEqual(failure.exception.code, 2)
            preflight.assert_not_called()

    def test_active_matching_local_tty(self):
        self.assertEqual(runner.validate_session(login(), "/dev/tty3", 1000), 3)

    def test_rejects_unsafe_sessions(self):
        for key, value in [("Type", "wayland"), ("Class", "greeter"), ("Active", "no"),
                           ("Remote", "yes"), ("Seat", ""), ("User", "2000"), ("VTNr", "0")]:
            with self.subTest(key=key):
                properties = dict(login(), **{key: value})
                with self.assertRaises(RuntimeError):
                    runner.validate_session(properties, "/dev/tty3", 1000)

    def test_rejects_root_and_pty_and_wrong_vt(self):
        for terminal, uid in [("/dev/tty3", 0), ("/dev/pts/3", 1000), ("/dev/tty4", 1000)]:
            with self.assertRaises(RuntimeError):
                runner.validate_session(login(), terminal, uid)

    def test_no_tty_never_starts_a_process(self):
        with patch.object(runner.os, "ttyname", side_effect=OSError), patch.object(runner.subprocess, "run") as run:
            with self.assertRaises(RuntimeError):
                runner.preflight(Path("/tmp/probe"))
            run.assert_not_called()

    def test_probe_manifest_matches_binary_source_and_kwin(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            folder = Path(temporary)
            binary = folder / "kwin/effects/plugins/libowg_input_probe.so"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"test binary")
            manifest = {"schema": 1, "kwinVersion": "6.7.5",
                        "pluginSHA256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                        "sourceSHA256": hashlib.sha256((runner.ROOT / "probes/native_input/observer.cpp").read_bytes()).hexdigest()}
            path = folder / "build-info.json"
            path.write_text(json.dumps(manifest))
            self.assertEqual(runner.verify_probe(folder, "kwin 6.7.5"), folder)
            with self.assertRaises(RuntimeError):
                runner.verify_probe(folder, "kwin 6.7.6")
            binary.write_bytes(b"different binary")
            with self.assertRaises(RuntimeError):
                runner.verify_probe(folder, "kwin 6.7.5")
            manifest["pluginSHA256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
            manifest["sourceSHA256"] = "old source"
            path.write_text(json.dumps(manifest))
            with self.assertRaises(RuntimeError):
                runner.verify_probe(folder, "kwin 6.7.5")

    def test_refuses_system_plugin_path(self):
        with self.assertRaises(RuntimeError):
            runner.verify_probe(Path("/usr/lib64/qt6/plugins"), "kwin 6.7.5")

    def test_direct_backend_and_no_injection(self):
        runner.verify_environment(backend(), 3)
        for key, value in [("outputBackend", "KWin::VirtualBackend"), ("sessionBackend", "KWin::NoopSession"),
                           ("sessionActive", False), ("sessionTerminal", 4), ("injectionEnabled", True),
                           ("directMode", False), ("schema", 2)]:
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                runner.verify_environment(dict(backend(), **{key: value}), 3)

    def test_complete_evidence_passes(self):
        result = runner.verify_result(*evidence())
        self.assertEqual(result["libinput"], {kind: 4 for kind in runner.KINDS})

    def test_synthetic_counts_cannot_substitute_for_libinput(self):
        args = evidence()
        args[3] = backend()
        with self.assertRaises(RuntimeError):
            runner.verify_result(*args)

    def test_each_input_type_must_reach_observer_origin_and_client(self):
        for kind in runner.KINDS:
            for target in ("observer", "origin", "client"):
                with self.subTest(kind=kind, target=target):
                    args = evidence()
                    counters = args[1] if target == "observer" else args[3]["libinput"] if target == "origin" else args[4]
                    counters[kind] = 0
                    with self.assertRaises(RuntimeError):
                        runner.verify_result(*args)

    def test_lifecycle_changes_invalidate_the_run(self):
        for key, value in [("epoch", "two"), ("topologyGeneration", 1), ("sessionGeneration", 1),
                           ("outputIdentityComplete", False), ("outputs", []), ("lockState", "locked"),
                           ("sleepState", "sleeping"), ("sessionActivity", "inactive")]:
            with self.subTest(key=key):
                args = evidence()
                args[1][key] = value
                with self.assertRaises(RuntimeError):
                    runner.verify_result(*args)

    def test_held_release_and_final_released_state_required(self):
        for index in (5, 6):
            args = evidence()
            args[index] = False
            with self.assertRaises(RuntimeError):
                runner.verify_result(*args)
        for key, value in [("buttonKnown", False), ("buttonsDown", True)]:
            args = evidence()
            args[1][key] = value
            with self.assertRaises(RuntimeError):
                runner.verify_result(*args)

    def test_bad_counts_refused(self):
        for value in (-1, True, float("nan"), "4"):
            args = evidence()
            args[3]["libinput"]["axis"] = value
            with self.assertRaises(RuntimeError):
                runner.verify_result(*args)

    def test_bounded_normal_shutdown(self):
        process = Mock()
        process.poll.return_value = None
        runner.terminate(process, 3)
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=3)
        process.kill.assert_not_called()

    def test_forced_shutdown_cannot_pass(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [runner.subprocess.TimeoutExpired("test", 3), 0]
        with self.assertRaises(RuntimeError):
            runner.terminate(process, 3)
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)


if __name__ == "__main__":
    unittest.main()
