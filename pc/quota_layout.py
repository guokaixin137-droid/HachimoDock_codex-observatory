"""Bounded quota text: large percentages and small dates in eight host bindings."""
from datetime import datetime
from zoneinfo import ZoneInfo
from heatmap import CHARS

PIXEL_CHARS = '0123456789BCDEIKMNOPSTUWX.%/-: '
MINI_TEXT = '0123456789/: -HDM?'
MINI_ALIASES = ''.join(chr(i) for i in range(33, 127) if chr(i) not in PIXEL_CHARS + CHARS)[:len(MINI_TEXT)]
MINI_ENCODE = dict(zip(MINI_TEXT, MINI_ALIASES))
MINI_DECODE = dict(zip(MINI_ALIASES, MINI_TEXT))
TZ = ZoneInfo('Asia/Shanghai')


def mini(text):
    return ''.join(MINI_ENCODE[c] for c in text)


def pad_pixels(pixels):
    assert pixels >= 0 and pixels % 12 == 0
    return ' ' * (pixels // 24) + mini(' ') * ((pixels % 24) // 12)


def remaining(window):
    value = window.get('remainingPercent')
    return str(round(value)) + '%' if window.get('status') == 'observed' and type(value) in (int,float) and 0 <= value <= 100 else '--'


def reset_date(window):
    try:
        value = datetime.fromisoformat(window['resetsAt'].replace('Z','+00:00'))
        return value.astimezone(TZ).strftime('%m/%d %H:%M') if value.tzinfo else '--/-- --:--'
    except (KeyError, ValueError, TypeError, AttributeError):
        return '--/-- --:--'


def period_code(window):
    minutes = window.get('windowMinutes')
    if type(minutes) is not int or minutes <= 0:
        return '????'
    code = str(minutes // 1440)+'D' if minutes % 1440 == 0 else str(minutes // 60)+'H' if minutes % 60 == 0 else str(minutes)+'M'
    return code.ljust(4) if len(code) <= 4 else '????'


def dual_payload(windows, generic=False):
    left, right = windows
    a, b = remaining(left), remaining(right)
    # 540px exactly: the following glyph wraps onto a second line. Miniature
    # atlas glyphs preserve 16px dates at the same 32px node size. No controls.
    top = a + pad_pixels(300 - len(a)*24) + b + pad_pixels(240 - len(b)*24)
    first = (period_code(left) if generic else ' '*4) + reset_date(left)
    second = (period_code(right) if generic else ' '*4) + reset_date(right)
    dates = mini(first.ljust(25) + second)
    result = top + dates
    assert len(result.encode()) == 63
    return result


def ordered_windows(quota):
    windows = [w for w in quota.get('windows', []) if isinstance(w, dict)]
    return sorted(windows, key=lambda w:w['windowMinutes'] if type(w.get('windowMinutes')) is int and w['windowMinutes'] > 0 else 10**9)


def dual_mode(windows, stale):
    known = [w.get('windowMinutes') for w in windows] == [300,10080]
    status = 2 if any(w.get('status') != 'observed' for w in windows) else 1 if stale else 0
    return 101 + status + (0 if known else 3)
