"""Seven positive activity bands for the current P4, with a 53-byte row budget."""
import math
import unicodedata
from bisect import bisect_left
from string import ascii_uppercase
from datetime import date, timedelta, timezone
from heatmap import COLUMNS, activity_start, month_labels
from home_view import BASE, LITERAL, SPACER, STATES
from quota_layout import MINI_ENCODE
from quota_layout import period_code, remaining, reset_date

# Leave frequently displayed numbers, spacing and punctuation in the default face.
# Nine states per day fit in 81 printable two-day symbols.
PAIRS = ''.join(chr(i) for i in range(33, 127) if chr(i) not in '0123456789.:%')
assert len(PAIRS) == 81
MISSING = 8
PERCENTILES = (.22, .44, .63, .78, .90)
# Alpha values are distinct endpoints in the device palette's 4-bit coverage.
ALPHAS = (17, 40, 60, 81, 107, 141, 188, 255)
BLUE = "0085ff"
TINT = "d9f3ff"
ALIASES = {ch: chr(0x100 + i) for i, ch in enumerate(PAIRS)}

# These legacy codes were originally rendered by the onboard full-width face.
SYSTEM_LETTERS = "AFGHJLQRVYZX"
ALIASES[SPACER] = "\u01ff"
for char in "{}|~X":
    ALIASES[char] = chr(ord(char) + 0xfee0)
for char in SYSTEM_LETTERS:
    if char in LITERAL:
        ALIASES[LITERAL[char]] = chr(ord(char) + 0xfee0)

def safe_text(s):
    return ''.join(ALIASES.get(char, char) for char in s)

def display_value(s, limit=63):
    encoded = safe_text(s)
    if len(encoded.encode()) <= limit:
        return encoded
    return encoded.encode()[:limit-3].decode('utf8', 'ignore') + '…'

# Alternate two-byte glyph codes leave room for the second line of Latin titles.
# Older installed fonts still use LITERAL in their own screen projections.
LOWER_GLYPHS = {c: chr(0x200 + i) for i, c in enumerate(LITERAL) if c.islower()}
LEGACY_TITLE_TABLE = {ord(c): LOWER_GLYPHS.get(c, safe_text(LITERAL.get(c, c))) for c in map(chr, range(32, 127))}
SHORT_GLYPHS = str.maketrans({LITERAL[c]: alt for c, alt in LOWER_GLYPHS.items()})
# These letters still use the wide face for token units or quota periods.
# All other capitals can keep the wire codes already sent by older collectors.
METRIC_CAPITALS = "BDEKMT"
TITLE_GLYPHS = {**LOWER_GLYPHS, **{
    c: chr(0x240 + i) if c in METRIC_CAPITALS else LEGACY_TITLE_TABLE[ord(c)]
    for i, c in enumerate(ascii_uppercase)
}}
TITLE_TABLE = {**LEGACY_TITLE_TABLE, **{ord(c): alias for c, alias in TITLE_GLYPHS.items()}}
DETAILED_HOME_RANGE = (BASE, 13 * BASE - 1)
THINKING_RANGE = (7 * BASE, DETAILED_HOME_RANGE[1])


def title_text(raw, table=TITLE_TABLE):
    clean = ' '.join(str(raw).split())
    clean = ''.join(c for c in clean if not unicodedata.category(c).startswith('C'))
    data = clean.translate(table).encode()
    return data.decode() if len(data) <= 63 else data[:60].decode('utf-8', 'ignore') + '…'


def detailed_home(home, snapshot):
    result = {k: v.translate(SHORT_GLYPHS) if isinstance(v, str) else v for k, v in home.items()}
    for slot, row in enumerate((snapshot.get('recentChats') or [])[:3], 4):
        title = ('状态未知 · ' if row.get('state') not in STATES else '') + (row.get('title') or '未命名对话')
        key = 'v' + str(slot)
        # Older packages read v4–v6. Only send an override when a wide metric
        # letter needs its own title glyph in the new package.
        result[key] = title_text(title, LEGACY_TITLE_TABLE)
        aligned = title_text(title)
        if aligned != result[key]:
            result['t' + str(slot)] = aligned
    group, weather = divmod(home['mode'] // BASE - 1, 3)
    band = 3 * group + (2 - weather if group else weather)
    if any(row.get('state') == 'working' for row in (snapshot.get('recentChats') or [])[:3]):
        band = 11 - band
    result['mode'] = (band + 1) * BASE + home['mode'] % BASE
    return result


def activity_levels(days, today):
    first = activity_start(today) if today else None
    entries = []
    for i in range(COLUMNS * 7):
        day = first + timedelta(days=i) if first else None
        record = days.get(str(day)) if day and day <= today else None
        n = record.get('total') if isinstance(record, dict) else record
        entries.append(n if type(n) is int and n >= 0 else None)
    positive = sorted({n for n in entries if n is not None and n > 0})
    if len(positive) <= 7:
        bands = {n: (1 + round(i*6/(len(positive)-1)) if len(positive)>1 else 1)
                 for i,n in enumerate(positive)}
    else:
        # Reserve the lightest blue for the peak. Distribute distinct lower
        # totals across six bands, even when many days have identical usage.
        lower = positive[:-1]
        cuts = []
        previous = -1
        for i,q in enumerate(PERCENTILES):
            idx = min(len(lower) - (6-i), max(previous+1, math.ceil(len(lower)*q)-1))
            cuts.append(lower[idx])
            previous = idx
        bands = {n:1+bisect_left(cuts,n) for n in lower}
        bands[positive[-1]] = 7
    def level(n):
        return MISSING if n is None else bands.get(n, 0)
    return [level(n) for n in entries]


def activity_rows(days, today):
    values = activity_levels(days, today)
    return [''.join(PAIRS[9 * values[7*c+2*pair] + (values[7*c+2*pair+1] if pair<3 else MISSING)]
                    for c in range(COLUMNS)) for pair in range(4)]

def activity_calendar(history, now):
    today = now.astimezone(timezone.utc).date() if now else None
    if today is None or not history.get('available'):
        return today, {}
    if 'utcTotals' not in history:
        return today, history.get('days') or {}
    totals = dict(history['utcTotals'])
    # Earlier local logs may have been removed, or activity may be on another
    # machine. An empty day is certain only inside the observed history.
    if totals and history.get('completeIndex',True):
        first = max(date.fromisoformat(min(totals)), activity_start(today))
        last = min(date.fromisoformat(history['utcThrough']), today)
        for offset in range((last-first).days+1):
            totals.setdefault(str(first+timedelta(days=offset)),0)
    return today, totals

def community_pages(pages, statistics, local, windows=()):
    home = {k:display_value(v) if isinstance(v,str) else v for k,v in pages['home'].items()}
    home['v1'] = display_value(pages['home']['v1'].replace(SPACER*3, SPACER*2 + MINI_ENCODE[' ']))
    home['v2'] = display_value(pages['home']['v2'],61)
    quota = {k:display_value(v) if k in ('v1','v2','v3') else v for k,v in pages['quota'].items()}
    history = statistics.get('history') or {}
    today, days = activity_calendar(history, local)
    rows = activity_rows(days, today)
    quota['v1'] = month_labels(today, first=activity_start(today), cell_width=11.5, glyph_width=10.5, width_chars=58) if today else ''
    if 0 <= quota['mode'] <= 100 and windows:
        quota['v2'] = display_value(reset_date(windows[0]) + ' ' + (windows[0].get('label') or ''))
    elif quota['mode'] >= 101 and len(windows) >= 2:
        def card(window):
            text = remaining(window) + '  ' + reset_date(window)
            if quota['mode'] >= 104:
                text += ' ' + period_code(window).strip()
            return text
        quota['v2'] = display_value(card(windows[0]).ljust(23) + card(windows[1]))
    quota.update({f'v{i+4}':row for i,row in enumerate(rows)})
    return {'home':home,'quota':quota}


def device_pages(community, windows=()):
    """Project the two 3.1.4 screens while keeping older device packets usable."""
    quota = dict(community['quota'])
    # Nine 24px advances at the 32px face match the native 216px column pitch.
    fields = quota['v3'].split()
    quota['v3'] = ''.join(field.ljust(9) for field in fields).rstrip()
    if 0 <= quota['mode'] <= 100:
        weekly = len(windows) == 1 and windows[0].get('windowMinutes') == 10080
        heading = '周额度剩余' + safe_text(SPACER) if weekly else '上次记录剩余'
        # Both headings occupy six ems. The two native text nodes crop opposite
        # ends of this one field, leaving all four activity strings available.
        detail = heading + '   ' + quota['v2']
        quota['v2'] = detail if len(detail.encode()) <= 63 else detail.encode()[:60].decode('utf-8','ignore') + '…'
    return {'home': community['home'], 'quota': quota}
