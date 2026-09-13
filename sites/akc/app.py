"""American Kennel Club mirror for WebHarbor.

Breed, article, event and sport content is built from the captured akc.org
source data in data/ (see seed_data.py). Accounts, saved breeds and event
registrations are local benchmark state.
"""
from __future__ import annotations

import os
import re
from datetime import date
from functools import wraps

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

import seed_data

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, instance_path=os.path.join(BASE_DIR, "instance"))
app.config["SECRET_KEY"] = "webharbor-akc-dev-key"
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(BASE_DIR, 'instance', 'akc.db')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

STOP_WORDS = {"the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "with", "dog", "dogs"}
# Fixed "today" so event listings and any date-sensitive copy stay identical
# across runs. Matches the capture date of data/content.json.
REFERENCE_DATE = date(2026, 9, 13)

BREED_GROUP_ORDER = [
    "Sporting Group", "Hound Group", "Working Group", "Terrier Group",
    "Toy Group", "Non-Sporting Group", "Herding Group",
]

breed_characteristics = db.Table(
    "breed_characteristics",
    db.Column("breed_id", db.Integer, db.ForeignKey("breed.id"), primary_key=True),
    db.Column("characteristic_id", db.Integer, db.ForeignKey("characteristic.id"), primary_key=True),
)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    household = db.Column(db.String(80), default="Apartment")
    activity_level = db.Column(db.String(80), default="Moderate")
    experience = db.Column(db.String(80), default="First-time owner")


class Characteristic(db.Model):
    """An AKC characteristic collection, e.g. "Best Dogs for Apartment Dwellers"."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    slug = db.Column(db.String(120), unique=True, nullable=False)


class Breed(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(80), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    plural = db.Column(db.String(140), default="")
    nicknames = db.Column(db.String(140), default="")
    akc_code = db.Column(db.String(16), default="")
    group = db.Column(db.String(80), nullable=False)
    origin = db.Column(db.String(120), default="")
    year_recognized = db.Column(db.String(16), default="")
    life_expectancy = db.Column(db.String(60), nullable=False)
    height = db.Column(db.String(160), nullable=False)
    weight = db.Column(db.String(160), nullable=False)
    temperament = db.Column(db.String(200), nullable=False)
    popularity_rank = db.Column(db.Integer)
    popularity_year = db.Column(db.Integer)
    blurb = db.Column(db.Text, default="")
    about = db.Column(db.Text, default="")
    history = db.Column(db.Text, default="")

    traits = db.relationship("BreedTrait", backref="breed", order_by="BreedTrait.position")
    attributes = db.relationship("BreedAttribute", backref="breed", order_by="BreedAttribute.position")
    colors_and_markings = db.relationship("BreedColor", backref="breed", order_by="BreedColor.position")
    facts = db.relationship("BreedFact", backref="breed", order_by="BreedFact.position")
    care = db.relationship("BreedCare", backref="breed", order_by="BreedCare.position")
    standard = db.relationship("BreedStandard", backref="breed", order_by="BreedStandard.position")
    photos = db.relationship("BreedPhoto", backref="breed", order_by="BreedPhoto.position")
    characteristics = db.relationship(
        "Characteristic", secondary=breed_characteristics, backref="breeds"
    )

    def trait(self, key: str):
        for row in self.traits:
            if row.key == key:
                return row
        return None

    def trait_score(self, key: str, default: int = 3) -> int:
        row = self.trait(key)
        return row.score if row else default

    def attribute(self, key: str) -> str:
        for row in self.attributes:
            if row.key == key:
                return row.value
        return ""

    def photo(self, role: str):
        for row in self.photos:
            if row.role == role:
                return row
        return self.photos[0] if self.photos else None

    @property
    def colors(self):
        return [row for row in self.colors_and_markings if row.kind == "color"]

    @property
    def markings(self):
        return [row for row in self.colors_and_markings if row.kind == "marking"]

    @property
    def about_paragraphs(self) -> list[str]:
        return [p for p in (self.about or "").split("\n\n") if p]

    @property
    def history_paragraphs(self) -> list[str]:
        return [p for p in (self.history or "").split("\n\n") if p]


class BreedTrait(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    breed_id = db.Column(db.Integer, db.ForeignKey("breed.id"), nullable=False)
    key = db.Column(db.String(80), nullable=False)
    label = db.Column(db.String(120), nullable=False)
    score = db.Column(db.Integer, nullable=False)
    low_label = db.Column(db.String(120), default="")
    mid_label = db.Column(db.String(120), default="")
    high_label = db.Column(db.String(120), default="")
    description = db.Column(db.Text, default="")
    position = db.Column(db.Integer, default=0)


class BreedAttribute(db.Model):
    """A pick-from-a-set trait such as coat type or coat length."""

    id = db.Column(db.Integer, primary_key=True)
    breed_id = db.Column(db.Integer, db.ForeignKey("breed.id"), nullable=False)
    key = db.Column(db.String(80), nullable=False)
    label = db.Column(db.String(120), nullable=False)
    value = db.Column(db.String(160), default="")
    options = db.Column(db.String(400), default="")
    description = db.Column(db.Text, default="")
    position = db.Column(db.Integer, default=0)


class BreedColor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    breed_id = db.Column(db.Integer, db.ForeignKey("breed.id"), nullable=False)
    kind = db.Column(db.String(16), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(16), default="")
    standard = db.Column(db.Boolean, default=False)
    position = db.Column(db.Integer, default=0)


class BreedFact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    breed_id = db.Column(db.Integer, db.ForeignKey("breed.id"), nullable=False)
    text = db.Column(db.Text, nullable=False)
    position = db.Column(db.Integer, default=0)


class BreedCare(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    breed_id = db.Column(db.Integer, db.ForeignKey("breed.id"), nullable=False)
    section = db.Column(db.String(40), nullable=False)
    label = db.Column(db.String(60), nullable=False)
    body = db.Column(db.Text, nullable=False)
    position = db.Column(db.Integer, default=0)

    @property
    def paragraphs(self) -> list[str]:
        return [p for p in (self.body or "").split("\n\n") if p]


class BreedStandard(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    breed_id = db.Column(db.Integer, db.ForeignKey("breed.id"), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    body = db.Column(db.Text, nullable=False)
    position = db.Column(db.Integer, default=0)

    @property
    def paragraphs(self) -> list[str]:
        return [p for p in (self.body or "").split("\n\n") if p]


class BreedPhoto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    breed_id = db.Column(db.Integer, db.ForeignKey("breed.id"), nullable=False)
    file = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(24), default="")
    alt = db.Column(db.String(400), default="")
    credit = db.Column(db.String(200), default="")
    position = db.Column(db.Integer, default=0)


class Sport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(120), unique=True, nullable=False)
    position = db.Column(db.Integer, default=0)


class Article(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(160), unique=True, nullable=False)
    title = db.Column(db.String(240), nullable=False)
    category = db.Column(db.String(80), nullable=False)
    category_slug = db.Column(db.String(80), default="")
    author = db.Column(db.String(160), nullable=False)
    published_on = db.Column(db.Date)
    updated_on = db.Column(db.Date)
    read_minutes = db.Column(db.Integer, default=1)
    word_count = db.Column(db.Integer, default=0)
    summary = db.Column(db.Text, default="")
    body = db.Column(db.Text, default="")

    @property
    def paragraphs(self) -> list[str]:
        return [p for p in (self.body or "").split("\n\n") if p]


class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(160), unique=True, nullable=False)
    title = db.Column(db.String(240), nullable=False)
    sport = db.Column(db.String(80), default="")
    date_display = db.Column(db.String(80), default="")
    starts_on = db.Column(db.Date)
    ends_on = db.Column(db.Date)
    city = db.Column(db.String(120), default="")
    region = db.Column(db.String(80), default="")
    summary = db.Column(db.Text, default="")
    body = db.Column(db.Text, default="")

    @property
    def paragraphs(self) -> list[str]:
        return [p for p in (self.body or "").split("\n\n") if p]

    @property
    def location(self) -> str:
        return ", ".join(part for part in (self.city, self.region) if part)

    @property
    def upcoming(self) -> bool:
        return bool(self.starts_on and self.starts_on >= REFERENCE_DATE)


class SavedBreed(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    breed_id = db.Column(db.Integer, db.ForeignKey("breed.id"), nullable=False)
    note = db.Column(db.String(240), default="")
    breed = db.relationship("Breed")


class EventRegistration(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    dog_name = db.Column(db.String(80), nullable=False)
    class_name = db.Column(db.String(80), nullable=False)
    event = db.relationship("Event")


MODELS = {
    "User": User, "Breed": Breed, "BreedTrait": BreedTrait,
    "BreedAttribute": BreedAttribute, "BreedColor": BreedColor,
    "BreedFact": BreedFact, "BreedCare": BreedCare,
    "BreedStandard": BreedStandard, "BreedPhoto": BreedPhoto,
    "Characteristic": Characteristic, "Sport": Sport, "Article": Article,
    "Event": Event, "SavedBreed": SavedBreed,
    "EventRegistration": EventRegistration,
}


def current_user() -> User | None:
    user_id = session.get("user_id")
    return db.session.get(User, user_id) if user_id else None


@app.context_processor
def inject_globals():
    return {
        "current_user": current_user(),
        "reference_date": REFERENCE_DATE,
        "nav_groups": group_order(),
        "breed_options": Breed.query.order_by(Breed.name).all(),
    }


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Please sign in to continue.", "info")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def tokens(query: str) -> list[str]:
    return [
        token
        for token in re.split(r"\W+", query.lower())
        if len(token) > 1 and token not in STOP_WORDS
    ]


def scored_search(query: str, rows, fields: list[str]):
    parts = tokens(query)
    if not parts:
        return list(rows)
    scored = []
    for row in rows:
        haystack = " ".join(str(getattr(row, field, "") or "") for field in fields).lower()
        score = sum(1 for part in parts if part in haystack)
        if score:
            scored.append((score, row))
    scored.sort(key=lambda item: (-item[0], getattr(item[1], "name", getattr(item[1], "title", ""))))
    return [row for _, row in scored]


def group_order() -> list[str]:
    present = {row[0] for row in db.session.query(Breed.group).distinct()}
    ordered = [g for g in BREED_GROUP_ORDER if g in present]
    return ordered + sorted(present - set(ordered))


@app.route("/")
def index():
    popular = (
        Breed.query.filter(Breed.popularity_rank.isnot(None))
        .order_by(Breed.popularity_rank)
        .limit(6)
        .all()
    )
    events = (
        Event.query.filter(Event.starts_on >= REFERENCE_DATE)
        .order_by(Event.starts_on)
        .limit(4)
        .all()
    )
    articles = Article.query.order_by(Article.published_on.desc()).limit(6).all()
    sports = Sport.query.order_by(Sport.position).all()
    characteristics = Characteristic.query.order_by(Characteristic.name).all()
    return render_template(
        "index.html", popular=popular, events=events, articles=articles,
        sports=sports, characteristics=characteristics,
        breed_count=Breed.query.count(),
    )


@app.route("/breeds")
def breeds():
    query = request.args.get("q", "").strip()
    group = request.args.get("group", "").strip()
    characteristic = request.args.get("characteristic", "").strip()
    sort = request.args.get("sort", "name").strip()

    rows = Breed.query.order_by(Breed.name).all()
    if group:
        rows = [breed for breed in rows if breed.group == group]
    if characteristic:
        rows = [
            breed for breed in rows
            if any(c.slug == characteristic for c in breed.characteristics)
        ]
    if query:
        rows = scored_search(query, rows, ["name", "group", "temperament", "blurb"])
    elif sort == "popularity":
        rows = sorted(
            rows, key=lambda b: (b.popularity_rank is None, b.popularity_rank or 0, b.name)
        )

    return render_template(
        "breeds.html", breeds=rows, groups=group_order(),
        characteristics=Characteristic.query.order_by(Characteristic.name).all(),
        query=query, group=group, characteristic=characteristic, sort=sort,
    )


@app.route("/breeds/<slug>")
def breed_detail(slug):
    breed = Breed.query.filter_by(slug=slug).first_or_404()
    related = (
        Breed.query.filter(Breed.group == breed.group, Breed.slug != breed.slug)
        .order_by(Breed.name)
        .limit(4)
        .all()
    )
    saved = False
    user = current_user()
    if user:
        saved = SavedBreed.query.filter_by(user_id=user.id, breed_id=breed.id).first() is not None
    return render_template("breed_detail.html", breed=breed, related=related, saved=saved)


@app.route("/breeds/<slug>/save", methods=["POST"])
@login_required
def save_breed(slug):
    breed = Breed.query.filter_by(slug=slug).first_or_404()
    user = current_user()
    existing = SavedBreed.query.filter_by(user_id=user.id, breed_id=breed.id).first()
    if not existing:
        db.session.add(SavedBreed(user_id=user.id, breed_id=breed.id, note=request.form.get("note", "")))
        db.session.commit()
        flash(f"{breed.name} was saved to your profile.", "success")
    return redirect(url_for("breed_detail", slug=slug))


# The selector scores against the AKC trait axes the breed pages publish.
SELECTOR_AXES = [
    ("energy", "energy_level", "Energy Level"),
    ("grooming", "coat_grooming_frequency", "Coat Grooming Frequency"),
    ("children", "good_with_young_children", "Good With Young Children"),
    ("training", "trainability_level", "Trainability Level"),
]


@app.route("/breed-selector", methods=["GET", "POST"])
def breed_selector():
    matches = []
    answers = {}
    if request.method == "POST":
        answers = {"home": request.form.get("home", "apartment")}
        for field, _key, _label in SELECTOR_AXES:
            try:
                answers[field] = max(1, min(5, int(request.form.get(field, "3"))))
            except ValueError:
                answers[field] = 3

        apartment = Characteristic.query.filter_by(
            slug="best-dogs-for-apartment-dwellers"
        ).first()
        for breed in Breed.query.all():
            score = 0
            for field, key, _label in SELECTOR_AXES:
                score += 5 - abs(breed.trait_score(key) - answers[field])
            if answers["home"] == "apartment":
                score += 5 - abs(breed.trait_score("adaptability_level") - 5)
                if apartment and apartment in breed.characteristics:
                    score += 4
            matches.append((score, breed))
        matches.sort(key=lambda item: (-item[0], item[1].name))
        matches = matches[:8]
    return render_template(
        "selector.html", matches=matches, answers=answers, axes=SELECTOR_AXES
    )


COMPARE_AXES = [
    "energy_level", "coat_grooming_frequency", "shedding_level",
    "trainability_level", "good_with_young_children", "good_with_other_dogs",
    "barking_level", "adaptability_level",
]


@app.route("/compare")
def compare():
    selected = [slug for slug in request.args.getlist("breed") if slug]
    breeds_for_picker = Breed.query.order_by(Breed.name).all()
    compared = (
        Breed.query.filter(Breed.slug.in_(selected)).order_by(Breed.name).all()
        if selected else []
    )
    labels = {}
    for breed in compared:
        for row in breed.traits:
            labels.setdefault(row.key, row.label)
    axes = [(key, labels[key]) for key in COMPARE_AXES if key in labels]
    return render_template(
        "compare.html", breeds=breeds_for_picker, compared=compared,
        selected=selected, axes=axes,
    )


@app.route("/articles")
def articles():
    category = request.args.get("category", "")
    rows = Article.query.order_by(Article.published_on.desc()).all()
    if category:
        rows = [article for article in rows if article.category_slug == category]
    categories = [
        (row[0], row[1])
        for row in db.session.query(Article.category_slug, Article.category)
        .distinct()
        .order_by(Article.category)
    ]
    return render_template(
        "articles.html", articles=rows, categories=categories, category=category
    )


@app.route("/articles/<slug>")
def article_detail(slug):
    article = Article.query.filter_by(slug=slug).first_or_404()
    related = (
        Article.query.filter(
            Article.category_slug == article.category_slug, Article.slug != article.slug
        )
        .order_by(Article.published_on.desc())
        .limit(3)
        .all()
    )
    return render_template("article_detail.html", article=article, related=related)


@app.route("/events")
def events():
    sport = request.args.get("sport", "")
    region = request.args.get("region", "")
    rows = Event.query.order_by(Event.starts_on).all()
    if sport:
        rows = [event for event in rows if event.sport == sport]
    if region:
        rows = [event for event in rows if event.region == region]
    sports = sorted({event.sport for event in Event.query.all() if event.sport})
    regions = sorted({event.region for event in Event.query.all() if event.region})
    return render_template(
        "events.html", events=rows, sports=sports, regions=regions,
        sport=sport, region=region,
    )


@app.route("/events/<slug>", methods=["GET", "POST"])
def event_detail(slug):
    event = Event.query.filter_by(slug=slug).first_or_404()
    if request.method == "POST":
        if not current_user():
            flash("Sign in before registering for an event.", "info")
            return redirect(url_for("login", next=request.path))
        reg = EventRegistration(
            user_id=current_user().id,
            event_id=event.id,
            dog_name=request.form.get("dog_name", "").strip() or "TBD",
            class_name=request.form.get("class_name", "Beginner Novice"),
        )
        db.session.add(reg)
        db.session.commit()
        flash("Registration saved in your AKC profile.", "success")
        return redirect(url_for("account"))
    return render_template("event_detail.html", event=event)


@app.route("/sports")
def sports():
    rows = Sport.query.order_by(Sport.position).all()
    upcoming = Event.query.order_by(Event.starts_on).all()
    return render_template("sports.html", sports=rows, events=upcoming)


@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    breed_results = scored_search(
        query, Breed.query.all(), ["name", "group", "temperament", "blurb", "nicknames"]
    )[:8] if query else []
    article_results = scored_search(
        query, Article.query.all(), ["title", "category", "summary", "body", "author"]
    )[:8] if query else []
    event_results = scored_search(
        query, Event.query.all(), ["title", "sport", "city", "region", "summary"]
    )[:8] if query else []
    return render_template(
        "search.html",
        query=query,
        breed_results=breed_results,
        article_results=article_results,
        event_results=event_results,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").lower().strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            session["user_id"] = user.id
            flash(f"Welcome back, {user.display_name}.", "success")
            return redirect(request.args.get("next") or url_for("account"))
        flash("Email or password did not match.", "error")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").lower().strip()
        username = request.form.get("username", "").strip()
        if User.query.filter((User.email == email) | (User.username == username)).first():
            flash("That email or username is already registered.", "error")
        else:
            user = User(
                email=email,
                username=username,
                display_name=request.form.get("display_name", username),
                password_hash=generate_password_hash(request.form.get("password", "TestPass123!")),
                household=request.form.get("household", "Apartment"),
                activity_level=request.form.get("activity_level", "Moderate"),
                experience=request.form.get("experience", "First-time owner"),
            )
            db.session.add(user)
            db.session.commit()
            session["user_id"] = user.id
            flash("Your AKC profile is ready.", "success")
            return redirect(url_for("account"))
    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("index"))


@app.route("/account")
@login_required
def account():
    user = current_user()
    saved = SavedBreed.query.filter_by(user_id=user.id).all()
    registrations = EventRegistration.query.filter_by(user_id=user.id).all()
    return render_template("account.html", user=user, saved=saved, registrations=registrations)


@app.route("/account/profile", methods=["POST"])
@login_required
def update_profile():
    user = current_user()
    user.household = request.form.get("household", user.household)
    user.activity_level = request.form.get("activity_level", user.activity_level)
    user.experience = request.form.get("experience", user.experience)
    db.session.commit()
    flash("Profile preferences updated.", "success")
    return redirect(url_for("account"))


@app.errorhandler(404)
def not_found(_error):
    return render_template("404.html"), 404


@app.route("/_health")
def health():
    return {"ok": True, "site": "akc"}


with app.app_context():
    os.makedirs(app.instance_path, exist_ok=True)
    db.create_all()
    if Breed.query.count() == 0:
        seed_data.build(db, MODELS)
    if User.query.filter_by(email="alice.j@test.com").first() is None:
        seed_data.build_users(db, MODELS, generate_password_hash)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
