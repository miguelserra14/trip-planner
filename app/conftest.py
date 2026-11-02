"""`pytest --browser` also runs the tests of the page in a real browser (app/tests/test_page.py): Edge or Chrome,
headless, driven over its DevTools protocol. No network but this computer's own address, and no model."""

def pytest_addoption(parser):
    parser.addoption("--browser", action="store_true", help="also test the app's pages in a headless Edge or Chrome")
