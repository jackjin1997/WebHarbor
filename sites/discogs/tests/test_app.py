"""Behavioral regressions; use an isolated database and synthetic fixtures."""
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from flask.testing import FlaskClient

SITE = Path(__file__).resolve().parents[1]
TEST_DIR = tempfile.TemporaryDirectory(prefix="discogs-tests-")
os.environ["DISCOGS_INSTANCE_DIR"] = TEST_DIR.name
os.environ["DISCOGS_SKIP_SEED"] = "1"
sys.path.insert(0, str(SITE))
m = importlib.import_module("app")


class IsolatedClient(FlaskClient):
    def open(self, *args, **kwargs):
        # Keep request-local login state separate from the fixture's DB context.
        with m.app.app_context():
            return super().open(*args, **kwargs)


m.app.test_client_class = IsolatedClient


class AppTests(unittest.TestCase):
    def setUp(self):
        m.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.context = m.app.app_context()
        self.context.push()
        self.reset_fixtures()

    def reset_fixtures(self):
        m.db.session.rollback()
        m.db.session.remove()
        m.db.drop_all()
        m.db.create_all()
        password = m.bcrypt.generate_password_hash("test-pass").decode()
        m.db.session.add_all([
            m.User(id=1, username="alice", email="alice@example.com", password_hash=password),
            m.User(id=2, username="bob", email="bob@example.com", password_hash=password),
            m.Artist(id=1, name="Bob Marley", slug="bob-marley"),
            m.Artist(id=2, name="Ziggy Marley", slug="ziggy-marley"),
            m.Label(id=1, name="Example Records", slug="example"),
            m.Genre(id=1, name="Reggae", slug="reggae"),
            m.Style(id=1, name="Dub", slug="dub"),
        ])
        m.db.session.flush()
        m.db.session.add_all([
            m.Release(id=1, discogs_id=1001, artist_id=1, title="Studio Session", have_count=10),
            m.Release(id=2, discogs_id=1002, artist_id=2, title="Another Session", have_count=100),
            m.List(id=1, user_id=1, title="Public selection", is_public=True),
            m.List(id=2, user_id=1, title="Private selection", description="Private notes", is_public=False),
        ])
        m.db.session.commit()
        self.client = m.app.test_client()

    def tearDown(self):
        m.db.session.remove()
        self.context.pop()

    def login(self, username="alice", target=None):
        return self.client.post("/login", query_string={"next": target} if target else {},
                                data={"username": username, "password": "test-pass"})

    def listing(self, **overrides):
        data = {"release_id": "1001", "price": "42.00", "currency": "USD",
                "media_condition": "Very Good Plus (VG+)",
                "sleeve_condition": "Very Good Plus (VG+)", "shipping_from": "United Kingdom"}
        data.update(overrides)
        return self.client.post("/sell", data=data)

    def test_private_lists_are_visible_only_to_owner(self):
        for identity in (None, "bob", "alice"):
            with self.subTest(identity=identity):
                self.client = m.app.test_client()
                if identity:
                    self.login(identity)
                html = self.client.get("/user/alice/lists").get_data(as_text=True)
                self.assertIn("Public selection", html)
                self.assertEqual("Private selection" in html, identity == "alice")
                self.assertEqual("Private notes" in html, identity == "alice")
                self.assertEqual(self.client.get("/list/2").status_code, 200 if identity == "alice" else 403)

    def test_other_user_cannot_add_to_private_list(self):
        self.login("bob")
        self.assertEqual(self.client.post("/list/2/add", data={"release_id": 1}).status_code, 403)
        self.assertEqual(m.ListItem.query.count(), 0)

    def test_review_for_missing_release_has_no_side_effect(self):
        self.login()
        response = self.client.post("/review", data={"release_id": 999999, "body": "Must not persist"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(m.Review.query.count(), 0)

    def test_invalid_sale_values_do_not_change_database(self):
        cases = [{"price": value} for value in ("-1", "0", "nan", "inf", "1e309", "1.001", "999999999999")]
        cases += [{"currency": "ZZZ"}, {"media_condition": "invented"},
                  {"sleeve_condition": "invented"}, {"shipping_from": ""}]
        for data in cases:
            with self.subTest(data=data):
                self.reset_fixtures()
                self.login()
                response = self.listing(**data)
                self.assertLess(response.status_code, 500)
                self.assertEqual(m.Listing.query.count(), 0)
                self.assertFalse(m.db.session.get(m.User, 1).is_seller)
                self.assertEqual(m.db.session.get(m.Release, 1).num_for_sale, 0)

    def test_valid_sale_has_bound_release_and_currency(self):
        self.login()
        self.assertEqual(self.listing(currency="GBP").status_code, 302)
        row = m.Listing.query.one()
        self.assertEqual((row.user_id, row.release_id, row.price, row.currency), (1, 1, 42, "GBP"))
        self.assertTrue(m.db.session.get(m.User, 1).is_seller)

    def test_login_rejects_foreign_or_ambiguous_returns(self):
        for target in ("https://foreign.invalid/", "//foreign.invalid/", "/\\foreign.invalid/",
                       "javascript:alert(1)", " https://foreign.invalid/", "/%5cforeign.invalid/"):
            with self.subTest(target=target):
                self.client = m.app.test_client()
                self.assertEqual(self.login(target=target).location, "/")

    def test_login_preserves_local_return(self):
        self.assertEqual(self.login(target="/list/1?view=items").location, "/list/1?view=items")

    def test_mutations_ignore_foreign_referrer(self):
        self.login()
        response = self.client.post("/collection/add", data={"release_id": 1},
                                    headers={"Referer": "https://foreign.invalid/"})
        self.assertEqual(response.location, "/release/1001")

    def test_logout_requires_post_and_csrf(self):
        self.login()
        self.assertEqual(self.client.get("/logout").status_code, 405)
        m.app.config["WTF_CSRF_ENABLED"] = True
        self.assertEqual(self.client.post("/logout").status_code, 400)
        self.assertEqual(self.client.get("/settings").status_code, 200)

    def test_invalid_pages_do_not_crash(self):
        for route in ("/search", "/marketplace", "/lists", "/user/alice/collection", "/user/alice/wantlist"):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route, query_string={"page": "oops"}).status_code, 200)

    def test_empty_entity_browse_is_populated(self):
        self.assertIn("Bob Marley", self.client.get("/search?type=artist").get_data(as_text=True))
        self.assertIn("Example Records", self.client.get("/search?type=label").get_data(as_text=True))

    def test_relevance_prefers_full_token_match_over_popularity(self):
        results = m.search_releases("Bob Marley").items
        self.assertEqual([row.id for row in results], [1, 2])

    def test_facets_preserve_other_filters(self):
        html = self.client.get("/search?q=Bob&genre=reggae&year=1978").get_data(as_text=True)
        from html.parser import HTMLParser
        from urllib.parse import parse_qs, urlsplit

        class Links(HTMLParser):
            def __init__(self):
                super().__init__()
                self.urls = []

            def handle_starttag(self, tag, attrs):
                if tag in ("a", "option"):
                    values = dict(attrs)
                    self.urls.append(values.get("href", values.get("value", "")))

        links = Links()
        links.feed(html)
        choices = [parse_qs(urlsplit(url).query) for url in links.urls if "style=dub" in url]
        self.assertTrue(choices)
        self.assertEqual(choices[0].get("genre"), ["reggae"])
        self.assertEqual(choices[0].get("year"), ["1978"])

    def test_collection_validation_and_duplicate_add_are_atomic(self):
        self.login()
        for field, value in (("folder", "invalid"), ("media_condition", "invalid"), ("sleeve_condition", "invalid")):
            self.client.post("/collection/add", data={"release_id": 1, field: value})
            self.assertEqual(m.CollectionItem.query.count(), 0)
            self.assertEqual(m.db.session.get(m.Release, 1).have_count, 10)
        for _ in range(2):
            self.client.post("/collection/add", data={"release_id": 1, "folder": "Vinyl"})
        self.assertEqual(m.CollectionItem.query.count(), 1)
        self.assertEqual(m.db.session.get(m.Release, 1).have_count, 11)

    def test_wantlist_rejects_unknown_grade(self):
        self.login()
        self.client.post("/wantlist/add", data={"release_id": 1, "min_grade": "invalid"})
        self.assertEqual(m.WantlistItem.query.count(), 0)

    def test_registration_rejects_invalid_identity_and_overlong_password(self):
        for overrides in ({"email": "@"}, {"username": "../escape"}, {"password": "a" * 73}):
            with self.subTest(overrides=overrides):
                self.reset_fixtures()
                data = {"username": "new_user", "email": "new@example.com", "password": "test-pass"}
                data.update(overrides)
                response = self.client.post("/register", data=data)
                self.assertLess(response.status_code, 500)
                self.assertEqual(m.User.query.count(), 2)


if __name__ == "__main__":
    unittest.main()
