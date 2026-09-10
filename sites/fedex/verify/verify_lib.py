#!/usr/bin/env python3
"""Shared deterministic verifier harness for the FedEx task contract."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import ParseResult, parse_qs, urlparse


SITE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SITE_ROOT))

from rate_quote import QuoteRequest, verify_quote_token  # noqa: E402


SITE = "fedex"


def configured_local_ports() -> frozenset[int]:
    override = os.environ.get("WH_VERIFIER_SITE_PORTS", "").strip()
    if override:
        try:
            return frozenset(int(value) for value in override.split(","))
        except ValueError:
            return frozenset()

    try:
        tasks = [
            json.loads(line)
            for line in (SITE_ROOT / "tasks.jsonl").read_text().splitlines()
            if line.strip()
        ]
        ports = frozenset(urlparse(str(task.get("web", ""))).port for task in tasks)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return frozenset()
    if len(ports) != 1:
        return frozenset()
    return frozenset(port for port in ports if port is not None)


ALLOWED_LOCAL_PORTS = configured_local_ports()


@dataclass(frozen=True)
class VerifyArgs:
    run_dir: str
    initial_db: str = ""
    after_db: str = ""
    container: str = os.environ.get("WH_CONTAINER", "wh-review")
    no_llm: bool = False


@dataclass(frozen=True)
class TaskSpec:
    required_paths: tuple[str, ...]
    answer_groups: tuple[tuple[str, ...], ...]
    state_check: Callable[[VerifyArgs], tuple[bool, str]] | None = None
    quote_request: QuoteRequest | None = None
    login_email: str | None = None


def parse_args() -> VerifyArgs:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run_dir", required=True)
    parser.add_argument("--initial_db", default="")
    parser.add_argument("--after_db", default="")
    parser.add_argument("--container", default=os.environ.get("WH_CONTAINER", "wh-review"))
    parser.add_argument("--no_llm", nargs="?", const="true", default="false")
    values = parser.parse_args()
    return VerifyArgs(
        run_dir=values.run_dir,
        initial_db=values.initial_db,
        after_db=values.after_db,
        container=values.container,
        no_llm=str(values.no_llm).casefold() in {"1", "true", "yes", "on"},
    )


def load_run(run_dir: str) -> dict:
    return json.loads((Path(run_dir) / "trajectory.json").read_text())


def normalized(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def trusted_local_url(raw_url: str) -> ParseResult | None:
    try:
        parsed = urlparse(raw_url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "http"
        or (parsed.hostname or "").casefold() not in {"localhost", "127.0.0.1"}
        or parsed.username is not None
        or parsed.password is not None
        or port not in ALLOWED_LOCAL_PORTS
    ):
        return None
    return parsed


def same_origin(left: ParseResult, right: ParseResult) -> bool:
    return (
        left.scheme.casefold(),
        (left.hostname or "").casefold(),
        left.port,
    ) == (
        right.scheme.casefold(),
        (right.hostname or "").casefold(),
        right.port,
    )


def navigated_to(trajectory: dict, required_location: str) -> bool:
    expected = urlparse(required_location)
    expected_query = parse_qs(expected.query, keep_blank_values=True)
    for step in trajectory.get("steps", []):
        actual = trusted_local_url(str(step.get("url", "")))
        if actual is None or actual.path.casefold() != expected.path.casefold():
            continue
        actual_query = parse_qs(actual.query, keep_blank_values=True)
        if all(actual_query.get(key) == values for key, values in expected_query.items()):
            return True
    return False


def final_answer(trajectory: dict) -> str:
    return str(trajectory.get("final_answer") or "").strip()


def quote_request_matches(trajectory: dict, expected: QuoteRequest) -> tuple[bool, str]:
    candidates: list[QuoteRequest] = []
    for step in trajectory.get("steps", []):
        action_result = step.get("action_result") or {}
        before = trusted_local_url(str(step.get("url", "")))
        after = trusted_local_url(str(action_result.get("url_after", "")))
        is_successful_submit = (
            before is not None
            and after is not None
            and same_origin(before, after)
            and str(step.get("action", "")).casefold() == "click"
            and action_result.get("success") is True
            and before.path == "/rate-estimate"
            and not parse_qs(before.query).get("quote")
            and after.path == "/rate-estimate"
        )
        if not is_successful_submit:
            continue
        token = parse_qs(after.query).get("quote", [""])[0]
        candidate = verify_quote_token(token)
        if candidate is not None:
            candidates.append(candidate)
    matched = expected in candidates
    return matched, f"expected={expected!r}; successful_signed_submissions={candidates!r}"


def login_matches(trajectory: dict, expected_email: str) -> tuple[bool, str]:
    seen_email = False
    seen_password = False
    successful_submits = 0
    entered_emails: set[str] = set()
    for step in trajectory.get("steps", []):
        before = trusted_local_url(str(step.get("url", "")))
        if before is None or before.path != "/login":
            continue
        action = str(step.get("action", "")).casefold()
        params = step.get("params") or {}
        locator = " ".join(
            str(params.get(key, ""))
            for key in ("selector", "label", "name", "field", "role")
        ).casefold()
        value = str(params.get("text", params.get("value", "")))
        if action in {"input", "fill", "type"} and "email" in locator:
            entered_emails.add(value.casefold())
            seen_email = value.casefold() == expected_email.casefold()
        if action in {"input", "fill", "type"} and "password" in locator:
            seen_password = value == "TestPass123!"
        action_result = step.get("action_result") or {}
        after = trusted_local_url(str(action_result.get("url_after", "")))
        if (
            action == "click"
            and action_result.get("success") is True
            and after is not None
            and same_origin(before, after)
            and after.path == "/account"
            and seen_email
            and seen_password
        ):
            successful_submits += 1
    matched = successful_submits > 0
    evidence = (
        f"expected_email={expected_email!r}; entered_emails={sorted(entered_emails)!r}; "
        f"password_entered={seen_password}; successful_matching_submits={successful_submits}"
    )
    return matched, evidence


def answer_contains(answer: str, alternative: str) -> bool:
    normalized_answer = normalized(answer)
    normalized_alternative = normalized(alternative)
    left_boundary = r"(?<![a-z0-9])" if normalized_alternative[:1].isalnum() else ""
    right_boundary = r"(?![a-z0-9])" if normalized_alternative[-1:].isalnum() else ""
    return bool(re.search(left_boundary + re.escape(normalized_alternative) + right_boundary, normalized_answer))


def fetch_db(container: str, kind: str) -> str | None:
    fd, target = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    source = f"{container}:/opt/WebSyn/{SITE}/{kind}/{SITE}.db"
    result = subprocess.run(["docker", "cp", source, target], capture_output=True, text=True)
    if result.returncode == 0:
        return target
    Path(target).unlink(missing_ok=True)
    return None


def resolve_db(explicit_path: str, container: str, kind: str) -> str | None:
    if explicit_path:
        return explicit_path if Path(explicit_path).is_file() else None
    return fetch_db(container, kind)


def query_one(db_path: str, sql: str, params: tuple = ()) -> tuple | None:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(sql, params).fetchone()
    finally:
        connection.close()


def query_all(db_path: str, sql: str, params: tuple = ()) -> list[tuple]:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(sql, params).fetchall()
    finally:
        connection.close()


def unchanged_except_inserts(
    initial_db: str,
    after_db: str,
    allowed_inserts: dict[str, tuple[str, object]],
) -> tuple[bool, str]:
    tables_sql = "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'sqlite_sequence'"
    initial_tables = {row[0] for row in query_all(initial_db, tables_sql)}
    after_tables = {row[0] for row in query_all(after_db, tables_sql)}
    if initial_tables != after_tables or not set(allowed_inserts).issubset(initial_tables):
        return False, f"initial_tables={sorted(initial_tables)!r}; after_tables={sorted(after_tables)!r}"

    changed_tables = []
    for table in sorted(initial_tables):
        where = ""
        params: tuple = ()
        if table in allowed_inserts:
            key_column, key_value = allowed_inserts[table]
            where = f' WHERE "{key_column}" != ? OR "{key_column}" IS NULL'
            params = (key_value,)
        initial_rows = sorted(query_all(initial_db, f'SELECT * FROM "{table}"{where}', params), key=repr)
        after_rows = sorted(query_all(after_db, f'SELECT * FROM "{table}"{where}', params), key=repr)
        if initial_rows != after_rows:
            changed_tables.append(table)
    return not changed_tables, f"unexpected_changed_tables={changed_tables!r}"


class Judge:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.passed = True
        self.reason = ""
        self.evidence: list[str] = []

    def check(self, name: str, condition: bool, evidence: str) -> None:
        if condition:
            self.evidence.append(f"[PASS] {name}: {evidence}")
            return
        self.passed = False
        if not self.reason:
            self.reason = name
        self.evidence.append(f"[FAIL] {name}: {evidence}")

    def emit(self) -> None:
        print(json.dumps({"task_id": self.task_id, "pass": self.passed, "reason": self.reason, "evidence": self.evidence}, indent=2))
        raise SystemExit(0 if self.passed else 1)


def shipment_state_matches(args: VerifyArgs) -> tuple[bool, str]:
    initial_db = resolve_db(args.initial_db, args.container, "instance_seed")
    after_db = resolve_db(args.after_db, args.container, "instance")
    if not initial_db or not after_db:
        return False, "initial or after-state database unavailable"
    initial_row = query_one(initial_db, "SELECT COUNT(*) FROM shipments WHERE tracking_number = ?", ("FDX260000061",))
    row = query_one(
        after_db,
        """SELECT COUNT(*), u.email, s.shipment_code, s.tracking_number,
                  s.service_slug, s.package_type, s.package_weight,
                  s.origin_city, s.origin_state, s.destination_city,
                  s.destination_state, s.recipient_name, s.declared_value,
                  s.total_cost, s.fulfillment_mode, s.pickup_location_slug,
                  s.pickup_window, s.status, s.created_on, s.invoice_number,
                  s.reference_label
             FROM shipments s JOIN users u ON u.id = s.user_id
            WHERE s.tracking_number = ?""",
        ("FDX260000061",),
    )
    expected = (
        1,
        "alice.j@test.com",
        "SH-260061",
        "FDX260000061",
        "fedex-2day",
        "Box",
        6.0,
        "Seattle",
        "WA",
        "Boston",
        "MA",
        "Alex Brown",
        240.0,
        46.6,
        "dropoff",
        "seattle-downtown-wa",
        "",
        "Label created",
        "2026-06-04",
        "INV-260061",
        "Local demo shipment",
    )
    invoice = query_one(
        after_db,
        """SELECT i.invoice_number, u.email, s.tracking_number, i.billed_on,
                  i.due_date, i.amount, i.status
             FROM invoices i
             JOIN users u ON u.id = i.user_id
             JOIN shipments s ON s.id = i.shipment_id
            WHERE i.invoice_number = ?""",
        ("INV-260061",),
    )
    expected_invoice = (
        "INV-260061",
        "alice.j@test.com",
        "FDX260000061",
        "2026-06-04",
        "2026-06-18",
        46.6,
        "Open",
    )
    tracking = query_one(
        after_db,
        """SELECT t.id, t.tracking_number, u.email, t.recipient_name,
                  t.sender_name, t.origin_city, t.origin_state,
                  t.destination_city, t.destination_state, t.service_slug,
                  t.package_type, t.weight_lb, t.status_stage, t.status_summary,
                  t.ship_date, t.estimated_delivery, t.latest_scan,
                  t.package_count, t.signature_required, t.dropoff_location_slug
             FROM tracking_records t
             JOIN users u ON u.id = t.user_id
            WHERE t.tracking_number = ?""",
        ("FDX260000061",),
    )
    expected_tracking = (
        "FDX260000061",
        "alice.j@test.com",
        "Alex Brown",
        "Alice Johnson",
        "Seattle",
        "WA",
        "Boston",
        "MA",
        "fedex-2day",
        "Box",
        6.0,
        "Label created",
        "Shipment information sent to local FedEx demo systems.",
        "2026-06-04",
        "2026-06-06 by 8:00 PM",
        "Label created",
        1,
        0,
        "seattle-downtown-wa",
    )
    tracking_id = tracking[0] if tracking else None
    events = (
        query_all(
            after_db,
            """SELECT sequence, event_time, location_label, status_label, details
                 FROM tracking_events
                WHERE tracking_record_id = ?
                ORDER BY sequence""",
            (tracking_id,),
        )
        if tracking_id is not None
        else []
    )
    expected_events = [
        (
            1,
            "2026-06-04 09:00",
            "Seattle, WA",
            "Label created",
            "Shipment information sent to local demo systems.",
        ),
        (
            2,
            "2026-06-04 11:30",
            "Seattle, WA",
            "Picked up",
            "Package picked up in the demo handoff flow.",
        ),
    ]
    only_expected_change, change_evidence = unchanged_except_inserts(
        initial_db,
        after_db,
        {
            "shipments": ("tracking_number", "FDX260000061"),
            "invoices": ("invoice_number", "INV-260061"),
            "tracking_records": ("tracking_number", "FDX260000061"),
            "tracking_events": ("tracking_record_id", tracking_id),
        },
    )
    ok = bool(
        initial_row
        and initial_row[0] == 0
        and row == expected
        and invoice == expected_invoice
        and tracking is not None
        and tracking[1:] == expected_tracking
        and events == expected_events
        and only_expected_change
    )
    return ok, (
        f"initial_count={initial_row[0] if initial_row else None}; shipment={row!r}; "
        f"invoice={invoice!r}; tracking={tracking!r}; events={events!r}; {change_evidence}"
    )


def pickup_state_matches(args: VerifyArgs) -> tuple[bool, str]:
    initial_db = resolve_db(args.initial_db, args.container, "instance_seed")
    after_db = resolve_db(args.after_db, args.container, "instance")
    if not initial_db or not after_db:
        return False, "initial or after-state database unavailable"
    initial_row = query_one(initial_db, "SELECT COUNT(*) FROM pickup_requests WHERE confirmation_code = ?", ("PU-2609",))
    row = query_one(
        after_db,
        """SELECT COUNT(*), u.email, l.slug, p.slot_date, p.time_window,
                  p.package_count, p.status, p.created_on
             FROM pickup_requests p
             JOIN users u ON u.id = p.user_id
             JOIN locations l ON l.id = p.location_id
            WHERE p.confirmation_code = ?""",
        ("PU-2609",),
    )
    expected = (
        1,
        "alice.j@test.com",
        "seattle-downtown-wa",
        "2026-06-05",
        "9:00 AM - 11:00 AM",
        1,
        "Scheduled",
        "2026-06-04",
    )
    only_expected_change, change_evidence = unchanged_except_inserts(
        initial_db,
        after_db,
        {"pickup_requests": ("confirmation_code", "PU-2609")},
    )
    ok = bool(initial_row and initial_row[0] == 0 and row == expected and only_expected_change)
    return ok, (
        f"initial_count={initial_row[0] if initial_row else None}; after={row!r}; "
        f"{change_evidence}"
    )


TASK_SPECS: dict[int, TaskSpec] = {
    0: TaskSpec(("/track/results", "/tracking/FDX260000004"), (("los angeles",), ("weather",))),
    1: TaskSpec(("/track/results", "/tracking/FDX260000001"), (("fdx260000001",), ("delivered",), ("required", "yes"))),
    2: TaskSpec(("/support?q=tracking", "/support/shipment-exception-status"), (("event time", "timestamp", "time"), ("event location", "location"), ("operational delay",))),
    3: TaskSpec(
        ("/rate-estimate",),
        (("fedex ground home delivery",), ("$37.40", "37.4")),
        quote_request=QuoteRequest("CA", "TX", 8, "Box"),
    ),
    4: TaskSpec(
        ("/rate-estimate",),
        (("fedex priority overnight",), ("fedex ground home delivery",), ("$43.80", "43.8")),
        quote_request=QuoteRequest("WA", "FL", 4, "Envelope"),
    ),
    5: TaskSpec(
        ("/login", "/account/shipments", "/invoices"),
        (("inv-260001",),),
        login_email="alice.j@test.com",
    ),
    6: TaskSpec(
        ("/login", "/claims"),
        (("clm-2623",), ("fdx260000023",)),
        login_email="bob.c@test.com",
    ),
    7: TaskSpec(
        ("/login", "/account"),
        (
            ("pu-2621",),
            ("9:00 am", "9 am", "9:00 a.m.", "9 a.m.", "9am"),
            ("11:00 am", "11 am", "11:00 a.m.", "11 a.m.", "11am"),
        ),
        login_email="carol.d@test.com",
    ),
    8: TaskSpec(
        ("/login", "/account/shipments"),
        (
            ("sh-260050",),
            ("charlotte",),
            ("sh-260055",),
            ("los angeles",),
            ("sh-260060",),
            ("seattle",),
        ),
        login_email="david.k@test.com",
    ),
    9: TaskSpec(("/locations", "/locations/dallas-arts-tx"), (("freight cutoff",), ("4:45 pm",))),
    10: TaskSpec(("/locations", "/locations/miami-brickell-fl"), (("international docs",), ("5:45 pm",))),
    11: TaskSpec(
        ("/search?q=delay", "/support/weather-delay-guidance", "/tracking/FDX260000004"),
        (("2026-06-03", "june 3, 2026", "3 june 2026"), ("07:35", "7:35"), ("pending", "no confirmed", "not confirmed")),
    ),
    12: TaskSpec(
        ("/login", "/ship", "/ship/service", "/ship/review", "/ship/confirmation"),
        (("fdx260000061",),),
        shipment_state_matches,
        login_email="alice.j@test.com",
    ),
    13: TaskSpec(
        ("/login", "/pickup", "/account"),
        (("pu-2609",),),
        pickup_state_matches,
        login_email="alice.j@test.com",
    ),
    14: TaskSpec(
        ("/search", "/locations/seattle-downtown-wa"),
        (
            ("7:00 am", "7 am", "7:00 a.m.", "7 a.m.", "7am"),
            ("9:00 pm", "9 pm", "9:00 p.m.", "9 p.m.", "9pm"),
        ),
    ),
    15: TaskSpec(
        ("/login", "/claims"),
        (("clm-2653",), ("fdx260000053",)),
        login_email="david.k@test.com",
    ),
    16: TaskSpec(("/track/results", "/tracking/FDX260000500"), (("delivered",), ("los angeles",), ("ca", "california"))),
    17: TaskSpec(
        ("/rate-estimate",),
        (("fedex freight economy",), ("$191.40", "191.4")),
        quote_request=QuoteRequest("TX", "FL", 12, "Freight pallet"),
    ),
}


def _codes(answer: str, prefix: str) -> set[str]:
    return {match.upper() for match in re.findall(rf"\b{re.escape(prefix)}-?\d+\b", answer, re.IGNORECASE)}


def _claims_relationship(answer: str, subject: str, predicate: str) -> bool:
    text = normalized(answer)
    subject_pattern = re.escape(normalized(subject))
    predicate_pattern = re.escape(normalized(predicate))
    subject_first = rf"{subject_pattern}\s+(?:is|was)\s+(?:the\s+)?{predicate_pattern}\b"
    predicate_first = rf"\b{predicate_pattern}\s+(?:option\s+)?(?:is|:)\s*{subject_pattern}\b"
    return bool(re.search(subject_first, text) or re.search(predicate_first, text))


def _negates(answer: str, phrase: str) -> bool:
    text = normalized(answer)
    phrase_pattern = re.escape(normalized(phrase))
    return bool(
        re.search(rf"\b(?:not|never|no)\b.{{0,24}}{phrase_pattern}", text)
        or re.search(rf"\b(?:isn't|isnt|wasn't|wasnt)\b.{{0,12}}{phrase_pattern}", text)
    )


def _mentions_hour(answer: str, hour: int, meridiem: str) -> bool:
    suffix = meridiem.casefold()
    pattern = rf"(?<!\d)0?{hour}(?::00)?\s*{suffix[0]}\.?\s*{suffix[1]}\.?(?![a-z0-9])"
    return bool(re.search(pattern, normalized(answer)))


def semantic_answer_matches(index: int, answer: str) -> bool:
    """Reject contradictions, reversed relations, and extra identifiers.

    Token groups establish the frozen facts. This layer enforces the relationship
    between those facts so a sentence containing the right words but asserting the
    opposite cannot pass.
    """
    if not answer:
        return False
    if index == 0:
        return answer_contains(answer, "los angeles") and answer_contains(answer, "weather") and not _negates(answer, "weather")
    if index == 1:
        return (
            _codes(answer, "FDX") == {"FDX260000001"}
            and _claims_relationship(answer, "FDX260000001", "delivered")
            and answer_contains(answer, "required")
            and not _negates(answer, "delivered")
            and not _negates(answer, "required")
        )
    if index == 2:
        return (
            any(answer_contains(answer, value) for value in ("event time", "timestamp", "time"))
            and answer_contains(answer, "location")
            and answer_contains(answer, "operational delay")
            and not _negates(answer, "operational delay")
        )
    if index == 3:
        return (
            answer_contains(answer, "FedEx Ground Home Delivery")
            and any(answer_contains(answer, price) for price in ("$37.40", "37.4"))
            and not _negates(answer, "cheapest")
        )
    if index == 4:
        return (
            _claims_relationship(answer, "FedEx Priority Overnight", "fastest")
            and _claims_relationship(answer, "FedEx Ground Home Delivery", "cheapest")
            and any(answer_contains(answer, price) for price in ("$43.80", "43.8"))
            and not _negates(answer, "fastest")
            and not _negates(answer, "cheapest")
        )
    if index == 5:
        return _codes(answer, "INV") == {"INV-260001"} and not _negates(answer, "inv-260001")
    if index == 6:
        return _codes(answer, "CLM") == {"CLM-2623"} and _codes(answer, "FDX") == {"FDX260000023"}
    if index == 7:
        return _codes(answer, "PU") == {"PU-2621"} and _mentions_hour(answer, 9, "am") and _mentions_hour(answer, 11, "am")
    if index == 8:
        expected_codes = {"SH-260050", "SH-260055", "SH-260060"}
        pairings = (("sh-260050", "charlotte"), ("sh-260055", "los angeles"), ("sh-260060", "seattle"))
        text = normalized(answer)
        return _codes(answer, "SH") == expected_codes and all(
            re.search(rf"{re.escape(code)}.{{0,40}}{re.escape(city)}", text) for code, city in pairings
        )
    if index == 9:
        return answer_contains(answer, "freight cutoff") and answer_contains(answer, "4:45 pm")
    if index == 10:
        return answer_contains(answer, "international docs") and answer_contains(answer, "5:45 pm")
    if index == 11:
        text = normalized(answer)
        date_matches = any(answer_contains(answer, date) for date in ("2026-06-03", "june 3, 2026", "3 june 2026"))
        time_matches = bool(re.search(r"\b0?7:35\b", text)) and not re.search(r"\b0?7:35\s*p\.?m", text)
        pending = (
            answer_contains(answer, "pending") and not _negates(answer, "pending")
        ) or bool(re.search(r"\b(?:no confirmed|not confirmed)\b", text))
        contradiction = bool(re.search(r"\b(?:delivery is confirmed|will (?:be )?deliver(?:ed)?)\b", text))
        return date_matches and time_matches and pending and not contradiction
    if index == 12:
        return _codes(answer, "FDX") == {"FDX260000061"} and not _negates(answer, "fdx260000061")
    if index == 13:
        return _codes(answer, "PU") == {"PU-2609"} and not _negates(answer, "pu-2609")
    if index == 14:
        return _mentions_hour(answer, 7, "am") and _mentions_hour(answer, 9, "pm")
    if index == 15:
        return _codes(answer, "CLM") == {"CLM-2653"} and _codes(answer, "FDX") == {"FDX260000053"}
    if index == 16:
        text = normalized(answer)
        delivered_claim = answer_contains(answer, "delivered")
        return (
            delivered_claim
            and answer_contains(answer, "los angeles")
            and (answer_contains(answer, "ca") or answer_contains(answer, "california"))
            and not _negates(answer, "delivered")
            and "still moving" not in text
        )
    if index == 17:
        return (
            answer_contains(answer, "FedEx Freight Economy")
            and any(answer_contains(answer, price) for price in ("$191.40", "191.4"))
            and not _negates(answer, "most expensive")
        )
    return False


def run_task(index: int) -> None:
    args = parse_args()
    task_id = f"FedEx--{index}"
    spec = TASK_SPECS[index]
    trajectory = load_run(args.run_dir)
    answer = final_answer(trajectory)
    judge = Judge(task_id)
    judge.check("task_id_matches", trajectory.get("task_id") == task_id, f"trajectory_task_id={trajectory.get('task_id')!r}")
    for required_path in spec.required_paths:
        judge.check(f"navigated_{required_path}", navigated_to(trajectory, required_path), f"required_path={required_path}")
    judge.check("non_empty_answer", bool(answer), f"final={answer!r}")
    if spec.quote_request:
        matched, evidence = quote_request_matches(trajectory, spec.quote_request)
        judge.check("submitted_requested_quote", matched, evidence)
    if spec.login_email:
        matched, evidence = login_matches(trajectory, spec.login_email)
        judge.check("signed_in_requested_account", matched, evidence)
    for group_number, alternatives in enumerate(spec.answer_groups, start=1):
        matched = any(answer_contains(answer, alternative) for alternative in alternatives)
        judge.check(f"answer_fact_{group_number}", matched, f"accepted_alternatives={alternatives!r}; final={answer!r}")
    judge.check("answer_semantics", semantic_answer_matches(index, answer), f"final={answer!r}")
    if spec.state_check:
        matched, evidence = spec.state_check(args)
        judge.check("after_state_matches", matched, evidence)
    judge.emit()


if __name__ == "__main__":
    raise SystemExit("Run a per-task verify_N.py entry point.")
