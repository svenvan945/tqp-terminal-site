#!/usr/bin/env python3
"""
Static generator for terminal.thequantphilosopher.com — Hyperliquid funding
terminal (design 2, light data-terminal). Regenerated hourly by GitHub Actions
and deployed to GitHub Pages. Isolated from the bots.

Output tree in <site_repo_dir>:
  index.html            page (template + inlined lw.js + rows/categories)
  data/hist/<coin>.json 92-day hourly funding per coin (loaded on demand)
  data/caps.json        market caps cache (stocks: Yahoo, crypto: CoinGecko)

Usage: publish_terminal.py <site_repo_dir> [--render-only] [--skip-caps]
       publish_terminal.py <site_repo_dir> --render-only --repair-history <coin>
Needs beside this script: terminal_template.html, lw.js, asset_map.json.
"""
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.hyperliquid.xyz/info"
HIST_DAYS = 92
S = requests.Session()
S.headers["User-Agent"] = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/126 Safari/537.36")
CATEGORY_ORDER = [
    "Crypto — Majors", "Crypto — L1/L2", "Crypto — DeFi", "Crypto — Memes",
    "Crypto — AI/Agents", "Crypto — Other",
    "Stocks — US Tech/Semis", "Stocks — US Other", "Stocks — Korea",
    "Stocks — Japan", "Stocks — China/Taiwan/Other Asia",
    "Stocks — Private/Pre-IPO", "ETFs & Indices", "Commodities", "FX",
    "Prediction/Other"]


def post(body, tries=5):
    last = None
    for k in range(tries):
        try:
            r = S.post(API, json=body, timeout=25)
            if r.status_code in (429, 500, 502, 503):
                time.sleep(2 + 2 * k)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 + 2 * k)
    raise RuntimeError(f"api failed: {last}")


def load_map():
    """asset_map.json (base) + asset_map_new.json (later dex listings)."""
    out = {}
    for fn in ("asset_map.json", "asset_map_new.json"):
        try:
            out.update(json.load(open(os.path.join(HERE, fn),
                                      encoding="utf-8")))
        except OSError:
            pass
    return out


def list_dexes():
    """MAIN + every builder dex that currently has live assets."""
    try:
        r = post({"type": "perpDexs"}) or []
    except Exception:  # noqa: BLE001
        return [None, "xyz"]
    out = [None]
    for d in r:
        if d and d.get("name"):
            out.append(d["name"])
    return out


def snapshot_rows(amap):
    rows = []
    for dex in list_dexes():
        body = {"type": "metaAndAssetCtxs"}
        if dex:
            body["dex"] = dex
        try:
            meta, ctxs = post(body)
        except Exception as e:  # noqa: BLE001
            print(f"dex {dex} failed: {e}")
            continue
        for i, u in enumerate(meta["universe"]):
            if u.get("isDelisted"):
                continue
            c = ctxs[i]
            try:
                fr = float(c.get("funding", 0) or 0)
                mk = float(c.get("markPx", 0) or 0)
            except (TypeError, ValueError):
                continue
            m = amap.get(u["name"], {})
            rows.append({
                "coin": u["name"], "mark": mk,
                "fh": round(fr * 100, 6),
                "apr": round(fr * 24 * 365 * 100, 2),
                "prem": round(float(c.get("premium", 0) or 0) * 100, 4),
                "oi": round(float(c.get("openInterest", 0) or 0) * mk),
                "vol": round(float(c.get("dayNtlVlm", 0) or 0)),
                "dex": dex or "main",
                "cat": m.get("category") or ("Crypto — Other" if not dex
                                             else "Prediction/Other"),
                "name": m.get("name") or u["name"],
                "kind": m.get("kind") or ("crypto" if not dex else "other"),
            })
    rows.sort(key=lambda r: -abs(r["apr"]))
    return rows


def history(coin, days=HIST_DAYS):
    now = int(time.time() * 1000)
    out = {}
    cur = now - days * 86400000
    for _ in range(12):
        ch = post({"type": "fundingHistory", "coin": coin,
                   "startTime": cur, "endTime": now}) or []
        if not ch:
            break
        for x in ch:
            out[int(x["time"]) // 1000] = float(x["fundingRate"])
        mx = max(int(x["time"]) for x in ch)
        if len(ch) < 500 or mx + 1 <= cur:
            break
        cur = mx + 1
        time.sleep(0.12)
    return sorted(out.items())


def averages(hist, now_s):
    """APR (%) averaged over trailing windows; None if <50% coverage."""
    out = {}
    for key, days in (("a1d", 1), ("a3d", 3), ("a7d", 7), ("a2w", 14),
                      ("a1m", 30), ("a3m", 90)):
        cut = now_s - days * 86400
        vals = [r for t, r in hist if t >= cut]
        need = days * 24 * 0.5
        out[key] = (round(sum(vals) / len(vals) * 24 * 365 * 100, 2)
                    if len(vals) >= need else None)
    return out


# ------------------------------ market caps ------------------------------ #
def _yahoo_crumb():
    """Yahoo v7/v10 need a session cookie + crumb (verified 2026-08-18)."""
    try:
        S.get("https://fc.yahoo.com/", timeout=15)          # sets A3 cookie
        r = S.get("https://query2.finance.yahoo.com/v1/test/getcrumb",
                  timeout=15)
        return r.text.strip() if r.status_code == 200 and r.text else None
    except Exception:  # noqa: BLE001
        return None


FX_FALLBACK = {"USD": 1.0}


def _fx_to_usd(currencies, crumb):
    """currency -> USD multiplier via Yahoo FX quotes (KRW=X etc)."""
    out = {"USD": 1.0}
    need = [c for c in currencies if c and c != "USD"]
    if not need:
        return out
    syms = ",".join(f"{c}=X" for c in need)          # e.g. KRW=X = USD/KRW
    try:
        r = S.get("https://query2.finance.yahoo.com/v7/finance/quote",
                  params={"symbols": syms, "crumb": crumb}, timeout=25)
        for q in (r.json().get("quoteResponse") or {}).get("result", []):
            px = q.get("regularMarketPrice")
            cur = q["symbol"].replace("=X", "")
            if px:
                out[cur] = 1.0 / float(px)
    except Exception:  # noqa: BLE001
        pass
    for c in need:                                     # GBp pence quirk etc.
        out.setdefault(c, None)
    return out


def caps_yahoo(symbols):
    """symbol -> market cap in USD (company cap; ETFs report netAssets)."""
    crumb = _yahoo_crumb()
    if not crumb:
        print("yahoo: no crumb — skipping stock caps this run")
        return {}
    raw = {}
    for i in range(0, len(symbols), 60):
        chunk = symbols[i:i + 60]
        for k in range(3):
            try:
                r = S.get("https://query2.finance.yahoo.com/v7/finance/quote",
                          params={"symbols": ",".join(chunk), "crumb": crumb,
                                  "fields": "marketCap,netAssets,currency,"
                                            "quoteType"}, timeout=25)
                if r.status_code == 429:
                    time.sleep(6 + 4 * k)
                    continue
                r.raise_for_status()
                for q in (r.json().get("quoteResponse") or {}).get("result", []):
                    mc = q.get("marketCap") or q.get("netAssets")
                    if mc:
                        raw[q["symbol"]] = (float(mc), q.get("currency") or "USD")
                break
            except Exception:  # noqa: BLE001
                time.sleep(3)
        time.sleep(0.8)
    fx = _fx_to_usd({cur for _, cur in raw.values()}, crumb)
    out = {}
    for sym, (mc, cur) in raw.items():
        m = fx.get(cur)
        if cur == "GBp":                                # pence
            m = (fx.get("GBP") or 0) / 100.0 if fx.get("GBP") else None
        if m:
            out[sym] = mc * m
    return out


def caps_coingecko(ids):
    out = {}
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        for k in range(4):
            try:
                r = S.get("https://api.coingecko.com/api/v3/coins/markets",
                          params={"vs_currency": "usd", "ids": ",".join(chunk),
                                  "per_page": 250, "page": 1}, timeout=25)
                if r.status_code == 429:
                    time.sleep(20 + 15 * k)
                    continue
                r.raise_for_status()
                for c in r.json():
                    mc = c.get("market_cap") or c.get("fully_diluted_valuation")
                    if mc:
                        out[c["id"]] = float(mc)
                break
            except Exception:  # noqa: BLE001
                time.sleep(5)
        time.sleep(2)
    return out


def market_caps(rows, amap, repo, skip=False):
    path = os.path.join(repo, "data", "caps.json")
    old = {}
    try:
        old = json.load(open(path, encoding="utf-8"))
    except OSError:
        pass
    if skip:
        return old
    y_syms = sorted({amap[r["coin"]]["yahoo"] for r in rows
                     if amap.get(r["coin"], {}).get("yahoo")})
    cg_ids = sorted({amap[r["coin"]]["coingecko"] for r in rows
                     if amap.get(r["coin"], {}).get("coingecko")})
    y = caps_yahoo(y_syms) if y_syms else {}
    g = caps_coingecko(cg_ids) if cg_ids else {}
    caps = dict(old)                    # keep last-known when a source fails
    for r in rows:
        m = amap.get(r["coin"], {})
        v = None
        if m.get("yahoo") and m["yahoo"] in y:
            v = y[m["yahoo"]]
        elif m.get("coingecko") and m["coingecko"] in g:
            v = g[m["coingecko"]]
        if v:
            caps[r["coin"]] = round(v)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(caps, open(path, "w", encoding="utf-8"), separators=(",", ":"))
    print(f"caps: yahoo {len(y)}/{len(y_syms)}, coingecko {len(g)}/{len(cg_ids)}"
          f" -> {sum(1 for r in rows if r['coin'] in caps)}/{len(rows)} rows")
    return caps


# ---------------------------------- main ---------------------------------- #
def repair_history(repo, coin):
    """Repair one existing row without refreshing the snapshot or other files."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_-]*", coin):
        raise ValueError("unsafe history coin")
    repo = Path(repo)
    index = repo / "index.html"
    html = index.read_bytes().decode("utf-8")
    assignments = list(re.finditer(r"window\.__TERMINAL_DATA__\s*=\s*", html))
    if len(assignments) != 1:
        raise ValueError("expected exactly one embedded terminal snapshot")
    start = assignments[0].end()

    def reject_constant(value):
        raise ValueError(f"nonfinite snapshot value: {value}")

    data, length = json.JSONDecoder(parse_constant=reject_constant).raw_decode(
        html[start:])
    end = start + length
    if not re.match(r"\s*;\s*</script>", html[end:]):
        raise ValueError("malformed embedded snapshot assignment")
    if (not isinstance(data, dict) or type(data.get("ts")) is not int
            or data["ts"] <= 0 or not isinstance(data.get("rows"), list)
            or not data["rows"]):
        raise ValueError("malformed terminal snapshot")
    coins = []
    for row in data["rows"]:
        if (not isinstance(row, dict) or not isinstance(row.get("coin"), str)
                or not row["coin"]):
            raise ValueError("malformed snapshot row")
        coins.append(row["coin"])
    if len(set(coins)) != len(coins) or coin not in coins:
        raise ValueError("repair requires one exact existing snapshot coin")
    row = data["rows"][coins.index(coin)]
    h = history(coin)
    if not isinstance(h, (list, tuple)) or len(h) < 24:
        raise ValueError("repair history requires at least 24 points")
    previous = 0
    for point in h:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("malformed history point")
        timestamp, rate = point
        if (type(timestamp) is not int or timestamp <= previous
                or type(rate) not in (int, float) or not math.isfinite(rate)):
            raise ValueError("history must be finite and strictly time ordered")
        previous = timestamp
    if h[-1][0] < data["ts"] // 3600 * 3600:
        raise ValueError("history does not reach the snapshot hour")
    row.update(averages(h, data["ts"]))
    row["hist"] = 1
    history_bytes = json.dumps(
        [[t, round(rate, 10)] for t, rate in h],
        separators=(",", ":"), allow_nan=False).encode("utf-8")
    payload = json.dumps(data, separators=(",", ":"), allow_nan=False)
    index_bytes = (html[:start] + payload + html[end:]).encode("utf-8")

    # Validate and serialize both outputs before replacing either published file.
    hdir = repo / "data" / "hist"
    hdir.mkdir(parents=True, exist_ok=True)
    target = hdir / (coin.replace(":", "_") + ".json")
    old_history = target.read_bytes() if target.exists() else None
    staged = []
    try:
        for content in (history_bytes, index_bytes):
            fd, name = tempfile.mkstemp(prefix=".history-repair-", dir=repo)
            staged.append(Path(name))
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
        os.replace(staged[0], target)
        try:
            os.replace(staged[1], index)
        except OSError:
            if old_history is None:
                target.unlink()
            else:
                staged[0].write_bytes(old_history)
                os.replace(staged[0], target)
            raise
    finally:
        for path in staged:
            path.unlink(missing_ok=True)
    print(f"repaired {coin}: {len(h)} points; snapshot {data['ts']} preserved")
    return 0


def store_row_history(row, hist, hdir, now_s):
    if len(hist) >= 24:
        filename = row["coin"].replace(":", "_") + ".json"
        with open(os.path.join(hdir, filename), "w", encoding="utf-8") as stream:
            json.dump([[t, round(rate, 10)] for t, rate in hist], stream,
                      separators=(",", ":"))
        row.update(averages(hist, now_s))
        row["hist"] = 1
        return 1
    row.update({key: None for key in ("a1d", "a3d", "a7d", "a2w", "a1m", "a3m")})
    row["hist"] = 0
    return 0


def main():
    repo = sys.argv[1]
    render_only = "--render-only" in sys.argv
    skip_caps = "--skip-caps" in sys.argv
    if "--repair-history" in sys.argv:
        at = sys.argv.index("--repair-history")
        if (not render_only or at + 1 >= len(sys.argv)
                or sys.argv.count("--repair-history") != 1):
            print("repair requires --render-only and exactly one coin")
            return 1
        try:
            return repair_history(repo, sys.argv[at + 1])
        except (ValueError, OSError, RuntimeError) as exc:
            print(f"history repair refused: {exc}")
            return 1
    amap = load_map()
    rows = snapshot_rows(amap)
    if len(rows) < 100:
        print("insufficient rows — refusing to publish")
        return 1
    now_s = int(time.time())
    hdir = os.path.join(repo, "data", "hist")
    os.makedirs(hdir, exist_ok=True)
    n_hist = 0
    failed = []
    for r in rows:
        try:
            h = history(r["coin"])
        except Exception as e:  # noqa: BLE001
            print("hist fail", r["coin"], e)
            failed.append(r)
            h = []
        n_hist += store_row_history(r, h, hdir, now_s)
        time.sleep(0.12)
    # One deferred retry lets a transient API outage recover after other coins.
    # Legitimately short histories are not failures and are never retried here.
    for r in failed:
        try:
            h = history(r["coin"])
        except Exception as e:  # noqa: BLE001
            print("hist retry fail", r["coin"], e)
            continue
        n_hist += store_row_history(r, h, hdir, now_s)
        time.sleep(0.12)
    caps = market_caps(rows, amap, repo, skip=skip_caps)
    for r in rows:
        r["mcap"] = caps.get(r["coin"])
    cats = [c for c in CATEGORY_ORDER if any(r["cat"] == c for r in rows)]
    data = {"ts": now_s, "rows": rows, "categories": cats,
            "hist_days": HIST_DAYS}
    tpl = Path(HERE, "terminal_template.html").read_text(encoding="utf-8")
    lw = Path(HERE, "lw.js").read_text(encoding="utf-8")
    html = (tpl.replace("__LW_JS__", lw)
               .replace("__DATA__", json.dumps(data, separators=(",", ":"))))
    with open(os.path.join(repo, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    stamp = datetime.fromtimestamp(now_s, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"rendered {len(rows)} rows, {n_hist} histories, {len(cats)} "
          f"categories, {len(html)//1024}KB @ {stamp}")
    if render_only:
        return 0

    def git(*a):
        return subprocess.run(["git", "-C", repo] + list(a),
                              capture_output=True, text=True, timeout=300)
    git("add", "-A")
    c = git("commit", "-m", f"update {stamp}")
    if "nothing to commit" in (c.stdout + c.stderr):
        print("no changes")
        return 0
    p = git("push")
    print("push:", "ok" if p.returncode == 0 else p.stderr[:300])
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
