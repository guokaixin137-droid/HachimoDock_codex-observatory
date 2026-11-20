"""Two pages of Codex data in the P4's eight display variables."""
from heatmap import empty_rows, encode, rolling_grid_levels
from home_view import compact, home_projection
from quota_layout import dual_mode, ordered_windows, pad_pixels, period_code, remaining, reset_date


def dashboard_pages(snapshot=None, local=None):
    snapshot = snapshot or {}
    statistics = snapshot.get("statistics") or {}
    week = (statistics.get("week") or {}) if snapshot.get("available") else {}
    history = statistics.get("history") or {}
    rows = (encode(rolling_grid_levels(history.get("days", {}), local.date()))
            if local and history.get("available") else empty_rows())
    quota = dict(mode=-1, v1="02 / 02", v2="账户额度 · 尚无记录",
                 v3="".join(compact(week.get(key)).ljust(11) for key in ("input","output","total")).rstrip(),
                 **{f"v{i+4}": row for i, row in enumerate(rows)})
    windows = ordered_windows(snapshot.get("quota") or {})[:2]
    if len(windows) == 1:
        window = windows[0]
        value = window.get("remainingPercent")
        usable = window.get("status") == "observed" and type(value) in (int,float) and 0 <= value <= 100
        quota["mode"] = round(value) if usable else -5
        if usable:
            quota["v2"] = reset_date(window)
        elif window.get("status") == "awaiting_reset_refresh":
            quota["v2"] = "已到重置时间 · 等待新额度记录"
        else:
            quota["v2"] = "额度暂不可用 · 等待新记录"
        if window.get("label"):
            quota["v1"] += "  " + window["label"]
    elif len(windows) == 2:
        quota["mode"] = dual_mode(windows, False)
        left, right = windows
        first = remaining(left)
        quota["v1"] = first + pad_pixels(312 - len(first)*24) + remaining(right)
        def reset(window):
            text = reset_date(window)
            return text + " " + period_code(window).strip() if quota["mode"] >= 104 else text
        quota["v2"] = reset(left).ljust(28) + reset(right)
    return {"home": home_projection(snapshot, local), "quota": quota}
