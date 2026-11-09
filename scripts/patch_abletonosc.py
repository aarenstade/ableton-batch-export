#!/usr/bin/env python3
"""Expose song.start_time in an existing AbletonOSC install, with a backup.

Usage: python scripts/patch_abletonosc.py /path/to/AbletonOSC [--write]
Quit Live before applying; restart Live afterward. No third-party files bundled.
"""

import argparse
import ast
from pathlib import Path


def patched_source(text):
    matches = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "properties_rw" for t in node.targets
        ):
            if isinstance(node.value, ast.List):
                matches.append(node.value)
    if len(matches) != 1:
        raise ValueError(
            "Expected one properties_rw list; upstream layout changed, review song.py manually"
        )
    values = matches[0]
    if "start_time" in [ast.literal_eval(item) for item in values.elts]:
        return text
    if not values.elts or values.lineno == values.end_lineno:
        raise ValueError("Expected a multiline property list; review song.py manually")
    lines = text.splitlines(keepends=True)
    indent = " " * values.elts[0].col_offset
    lines.insert(values.lineno, indent + '"start_time",\n')
    result = "".join(lines)
    ast.parse(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installation", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    path = args.installation.expanduser() / "abletonosc/song.py"
    old = path.read_text(encoding="utf-8")
    new = patched_source(old)
    if old == new:
        print("start_time is already exposed; no changes")
    elif not args.write:
        print(f"Would expose start_time in {path}. Quit Live, then rerun with --write.")
    else:
        backup = path.with_suffix(".py.before-batch-export")
        with backup.open("x", encoding="utf-8") as stream:
            stream.write(old)
        temporary = path.with_suffix(".py.tmp")
        temporary.write_text(new, encoding="utf-8")
        temporary.replace(path)
        print(f"Patched {path}; original at {backup}. Restart Live.")


if __name__ == "__main__":
    main()
