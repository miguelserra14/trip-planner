"""Run trip_planner.ipynb from the command line, without the airport checklist.

It plans the trip in the SETTINGS cell (and the ones in EXTRA_TRIPS), prints the result and saves the report
to reports/. Use it to check that the setup works:

    .venv\\Scripts\\python scripts\\run_headless.py
"""
import ast
import asyncio
import inspect
import os
import sys
from pathlib import Path

import nbformat

ROOT = Path(__file__).resolve().parent.parent
NOTEBOOK = ROOT / "trip_planner.ipynb"


def print_markdown(markdown: str) -> None:
    """Stands in for the notebook's show(): there is no notebook to render the Markdown in."""
    print("\n" + markdown + "\n")


async def run_notebook() -> dict:
    """Run every code cell in one namespace, as the kernel does. Returns that namespace."""
    notebook = nbformat.read(NOTEBOOK, as_version=4)
    namespace: dict = {"__name__": "__main__"}
    for number, cell in enumerate(notebook.cells):
        if cell.cell_type != "code" or not cell.source.strip():
            continue
        # Notebook cells may use await at the top level
        code = compile(cell.source, f"<cell {number}>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        result = eval(code, namespace)
        if inspect.iscoroutine(result):
            await result
        # No checklist and no question from the command line: every airport found is searched
        for settings in [namespace.get("SETTINGS"), *namespace.get("EXTRA_TRIPS", [])]:
            if settings is not None:
                settings.confirm_airports = False
        if "show" in namespace:
            namespace["show"] = print_markdown
    return namespace


def main() -> int:
    os.chdir(ROOT)   # the notebook reads .env, resources/ and reports/ from its own folder
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    namespace = asyncio.run(run_notebook())
    planned = len(namespace.get("responses") or [])
    if planned < len(namespace.get("TRIPS") or [None]):
        print("The trip was not planned, see the error above.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
