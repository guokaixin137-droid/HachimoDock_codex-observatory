"""Bounded daily activity glyphs: two vertical cells per ASCII character.

These are fixed font symbols, not executable device code or streamed images.
Four strings carry 53 x 7 observed daily levels within the legacy eight vars.
"""
from datetime import date, timedelta
import math

CHARS = "abcdefghijklmnopqrstuvwxyz!\"#$&'()*+,;<=>?"
assert len(CHARS) == 42 and len(set(CHARS)) == 42
LEVELS = 6  # zero, four nonzero levels, outside the observed date window
FUTURE = 5
COLUMNS = 53


def rolling_start(today):
    """Monday 52 weeks ago; the final column is always the current week."""
    return today - timedelta(days=today.weekday() + 7 * (COLUMNS - 1))

def activity_start(today):
    """The Codex profile's daily grid starts each UTC week on Sunday."""
    return today - timedelta(days=(today.weekday()+1)%7 + 7*(COLUMNS-1))

def month_ticks(today, first=None):
    """Month starts on the weekly axis shared by the device and desktop."""
    first = first or rolling_start(today)
    day = first
    while day <= today:
        if day.day == 1:
            yield (day - first).days // 7, day.strftime('%m')
        day += timedelta(days=1)


def month_labels(today, *, cell_width=11, glyph_width=9.75, width_chars=59, first=None):
    """Keep both digits inside the chart, including a month starting this week."""
    labels = [' '] * width_chars
    for column, month in month_ticks(today, first):
        pos = min(width_chars - 2, round(column * cell_width / glyph_width))
        labels[pos:pos + 2] = month
    return ''.join(labels).rstrip()


def rolling_grid_levels(days, today):
    return _levels(days, rolling_start(today), today)


def grid_levels(days, today):
    """Retain the 1.1.0/1.1.1 calendar-year projection during upgrades."""
    first = date(today.year, 1, 1)
    # 53 columns cover every day of a year. Columns are consecutive seven-day
    # groups starting Jan 1; no weekday labels imply a Monday/Sunday alignment.
    return _levels(days, first, today)


def _levels(days, first, today):
    values = [max(0, d.get("total", 0)) for key, d in days.items()
              if str(first) <= key <= str(today)]
    positive = sorted(v for v in values if v > 0)
    thresholds = [positive[max(0, math.ceil(len(positive)*q)-1)]
                  for q in (.25, .5, .75)] if positive else [0, 0, 0]
    cells = []
    for i in range(COLUMNS * 7):
        day = first + timedelta(days=i)
        item = days.get(str(day))
        if day > today or item is None:
            cells.append(FUTURE)
        else:
            n = max(0, item.get("total", 0))
            cells.append(0 if n == 0 else 1 + sum(n > t for t in thresholds))
    return cells


def encode(cells):
    assert len(cells) == COLUMNS * 7
    rows = []
    for pair in range(4):
        chars = []
        for col in range(COLUMNS):
            top = cells[col * 7 + pair * 2]
            index = 36 + top if pair == 3 else top * LEVELS + cells[col * 7 + pair * 2 + 1]
            chars.append(CHARS[index])
        rows.append("".join(chars))
    return rows


def empty_rows():
    return encode([FUTURE] * (COLUMNS * 7))
