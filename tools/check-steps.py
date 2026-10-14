#!/usr/bin/env python3
"""Every step of the installer must explain itself.

setup.py asks for a token, a directory, a model and a session name. Each of
those steps says one short sentence about what it is for and what breaks
without it. That is easy to honour and easy to forget: the next step someone
adds will have a heading and no reason, and the person running the installer
has no way to tell whether a question is load-bearing or cosmetic.

So it is asserted. A step without a `why(` next to its `heading(` fails.

Checks that the explanation is also short. A paragraph here pushes the
question down the screen, and a question nobody sees is a question that gets
answered with Enter.
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SETUP = ROOT / "setup.py"

MAX_CHARS = 260      # roughly four wrapped lines
LOOKAHEAD = 6        # statements after the heading that may hold the why()


def calls(node: ast.AST, name: str) -> list[ast.Call]:
    return [n for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == name]


def main() -> int:
    if not SETUP.exists():
        print(f"  {SETUP} not found")
        return 1

    tree = ast.parse(SETUP.read_text(encoding="utf-8"))
    steps = 0
    failures = 0

    print()
    for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
        body = fn.body
        idx = next((i for i, s in enumerate(body)
                    if calls(s, "heading")), None)
        if idx is None:
            continue
        steps += 1

        window = body[idx + 1: idx + 1 + LOOKAHEAD]
        whys = [w for s in window for w in calls(s, "why")]
        if not whys:
            print(f"  FAIL  {fn.name:26} step has no explanation")
            failures += 1
            continue

        text = ""
        for w in whys:
            if w.args and isinstance(w.args[0], ast.Constant) \
                    and isinstance(w.args[0].value, str):
                text += w.args[0].value
        if not text.strip():
            print(f"  FAIL  {fn.name:26} why() is empty")
            failures += 1
        elif len(text) > MAX_CHARS:
            print(f"  FAIL  {fn.name:26} explanation is {len(text)} chars, "
                  f"keep it under {MAX_CHARS}")
            failures += 1
        else:
            print(f"  OK    {fn.name:26} {len(text):3} chars")

    print()
    if steps == 0:
        print("  FAIL  no steps found - the check itself is broken")
        return 1
    print(f"  {steps} steps checked")
    print()
    print(f"failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())