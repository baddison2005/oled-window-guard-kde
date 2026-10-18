#!/usr/bin/env python3
"""Bundle QML plain-JS libraries for a context-free Qt WorkerScript engine."""
import argparse
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
IMPORT = re.compile(r'^\.import "([^"\n]+)" as (\w+)\s*$', re.MULTILINE)
FUNCTION = re.compile(r'^function (\w+)\(', re.MULTILINE)


def build(core_dir=ROOT / "core", *, probe=False):
    chunks = ["var OWGModules = {};\n"]
    loaded = set()
    active = set()
    def include(name):
        if name in loaded:
            return
        if name in active or Path(name).name != name or not name.endswith(".js"):
            raise ValueError("invalid or cyclic worker dependency: " + name)
        active.add(name)
        source = (core_dir / name).read_text()
        imports = IMPORT.findall(source)
        for dependency, _ in imports:
            include(dependency)
        body = IMPORT.sub("", source)
        body = re.sub(r'^\.pragma library\s*', "", body, flags=re.MULTILINE)
        aliases = "".join(f'var {alias} = OWGModules["{dependency}"];\n' for dependency, alias in imports)
        exports = ",".join(f"{name}:{name}" for name in FUNCTION.findall(body))
        chunks.append(f'OWGModules["{name}"] = (function() {{\nvar module;\n{aliases}{body}\nreturn {{{exports}}};\n}})();\n')
        loaded.add(name)
        active.remove(name)
    include("planning_worker_entry.js")
    hook = ""
    if probe:
        # Bounded test-only stall proves the GUI timer can run during worker work.
        hook = 'if (message.probeDelayMs === 200 || message.probeDelayMs === 500) { var until=Date.now()+message.probeDelayMs; while(Date.now()<until) {} }\n'
    chunks.append('WorkerScript.onMessage = function(message) {\n' + hook +
                  'WorkerScript.sendMessage(OWGModules["planning_worker_entry.js"].processPlanningRequest(message));\n};\n')
    return "".join(chunks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(build())
