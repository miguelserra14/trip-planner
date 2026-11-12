"""The app over a few reports and maps kept in the repository (demo/), to be tried with no key at all.

    python main.py --demo          the app, in English, on the reports and maps of demo/
    python -m app.demo PATH...     copy a trip's folder (reports/...) or a map's page (discover/maps/*.html) into demo/

The reports and the maps there were made by real searches, and are shown as the traveller's own are. Nothing that
would ask a service or change a file is done (site.DEMO_ALLOWED): a search, a map, a price asked again, the check
of the services or a language added is answered with a note, and a report is not moved among the kept ones. The
log of a run is left out of a copy: it lists every request of the search, and the report says all a reader needs."""
import shutil
import sys
from pathlib import Path

from app import maps, reports
from trip_planner.report import full

DEMO = Path(__file__).parents[1] / "demo"
DEMO_REPORTS = DEMO / "reports"   # a trip's folder each, as reports/ keeps them
DEMO_MAPS = DEMO / "maps"         # a map's page each, as discover/maps keeps them
LEFT_OUT = {".log"}               # the files of a trip's folder not copied
LANGUAGE = "en"                   # the language the demo opens in: the one of the code and the README

def use() -> None:
    """Point the app at demo/ instead of the traveller's reports and maps. reports.py has the folders under its own
    names, as the tests patch them (app/tests/conftest.py)."""
    full.REPORTS, full.SAVED_REPORTS = DEMO_REPORTS, DEMO_REPORTS / "saved"
    reports.REPORTS, reports.SAVED_REPORTS = full.REPORTS, full.SAVED_REPORTS
    maps.SAVED_MAPS = DEMO_MAPS

def copy(source: Path) -> Path:
    """A trip's folder or a map's page copied into demo/, the run's log left out. Returns where it went."""
    if source.is_dir() and source.name.startswith("trip_") and list(source.glob("trip_*.md")):
        target = DEMO_REPORTS / source.name
        shutil.copytree(source, target, dirs_exist_ok=True,
                        ignore=lambda folder, names: [name for name in names if Path(name).suffix in LEFT_OUT])
        return target
    if source.is_file() and source.suffix == ".html" and maps.page_data(source.read_text(encoding="utf-8")):
        DEMO_MAPS.mkdir(parents=True, exist_ok=True)
        return Path(shutil.copy2(source, DEMO_MAPS / source.name))
    raise ValueError(f"{source}: neither a trip's folder with its report nor a map's page")

def main(arguments: list[str]) -> int:
    if not arguments:
        print(__doc__)
        return 1
    for argument in arguments:
        try:
            print(copy(Path(argument)))
        except (OSError, ValueError) as error:
            print(error)
            return 1
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
