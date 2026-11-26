"""Dates, working calendar and timezone helpers. Timestamps are aware UTC datetimes."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]


@lru_cache(maxsize=16)
def tz(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def parse_date(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not DATE_RE.match(value):
        raise ValueError("Некорректная календарная дата")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("Некорректная календарная дата") from None


def check_month(month: str) -> str:
    if not isinstance(month, str) or not MONTH_RE.match(month):
        raise ValueError("Месяц должен быть в формате ГГГГ-ММ")
    return month


def shift_month(month: str, delta: int) -> str:
    check_month(month)
    idx = int(month[:4]) * 12 + int(month[5:]) - 1 + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def first_date(month: str) -> date:
    check_month(month)
    return date(int(month[:4]), int(month[5:]), 1)


def last_date(month: str) -> date:
    return first_date(shift_month(month, 1)) - timedelta(days=1)


def nominal_date(month: str, day: int) -> date:
    """Day N of the month, clamped to its last day (30th of February -> 28/29th)."""
    last = last_date(month)
    return date(last.year, last.month, min(day, last.day))


def month_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def calendar_months_before(d: date, months: int) -> date:
    return nominal_date(shift_month(month_of(d), -months), d.day)


def parse_ts(value) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Некорректная отметка времени")
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def try_parse_ts(value) -> datetime | None:
    try:
        return parse_ts(value)
    except (TypeError, ValueError):
        return None


def iso(dt: datetime) -> str:
    """JS-compatible ISO string: 2026-11-17T20:59:59.999Z"""
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def at_local(d: date, hhmm: str, tzname: str) -> datetime:
    if not TIME_RE.match(hhmm):
        raise ValueError("Время должно быть в формате ЧЧ:ММ")
    h, m = map(int, hhmm.split(":"))
    return datetime.combine(d, time(h, m), tzinfo=tz(tzname)).astimezone(timezone.utc)


def end_of_local_date(d: date, tzname: str) -> datetime:
    return datetime.combine(d, time(23, 59, 59, 999000), tzinfo=tz(tzname)).astimezone(timezone.utc)


def local_date(dt: datetime, tzname: str) -> date:
    return dt.astimezone(tz(tzname)).date()


def local_time(dt: datetime, tzname: str) -> str:
    return dt.astimezone(tz(tzname)).strftime("%H:%M")


class WorkCalendar:
    """Mon–Fri by default; holidays and extra working days are configured manually."""

    def __init__(self, holidays=(), working_dates=()):
        self.holidays = {parse_date(x) for x in holidays}
        self.working = {parse_date(x) for x in working_dates}

    def is_working(self, d: date) -> bool:
        if d in self.working:
            return True
        if d in self.holidays:
            return False
        return d.weekday() < 5

    def previous_working(self, d: date) -> date:
        for _ in range(370):
            if self.is_working(d):
                return d
            d -= timedelta(days=1)
        raise ValueError("В календаре не найден рабочий день")

    def add_working_days(self, d: date, count: int) -> date:
        remaining = count
        for _ in range(740):
            if remaining <= 0:
                break
            d += timedelta(days=1)
            if self.is_working(d):
                remaining -= 1
        if remaining > 0:
            raise ValueError("В календаре недостаточно рабочих дней")
        return d

    def nth_working(self, month: str, n: int) -> date:
        d, end, count = first_date(month), last_date(month), 0
        while d <= end:
            if self.is_working(d):
                count += 1
                if count == n:
                    return d
            d += timedelta(days=1)
        raise ValueError(f"В месяце меньше {n} рабочих дней")
