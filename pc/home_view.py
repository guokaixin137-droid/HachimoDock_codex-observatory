"""Project the home screen into the eight scalars supported by the P4 host."""
from datetime import timedelta
import math
import unicodedata
from heatmap import CHARS
from quota_layout import MINI_ALIASES, MINI_ENCODE, pad_pixels

# The controller selects image columns by taking a scalar modulo 3, 7, 8 or 5.
# Two bands select the first chat's badge row. Each contains three weather rows.
TILE_COLUMNS = (3, 7, 8, 5)
BASE = math.prod(TILE_COLUMNS)
WEATHER_GROUPS = 3
HOME_RANGE = (BASE, BASE * (1 + 2 * WEATHER_GROUPS) - 1)
TILE_WEIGHTS = tuple((BASE // n) * pow(BASE // n, -1, n) for n in TILE_COLUMNS)
WEATHER_ICONS = ("unknown","clear","partly","cloud","overcast","fog","drizzle","rain",
                 "freezing","snow","shower","storm","hail")
WEATHER_CODES = {code: kind for kind, codes in (
    ("clear",(0,)), ("partly",(1,)), ("cloud",(2,)), ("overcast",(3,)), ("fog",(45,48)),
    ("drizzle",(51,53,55)), ("rain",(61,63,65)), ("freezing",(56,57,66,67)),
    ("snow",(71,73,75,77,85,86)), ("shower",(80,81,82)), ("storm",(95,)), ("hail",(96,99)),
) for code in codes}
STATES = {"done": 1, "waiting": 2, "error": 3, "working": 4, "stopped": 5}
LABELS = ("", "已完成", "等你答复", "执行出错", "思考中", "已停止")
WEEKDAYS = "一二三四五六日"
DATE_CHARS = "0123456789."
TEMP_CHARS = "0123456789-°C—"
SMALL_EXTRA = ".KMBTE—"

def symbols(start, count):
    return "".join(chr(start + n) for n in range(count))

DATE = dict(zip(DATE_CHARS, symbols(0xe000, len(DATE_CHARS))))
TEMP = dict(zip(TEMP_CHARS, symbols(0xe010, len(TEMP_CHARS))))
NO_DAY = chr(0xe0b0)
SMALL = {**MINI_ENCODE, **dict(zip(SMALL_EXTRA, symbols(0xe030, len(SMALL_EXTRA))))}
BARS = [symbols(0xe040, 18), symbols(0xe060, 18)]  # 0..16, then unknown
DAYS = symbols(0xe080, 32)  # day 1..31, followed by today
SPACER = chr(0xe0a0)  # transparent 32/em advance
RESERVED = CHARS + MINI_ALIASES
LETTERS = "".join(c for c in RESERVED if c.isalpha())
LITERAL = dict(zip(LETTERS, symbols(0xe100, len(LETTERS))))
LITERAL.update({c:chr(ord(c)+0xfee0) for c in RESERVED if c not in LETTERS})

def display_text(raw, byte_limit=63):
    # The legacy heatmap owns some printable ASCII font slots. Use alternate
    # glyphs for those letters in real titles, including mixed Chinese/Latin.
    clean = " ".join(str(raw or "").split())
    clean = "".join(c for c in clean if unicodedata.category(c)[0] != "C")
    chars = [LITERAL.get(c, c) for c in clean]
    if len("".join(chars).encode()) <= byte_limit:
        return "".join(chars)
    value = ""
    for ch in chars:
        if len((value + ch + "…").encode()) > byte_limit:
            break
        value += ch
    return value + "…"

def compact(n):
    if type(n) not in (int, float) or n < 0 or (type(n) is float and not math.isfinite(n)):
        return "—"
    if n >= 10**15:
        mantissa, exponent = f"{n:.2E}".split("E")
        return mantissa.rstrip("0").rstrip(".") + "E" + str(int(exponent))
    for scale, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= scale:
            result = f"{n / scale:.2f}".rstrip("0").rstrip(".")
            return result + unit
    return str(int(n))

def status_index(rows, weather_code=None):
    kinds = [STATES.get(row.get("state"), 0) for row in rows[:3]]
    a, b, c = (kinds + [0, 0, 0])[:3]
    icon = WEATHER_ICONS.index(WEATHER_CODES.get(weather_code, "unknown")) if type(weather_code) is int else 0
    band = a // 3 * WEATHER_GROUPS + icon // 5
    residues = (a % 3, b, c, icon % 5)
    return BASE * (1 + band) + sum(v * w for v, w in zip(residues, TILE_WEIGHTS)) % BASE


def top_line(local=None, temperature=None):
    if local is None:
        return "--:--"
    stamp = "".join(DATE[c] for c in local.strftime("%Y.%m.%d"))
    temp = str(round(temperature)) + "°C" if type(temperature) in (int,float) and -100 <= temperature <= 70 else "—"
    return local.strftime("%H:%M") + MINI_ENCODE[" "] + stamp + SPACER * 3 + "".join(TEMP[c] for c in temp)


def calendar_weather(local, weather):
    weekday = "星期" + WEEKDAYS[local.weekday()] if local else "　　　"
    prefix = weekday + " " * 8
    # Give the condition and the fallback-city marker priority when a city is long.
    label = weather.get("label") or "天气暂不可用"
    city, separator, condition = label.partition(" · ")
    def width(value):
        return sum(20 if unicodedata.east_asian_width(c) in ("W","F","A") else 12 for c in value)
    budget = 63 - len(prefix.encode())
    if separator and (width(label) > 230 or display_text(label,budget).endswith("…")):
        suffix = "…" + separator + condition
        while city and (width(city + suffix) > 230 or len(display_text(city+suffix,512).encode()) > budget):
            city = city[:-1]
        label = city + suffix
    return prefix + display_text(label, budget)


def usage_line(today):
    total, inp, out = (compact(today.get(k)) for k in ("total", "input", "output"))
    # The 270px node at 40px has 216 design units. Start a new line after
    # the large total, then align the smaller output with its static label.
    total = total if total != "—" else "-"
    def small(value):
        return "".join(SMALL.get(c,c) for c in value)
    return (total + pad_pixels(216 - len(total)*24) + small(inp)
            + pad_pixels(108 - len(inp)*12) + small(out))

def chart_line(local_date, days):
    if local_date is None:
        return ""
    dates = [local_date - timedelta(days=i) for i in range(6,-1,-1)]
    values = [(days.get(str(day)) or {}).get("total") for day in dates]
    maximum = max((n for n in values if type(n) is int and n > 0), default=1)
    line = ""
    for i, (day, n) in enumerate(zip(dates, values)):
        level = 17 if type(n) is not int or n < 0 else 0 if n == 0 else max(1, round(16*n/maximum))
        line += BARS[int(i==6)][level] + DAYS[31 if i==6 else day.day-1]
    return line

def home_projection(snapshot, local=None):
    weather = snapshot.get("weather") or {}
    chats = snapshot.get("recentChats")
    rows = (chats or [])[:3]
    stats = snapshot.get("statistics") or {}
    today = stats.get("today", {}) if snapshot.get("available") else {}
    days = stats.get("days", {}) if snapshot.get("available") else {}
    titles = [display_text(("状态未知 · " if row.get("state") not in STATES else "") +
              (row.get("title") or "未命名对话")) for row in rows]
    if not titles:
        titles = ["等待对话数据" if chats is None else "暂无对话"]
    titles += [""] * (3 - len(titles))
    chart = chart_line(local.date() if local else None, days)
    if chart:
        chart = chart.translate(str.maketrans(BARS[1], BARS[0]))
    else:
        chart = "".join(BARS[0][17] + (DAYS[31] if i == 6 else NO_DAY) for i in range(7))
    return {"mode": status_index(rows, weather.get("code")),
            "v1":top_line(local, weather.get("temperature")), "v2":calendar_weather(local, weather),
            "v3":usage_line(today), "v4":titles[0], "v5":titles[1], "v6":titles[2], "v7":chart}
