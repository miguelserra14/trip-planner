"""The reports as web pages (report_page.py, reports.py) and the server that shows them (server.py). No network but
this computer's own address."""
import http.client
import re

from app import report_page, reports, server
from app.site import Reply, Site
from settings import SETTINGS
from trip_planner.report.formatting import timeline
from trip_planner.texts import TEXTS

def write_report(folder, name: str, text: str):
    path = folder / name / f"{name}.md"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return path

def test_what_a_model_wrote_cannot_become_markup():
    body, _ = report_page.to_html('Book here <script>alert(1)</script> javascript:alert(1) "x" onclick=1')
    assert "<script>" not in body and "&lt;script&gt;" in body
    assert "<a " not in body

def test_a_link_is_kept_without_the_punctuation_after_it():
    body, _ = report_page.to_html("- Where to book: https://shop.flixbus.pt/search?a=1&b=2).")
    assert '<a href="https://shop.flixbus.pt/search?a=1&amp;b=2"' in body and body.count("<li>") == 1

def test_the_line_of_an_option_is_drawn_to_scale():
    line = timeline([("16:07", "Bruxelas"), ("17:38", "CRL"), ("19:25", "✈")], [("B", 30), (None, 30)])
    body, _ = report_page.to_html(line)
    widths = [float(width) for width in re.findall(r"width: ([\d.]+)%", body)]
    assert widths == [50.0, 50.0] and "var(--light-B)" in body and "Bruxelas" in body and "point last" in body

def test_a_table_has_its_header_and_its_lights():
    body, _ = report_page.to_html("| Part | Grade |\n| :-- | :-- |\n| Flight | 🔵 confirmed |")
    assert "<th>Part</th>" in body and 'class="light-A"' in body and ":--" not in body

def test_a_rule_the_model_wrote_is_a_line():
    body, _ = report_page.to_html("Before\n***\n- - -\nAfter")
    assert body.count("<hr>") == 2 and "*" not in body

def test_the_list_of_contents_has_the_two_first_levels_of_headings():
    _, sections = report_page.to_html("# Plan\n## Settings\n### 1. Places\n# Final result")
    assert [title for _, title in sections] == ["Plan", "Settings", "Final result"]

def test_a_report_is_found_by_its_key_and_nothing_else_is():
    write_report(reports.REPORTS, "trip_coimbra_antwerp_2026-11-02_1310", "# Trip plan: Coimbra → Antwerp\n\nHello")
    site = Site(SETTINGS)
    listed = site.get("/")
    assert listed.status == 200 and "Trip plan: Coimbra → Antwerp (02/11/2026 13:10)" in listed.html
    shown = site.get("/report/recent/trip_coimbra_antwerp_2026-11-02_1310")
    assert shown.status == 200 and "<p>Hello</p>" in shown.html
    for path in ("/report/recent/../../settings.py", "/report/recent%2F..%2F..%2Fsettings", "/report/", "/settings.py"):
        assert site.get(path).status == 404

class Answers:
    def get(self, path):
        return Reply(200, "the page")

    def post(self, path, body):
        return Reply(200, "posted")

def ask(port: int, method: str, host: str, body: str | None = None, length: int | None = None) -> int:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        sizes = {"Content-Length": str(length)} if length else {}
        connection.request(method, "/search", body=body, headers={"Host": host, **sizes})
        response = connection.getresponse()
        assert "script-src 'self';" in response.getheader("Content-Security-Policy")   # the app's own file, never an inline script
        return response.status
    finally:
        connection.close()

def test_the_server_answers_only_to_its_own_address_and_to_a_form_sized_post():
    running = server.start(Answers())
    try:
        port = running.server_address[1]
        for host, status in ((f"127.0.0.1:{port}", 200), (f"localhost:{port}", 404), ("evil.example", 404)):
            assert ask(port, "GET", host) == status and ask(port, "POST", host, "token=x") == status
        # Refused from its length alone, before anything is read: the test sends none of it
        assert ask(port, "POST", f"127.0.0.1:{port}", length=server.MAX_BODY_BYTES + 1) == 400
    finally:
        server.stop(running)

def test_english_and_portuguese_have_the_same_texts():
    keys = lambda language: {key for key in TEXTS[language] if key.startswith("app_")}
    assert keys("en") and keys("en") == keys("pt-PT")
