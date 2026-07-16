#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台灣電力情報站 · 全能源別即時抓取器  ·  taipower_power_scraper.py
================================================================
用途：每 15 分鐘抓台電「各機組發電量」即時資料(genary)，解析【全部】
      能源別(核能/燃煤/燃氣/燃油/水力/風力/太陽能/抽蓄/儲能/汽電共生/
      其他再生等)，輸出前端可讀的 JSON；同時抓電力供需報表(備轉容量率)。

血統：由 windfarmTaiwan(風電風情) 的 taipower_wind_scraper.py 改造而來，
      沿用該專案已在 GitHub Actions 實測過的解析、防呆與誠實原則：
      - 欄位寬容比對，辨識不了就印診斷、不寫猜測值
      - 分組對照表比對不到的機組類型歸入 "other" 並在 log 印出原始名稱供校準
      - 電力供需主要來源被 WAF 擋(403)時退到政府開放資料每日備援，
        並以 granularity 欄位誠實標示 intraday/daily

輸出(寫入 docs/，與前端同資料夾，GitHub Pages 同網域服務)：
  docs/power_realtime.json  即時各能源別彙總 + 原始類型對照(供校準與透明)
  docs/power_history.json   滾動 7 天歷史(每筆=一次快照的各能源別 MW)
  docs/grid_status.json     電力供需(尖峰負載/供電能力/備轉容量率)
================================================================
依賴：requests
"""

import csv
import io
import json
import os
import re
import sys
import datetime as dt
from pathlib import Path

import requests

GENARY_URL = "https://service.taipower.com.tw/data/opendata/apply/file/d006001/001.json"
GENARY_URL_ALT = "https://www.taipower.com.tw/d006/loadGraph/loadGraph/data/genary.json"

DOCS = Path(__file__).with_name("docs")
OUTPUT = DOCS / "power_realtime.json"
HISTORY = DOCS / "power_history.json"
GRID_OUTPUT = DOCS / "grid_status.json"
HISTORY_DAYS = 7
MAX_POINTS = 1200

GRID_URL = "https://www.taipower.com.tw/d006/loadGraph/loadGraph/data/sys_dem_sup.csv"
GRID_FALLBACK_META_APIS = [
    "https://data.gov.tw/api/v2/rest/dataset/19995",
    "https://data.nat.gov.tw/api/v2/rest/dataset/19995",
]

TZ = dt.timezone(dt.timedelta(hours=8))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TaiwanPowerWatch/1.0; +https://github.com/dofliu/taiwanPowerWatch)",
    "Accept": "application/json,text/plain,*/*",
}
GRID_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/csv,application/json,text/plain,*/*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.taipower.com.tw/d006/loadGraph/loadGraph/load_briefing3.html",
}

# 機組類型 → 能源別分組。genary 的類型字串含英文括註(如 "核能(Nuclear)")，
# 依「關鍵字包含」比對(先英後中，順序即優先序：Pumped 要先於 Hydro，
# IPP-Coal 含 Coal 所以同組)。比對不到 → "other" 並印診斷供校準。
GROUP_RULES = [
    ("pumped",  ("Pumped", "抽蓄")),
    ("storage", ("Storage", "儲能")),
    ("nuclear", ("Nuclear", "核能")),
    ("coal",    ("Coal", "燃煤")),
    ("lng",     ("LNG", "燃氣")),
    ("oil",     ("Oil", "Diesel", "燃料油", "燃油", "輕油", "重油")),
    ("cogen",   ("Co-Gen", "CoGen", "汽電共生")),
    ("wind",    ("Wind", "風力")),
    ("solar",   ("Solar", "太陽能", "太陽光電")),
    ("hydro",   ("Hydro", "水力")),
    ("other_re", ("Other Renewable", "Geothermal", "其它再生", "其他再生", "地熱", "生質")),
]
GROUP_LABELS = {  # 前端顯示名(中/英)；順序=預設顯示順序(概略依規模)
    "lng": ("燃氣", "LNG"), "coal": ("燃煤", "Coal"), "nuclear": ("核能", "Nuclear"),
    "cogen": ("汽電共生", "Co-gen"), "wind": ("風力", "Wind"), "solar": ("太陽能", "Solar"),
    "hydro": ("水力", "Hydro"), "pumped": ("抽蓄", "Pumped"), "oil": ("燃油", "Oil"),
    "storage": ("儲能", "Storage"), "other_re": ("其他再生", "Other RE"), "other": ("其他", "Other"),
}


def strip_html(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


def to_float(s):
    if s is None:
        return None
    t = str(s).replace(",", "").strip()
    t = re.sub(r"\(.*?\)", "", t).strip()
    if t in ("", "N/A", "-", "--", "NA"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def clean_name(s):
    return re.sub(r"\(註\d+\)", "", strip_html(s or "")).strip()


def classify(etype):
    for group, keys in GROUP_RULES:
        for k in keys:
            if k.lower() in etype.lower():
                return group
    return "other"


def fetch_genary():
    last_err = None
    for url in (GENARY_URL, GENARY_URL_ALT):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return json.loads(r.text.lstrip("﻿"))
        except Exception as e:
            print(f"[WARN] 取得失敗 {url}：{e}", file=sys.stderr)
            last_err = e
    raise RuntimeError(f"genary 兩個端點皆失敗：{last_err}")


def parse_all(data):
    """解析全部機組列 → (groups, raw_types, system_total)。
    groups: {group: {"mw","cap_mw","units"}}；抽蓄/儲能之「負載」列為負值照實計入。
    raw_types: {原始類型字串: {"mw","group","units"}}，供前端透明揭露與人工校準。"""
    rows = data.get("aaData") or data.get("data") or []
    groups, raw_types = {}, {}
    total = 0.0
    for row in rows:
        if isinstance(row, dict):
            etype = strip_html(str(row.get("機組類型", "")))
            raw = str(row.get("機組名稱", ""))
            cap = to_float(row.get("裝置容量(MW)"))
            out = to_float(row.get("淨發電量(MW)"))
        elif isinstance(row, (list, tuple)) and len(row) >= 4:
            etype, raw = strip_html(str(row[0])), str(row[1])
            cap, out = to_float(row[2]), to_float(row[3])
        else:
            continue
        name = clean_name(raw)
        if any(x in name for x in ("小計", "合計", "總計")) or out is None:
            continue
        g = classify(etype)
        if g == "other" and etype:
            print(f"[CAL] 未知機組類型歸入 other：{etype!r}(機組 {name!r})", file=sys.stderr)
        gg = groups.setdefault(g, {"mw": 0.0, "cap_mw": 0.0, "units": 0})
        gg["mw"] += out
        gg["cap_mw"] += cap or 0.0
        gg["units"] += 1
        rt = raw_types.setdefault(etype or "(空白)", {"mw": 0.0, "group": g, "units": 0})
        rt["mw"] += out
        rt["units"] += 1
        total += out
    for g in groups.values():
        g["mw"] = round(g["mw"], 1)
        g["cap_mw"] = round(g["cap_mw"], 1)
    for t in raw_types.values():
        t["mw"] = round(t["mw"], 1)
    return groups, raw_types, round(total, 1)


def update_history(groups, total, t, updated):
    points = []
    if HISTORY.exists():
        try:
            points = json.loads(HISTORY.read_text(encoding="utf-8")).get("points", [])
        except Exception:
            points = []
    rec = {"t": t, "g": {k: v["mw"] for k, v in groups.items()}, "total": total}
    if points and points[-1].get("t") == t:
        points[-1] = rec
    else:
        points.append(rec)
    try:
        newest = dt.datetime.fromisoformat(max(p.get("t") or "" for p in points))
        cutoff = (newest - dt.timedelta(days=HISTORY_DAYS)).isoformat(timespec="seconds")
        points = [p for p in points if (p.get("t") or "") >= cutoff]
    except ValueError:
        pass
    points = points[-MAX_POINTS:]
    HISTORY.write_text(json.dumps(
        {"updated": updated, "points": points}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    return len(points)


# ===== 電力供需(備轉容量率)：整段沿用 windfarmTaiwan 已實測的作法 =====
GRID_TIME_KEYS = ("時間", "日期時間", "日期", "Date", "DateTime", "datetime", "Time")
GRID_LOAD_KEYS = ("尖峰負載(MW)", "瞬時尖峰負載(MW)", "尖峰負載(萬瓩)", "瞬時尖峰負載(萬瓩)")
GRID_SUPPLY_KEYS = ("淨尖峰供電能力(MW)", "系統運轉淨尖峰供電能力(MW)",
                     "淨尖峰供電能力(萬瓩)", "系統運轉淨尖峰供電能力(萬瓩)")
GRID_RESERVE_KEYS = ("備轉容量(MW)", "瞬時備轉容量(MW)", "備轉容量(萬瓩)", "瞬時備轉容量(萬瓩)")
GRID_PCT_KEYS = ("備轉容量率(%)", "瞬時備轉容量率(%)", "備轉容量率")
GRID_STATUS_KEYS = ("燈號", "供電燈號", "備轉容量率燈號", "狀態", "供電狀況")


def _grid_fmt_date(s):
    s = (s or "").strip()
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _grid_find_key(row, exact_keys, hint=None):
    for k in exact_keys:
        if k in row:
            return k
    if hint:
        for k in row:
            if hint in k and ("率" in hint) == ("率" in k):
                return k
    return None


def _grid_num_mw(row, key):
    if key is None:
        return None
    v = to_float(row.get(key))
    if v is None:
        return None
    if "萬瓩" in key:
        return round(v * 10, 1)
    if "MW" in key.upper():
        return round(v, 1)
    if 1000 <= v <= 8000:
        return round(v * 10, 1)
    if 8000 < v <= 60000:
        return round(v, 1)
    return None


def parse_grid_row(row, granularity="intraday"):
    time_key = _grid_find_key(row, GRID_TIME_KEYS)
    load_key = _grid_find_key(row, GRID_LOAD_KEYS, "尖峰負載")
    supply_key = _grid_find_key(row, GRID_SUPPLY_KEYS, "供電能力")
    reserve_key = _grid_find_key(row, GRID_RESERVE_KEYS, "備轉容量")
    pct_key = _grid_find_key(row, GRID_PCT_KEYS, "備轉容量率")
    status_key = _grid_find_key(row, GRID_STATUS_KEYS)
    peak_load = _grid_num_mw(row, load_key)
    supply_cap = _grid_num_mw(row, supply_key)
    reserve_mw = _grid_num_mw(row, reserve_key)
    reserve_pct = to_float(row.get(pct_key)) if pct_key else None
    if reserve_pct is None and peak_load and supply_cap and peak_load > 0:
        reserve_pct = round((supply_cap - peak_load) / peak_load * 100, 2)
    if peak_load is None and supply_cap is None and reserve_mw is None and reserve_pct is None:
        return None
    return {
        "granularity": granularity,
        "source_time": _grid_fmt_date(strip_html(str(row.get(time_key, "")))) if time_key else None,
        "peak_load_mw": peak_load, "supply_capacity_mw": supply_cap,
        "reserve_mw": reserve_mw, "reserve_pct": reserve_pct,
        "status_text": strip_html(str(row.get(status_key, ""))).strip() or None if status_key else None,
    }


def _grid_rows_from_text(text):
    rows = list(csv.DictReader(io.StringIO(text)))
    if rows:
        return rows
    try:
        data = json.loads(text)
        rows = data if isinstance(data, list) else (data.get("data") or data.get("records") or [])
        return [r for r in rows if isinstance(r, dict)]
    except json.JSONDecodeError:
        return []


def fetch_grid_status():
    try:
        r = requests.get(GRID_URL, headers=GRID_HEADERS, timeout=20)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        rows = _grid_rows_from_text(r.text.lstrip("﻿"))
        if rows:
            parsed = parse_grid_row(rows[-1], "intraday")
            if parsed:
                print(f"[OK] 電力供需(即時)：{parsed}", file=sys.stderr)
                return parsed
            print(f"[WARN] 電力供需(即時)欄位無法辨識：{sorted(rows[-1].keys())}", file=sys.stderr)
    except Exception as e:
        print(f"[WARN] 電力供需(即時)不可用：{e}", file=sys.stderr)

    urls = []
    for api in GRID_FALLBACK_META_APIS:
        try:
            r = requests.get(api, headers=HEADERS, timeout=25)
            r.raise_for_status()
            for d in (r.json().get("result") or {}).get("distribution") or []:
                u = d.get("resourceDownloadUrl") or d.get("resourceAccessUrl")
                if u:
                    urls.append(u)
            if urls:
                break
        except Exception as e:
            print(f"[WARN] 電力供需(備援)metadata 失敗 {api}：{e}", file=sys.stderr)
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            rows = _grid_rows_from_text(r.text.lstrip("﻿"))
            if not rows:
                continue
            time_key = _grid_find_key(rows[0], GRID_TIME_KEYS)
            best, best_d = rows[-1], ""
            if time_key:
                for row in rows:
                    d = _grid_fmt_date(strip_html(str(row.get(time_key, ""))))
                    if d and d > best_d:
                        best_d, best = d, row
            parsed = parse_grid_row(best, "daily")
            if parsed:
                print(f"[OK] 電力供需(每日備援)：{parsed}", file=sys.stderr)
                return parsed
        except Exception as e:
            print(f"[WARN] 電力供需(備援)不可用 {url}：{e}", file=sys.stderr)
    return None


def main():
    DOCS.mkdir(exist_ok=True)
    data = fetch_genary()
    upd = None
    for k in ("DateTime", "", "recordtime", "updateTime", "datetime"):
        if isinstance(data, dict) and data.get(k):
            upd = strip_html(str(data[k]))
            break
    groups, raw_types, total = parse_all(data)
    now = dt.datetime.now(TZ).isoformat(timespec="seconds")

    try:
        grid = fetch_grid_status()
        if grid:
            GRID_OUTPUT.write_text(json.dumps(
                {"updated": now, **grid}, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        print(f"[WARN] 電力供需處理失敗（不影響主流程）：{e}", file=sys.stderr)

    OUTPUT.write_text(json.dumps({
        "updated": now, "source_time": upd,
        "system_total_mw": total,
        "groups": groups,
        "labels": {k: {"zh": v[0], "en": v[1]} for k, v in GROUP_LABELS.items()},
        "raw_types": raw_types,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    n = update_history(groups, total, upd or now, now)
    print(f"[OK] {now} 全系統 {total} MW，{len(groups)} 個能源別，"
          f"{len(raw_types)} 種原始類型 · 歷史 {n} 筆")


if __name__ == "__main__":
    main()
