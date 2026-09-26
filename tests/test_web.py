import re
import tempfile
import unittest
from pathlib import Path

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from fastapi.testclient import TestClient

    from battle_test.config import CorpusConfig
    from battle_test.corpus import build_index
    from battle_test.web.app import case_title, create_app, facts_text, safe_next
    from battle_test.web.demo import DemoClient
    from battle_test.web.render import document_html
except ImportError:  # web and corpus-build dependencies are optional
    TestClient = None


def _row(act_id, citation, title, text):
    return {"act_id": act_id, "citation": citation, "document_type": "statute", "title_name": "Code",
            "chapter_name": "None", "section_title": title, "display_path": citation,
            "act_status": "in_force", "text": text, "source_url": f"https://le.utah.gov/{act_id}",
            "last_amended_year": 2020}


FACTS = {"state": "UT", "mode": "facts", "rounds": "1", "plaintiff": "Dana Whitfield",
         "defendant": "Summit Peak Roofing LLC", "timeline": "Paid a deposit; roofer never came back."}

# Throwaway test credentials only.
PW = "correct horse battery"
NEW_PW = "a brand new passphrase"


@unittest.skipIf(TestClient is None, "web or pyarrow dependencies not installed")
class WebAppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        pq.write_table(pa.Table.from_pylist([
            _row("R56", "Utah R. Civ. P. 56", "Rule 56. Summary judgment", "A party may move for summary judgment."),
            _row("L1", "Utah Code § 78B-2-309", "Within six years", "An action upon a contract in writing."),
        ]), root / "us_ut_court_rules.parquet")
        cfg = CorpusConfig("vTEST", "http://unused", root, ("ut",), ("court_rules",))
        build_index(cfg, {"snapshot_date": "2026-08-14"}, [root / "us_ut_court_rules.parquet"])
        self.app = create_app(client=DemoClient(delay_per_word=0), model_label="demo",
                              data_dir=root / "cases", law_db=cfg.db_path)
        self.app.state.auth.create_user("dana", PW)
        self.app.state.auth.create_user("other", PW)
        self.client = TestClient(self.app)
        self.client.__enter__()  # runs the lifespan, which starts the worker
        self.login("dana")

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def login(self, username, password=PW, client=None):
        client = client or self.client
        return client.post("/login", data={"username": username, "password": password, "next": "/"},
                           follow_redirects=False)

    def csrf(self, client=None):
        page = (client or self.client).get("/cases/new").text
        return re.search(r'name="csrf" value="([^"]+)"', page).group(1)

    def submit(self, client=None, **overrides):
        client = client or self.client
        data = {**FACTS, "csrf": self.csrf(client), **overrides}
        return client.post("/cases", data=data, follow_redirects=False)

    def finished_case(self, **overrides):
        case_url = self.submit(**overrides).headers["location"]
        self.assertTrue(self.app.state.worker.wait_idle())
        return case_url

    # --- signing in ---------------------------------------------------------

    def test_pages_need_sign_in(self):
        anon = TestClient(self.app)
        for path in ("/", "/cases/new", "/account", "/cases/abc", "/cases/abc/events", "/cases/abc/download.md"):
            with self.subTest(path=path):
                response = anon.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 303)
                self.assertTrue(response.headers["location"].startswith("/login?next="))
        self.assertEqual(anon.post("/cases", data=FACTS, follow_redirects=False).status_code, 303)
        self.assertEqual(anon.get("/login").status_code, 200)

    def test_wrong_password_is_refused(self):
        anon = TestClient(self.app)
        response = self.login("dana", "not the password", client=anon)
        self.assertEqual(response.status_code, 401)
        self.assertIn("Wrong username or password.", response.text)
        self.assertEqual(anon.get("/", follow_redirects=False).status_code, 303)

    def test_session_cookie_is_httponly_and_samesite(self):
        cookie = self.login("dana", client=TestClient(self.app)).headers["set-cookie"]
        self.assertIn("bt_session=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)

    def test_login_redirects_only_within_the_site(self):
        for target, expected in [("/cases/new", "/cases/new"), ("//evil.example", "/"),
                                 ("https://evil.example", "/"), ("/\\evil.example", "/")]:
            with self.subTest(target=target):
                self.assertEqual(safe_next(target), expected)
        response = TestClient(self.app).post(
            "/login", data={"username": "dana", "password": PW, "next": "//evil.example"}, follow_redirects=False)
        self.assertEqual(response.headers["location"], "/")

    def test_logout(self):
        token = re.search(r'name="csrf" value="([^"]+)"', self.client.get("/").text).group(1)
        self.client.post("/logout", data={"csrf": token}, follow_redirects=False)
        self.assertEqual(self.client.get("/", follow_redirects=False).status_code, 303)

    def test_change_password(self):
        response = self.client.post("/account", data={"csrf": self.csrf(), "current": PW, "new": NEW_PW,
                                                     "repeat": NEW_PW})
        self.assertIn("Password changed", response.text)
        self.assertEqual(self.client.get("/").status_code, 200)  # this browser stays signed in
        self.assertEqual(self.login("dana", PW, client=TestClient(self.app)).status_code, 401)
        self.assertEqual(self.login("dana", NEW_PW, client=TestClient(self.app)).status_code, 303)

    def test_change_password_needs_the_current_one(self):
        response = self.client.post("/account", data={"csrf": self.csrf(), "current": "wrong wrong wrong",
                                                     "new": NEW_PW, "repeat": NEW_PW})
        self.assertEqual(response.status_code, 400)
        self.assertIn("current password is wrong", response.text)

    def test_forms_need_csrf_token_and_same_origin(self):
        self.assertEqual(self.submit(csrf="wrong").status_code, 403)
        cross = self.client.post("/cases", data={**FACTS, "csrf": self.csrf()},
                                 headers={"origin": "https://evil.example"}, follow_redirects=False)
        self.assertEqual(cross.status_code, 403)
        self.assertIn("No cases yet", self.client.get("/").text)

    def test_browser_style_origin_headers(self):
        # Browsers send Origin on form posts. Same-site is fine; "null" (what
        # a no-referrer policy produces) and other sites are refused.
        anon = TestClient(self.app)
        form = {"username": "dana", "password": PW, "next": "/"}
        ok = anon.post("/login", data=form, headers={"origin": "http://testserver"}, follow_redirects=False)
        self.assertEqual(ok.status_code, 303)
        for origin in ("null", "http://evil.example"):
            with self.subTest(origin=origin):
                refused = TestClient(self.app).post("/login", data=form, headers={"origin": origin},
                                                    follow_redirects=False)
                self.assertEqual(refused.status_code, 403)
        self.assertEqual(self.client.get("/").headers["referrer-policy"], "same-origin")

    def test_security_headers(self):
        headers = self.client.get("/").headers
        self.assertIn("default-src 'self'", headers["content-security-policy"])
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertEqual(headers["cache-control"], "no-store")

    # --- cases ----------------------------------------------------------------

    def test_pages_render(self):
        for path in ("/", "/cases/new", "/account"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("Sign out", response.text)
        self.assertIn("No cases yet", self.client.get("/").text)
        new = self.client.get("/cases/new").text
        self.assertIn("Draft the complaint and the motion", new)
        self.assertIn("Use my complaint, draft only the motion", new)

    def test_full_run_from_facts(self):
        case_url = self.finished_case()
        page = self.client.get(case_url).text
        self.assertIn("Citation check", page)
        self.assertIn("current as of\n  <strong>2026-08-14</strong>", page)
        # The demo cites the first authority it's given (✅, linked) and an invented section (❌, flagged).
        self.assertRegex(page, r'<a class="cite ok" href="https://le\.utah\.gov/\w+"')
        self.assertIn('<mark class="flag">[⚠ NOT FOUND', page)
        self.assertIn('<mark class="placeholder">[CITATION NEEDED', page)
        self.assertIn("Opposition to Motion for Summary Judgment", page)
        self.assertNotIn("Reply in Support", page)  # 1 round
        self.assertIn("plaintiff model <code>demo</code>", page)
        self.assertNotIn("qwen", page)

        download = self.client.get(f"{case_url}/download.md")
        self.assertEqual(download.status_code, 200)
        self.assertIn("# Battle test — Utah", download.text)

        listing = self.client.get("/").text
        self.assertIn("Dana Whitfield v. Summit Peak Roofing LLC", listing)
        self.assertIn("Complaint and motion drafted", listing)
        self.assertIn('class="status done"', listing)

    def test_pasted_complaint_is_used_as_is(self):
        complaint = "COMPLAINT\nPlaintiff sues under Utah Code § 78B-2-309."
        case_url = self.finished_case(mode="complaint", complaint=complaint)
        page = self.client.get(case_url).text
        self.assertIn("user-supplied", page)
        self.assertIn("Plaintiff sues under", page)
        self.assertIn("Your complaint, motion drafted", self.client.get("/").text)

    def test_cases_are_private_to_their_owner(self):
        case_url = self.finished_case()
        other = TestClient(self.app)
        self.login("other", client=other)
        for path in (case_url, f"{case_url}/events", f"{case_url}/download.md"):
            with self.subTest(path=path):
                self.assertEqual(other.get(path).status_code, 404)
        self.assertIn("No cases yet", other.get("/").text)

    def test_events_stream_stages_and_finish(self):
        case_url = self.finished_case()
        body = self.client.get(f"{case_url}/events").text
        stages = re.findall(r'"title": "([^"]+)"', body)
        self.assertEqual(stages[:3], ["Legal research", "Selecting authorities", "Complaint"])
        self.assertIn("event: text", body)
        self.assertTrue(body.rstrip().endswith('"error": ""}'))  # the final "done" event

    def test_validation_errors_keep_the_form(self):
        response = self.submit(state="NV", plaintiff="Kept Name", timeline="")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Choose a state.", response.text)
        self.assertIn("Fill in at least the plaintiff and what happened.", response.text)
        self.assertIn("Kept Name", response.text)
        self.assertIn("No cases yet", self.client.get("/").text)

    def test_unknown_case_is_404(self):
        self.assertEqual(self.client.get("/cases/nope").status_code, 404)
        self.assertEqual(self.client.get("/cases/nope/download.md").status_code, 404)


@unittest.skipIf(TestClient is None, "web dependencies not installed")
class RenderTest(unittest.TestCase):
    def doc(self, text, checks=()):
        return {"text": text, "checks": list(checks), "unchecked": [], "authorities": []}

    def test_model_output_is_escaped(self):
        html = document_html(self.doc('<script>alert(1)</script> **bold**'))
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("<strong>bold</strong>", html)

    def test_links_longest_citation_first(self):
        section = lambda c: {"citation": c, "source_url": f"https://x.gov/{c[-2:]}"}  # noqa: E731
        html = document_html(self.doc(
            "See § 1-1-12 and § 1-1-1.",
            [{"text": "§ 1-1-1", "status": "verified", "section": section("§ 1-1-1")},
             {"text": "§ 1-1-12", "status": "verified", "section": section("§ 1-1-12")}],
        ))
        self.assertIn('href="https://x.gov/12"', html)
        self.assertIn('href="https://x.gov/-1"', html)
        self.assertEqual(html.count("<a "), 2)

    def test_case_title_uses_names_only(self):
        self.assertEqual(case_title({"plaintiff": "Dana Whitfield, homeowner in Provo",
                                     "defendant": "Summit Peak Roofing LLC (Orem)"}),
                         "Dana Whitfield v. Summit Peak Roofing LLC")
        self.assertEqual(case_title({"plaintiff": "Dana Whitfield"}), "Dana Whitfield")

    def test_facts_text_skips_empty_fields(self):
        text = facts_text({"plaintiff": "A", "defendant": " ", "timeline": "B"})
        self.assertIn("- **Your client (the plaintiff):** A", text)
        self.assertNotIn("defendant", text)


if __name__ == "__main__":
    unittest.main()
