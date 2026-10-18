# Private planner responsiveness probes

These development harnesses exercise synthetic geometry in an isolated virtual
KWin session. They do not move the user's application windows. Run from the root:

```sh
python3 tests/integration/run_planner_responsiveness.py
```

The harness measures planner execution, timer delivery and cancellation using
owned fixtures. Production planning runs in a Qt WorkerScript; compositor-local
validation and guarded writes remain separate. Generated production workers
exclude injected probe stalls.

The retained timing of a synthetic fixture is not a general worst-case latency
guarantee. Read the runner's options and isolation prerequisites before using it.
Private output/log directories are created by the runner and are not release
assets. See the [release review](../../docs/RELEASE_REVIEW_v1.0.0.md) for the
current validation summary.
