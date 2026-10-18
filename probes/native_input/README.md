# Private native input probe

This is a test-only KWin effect for disposable sessions. It observes input and
can inject a controlled test sequence only in its isolated nested mode. It is
not the production observer and is excluded from the RPM.

Build against matching development headers:

```sh
python3 probes/native_input/build_probe.py --sysroot /path/to/extracted/usr --output /tmp/owg-input-plugins
```

Test runners under `tests/integration/` include `run_input_keyboard_scope.py`,
`run_input_lock.py` and `run_input_direct.py`. Read their options and prerequisites
before running them. Direct-device checks require an authenticated local VT and
explicit operator participation. Synthetic nested input, a private NoopSession
or a virtual output cannot establish physical-device acceptance by themselves.

The production observer is documented in
[native/observer](../../native/observer/README.md). Current release validation is
summarized in the [release review](../../docs/RELEASE_REVIEW_v1.0.0.md).
