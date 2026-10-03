"""条件に合った日本株を探して LINE に送るスクリプト。

使い方:
    python main.py --dry-run               # レポート（docs/）を作り、LINE に送らず要約を画面に表示
    python main.py --dry-run --limit 100   # 先頭100銘柄だけで動作確認
    python main.py --url https://...       # レポートを作り、そのURL付きで LINE に送る
    python main.py                         # レポートを作り、送信内容を line_message.json に保存（GitHub Actions 用）
    python main.py --send line_message.json --url https://...   # 保存した内容を LINE に送る
"""
import argparse
import html
import io
import json
import os
import re
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import jpholiday
import pandas as pd
import requests
import yaml
import yfinance as yf

from report import write_site

JPX_LIST_PAGE = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"
TDNET_LIST_URL = "https://www.release.tdnet.info/inbs/I_list_{page:03d}_{day:%Y%m%d}.html"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
HEADERS = {"User-Agent": "Mozilla/5.0"}
JST = timezone(timedelta(hours=9))
WEEKDAYS = "月火水木金土日"
CHUNK_SIZE = 200          # yfinance に一度に問い合わせる銘柄数
MIN_ROWS = 60             # これより日足が少ない銘柄（上場直後など）は対象外
SUMMARY_MARKET_ITEMS = 4  # LINE の要約に載せるマーケット指標の数（config.yaml の上から）
SUMMARY_FOCUS_ITEMS = 5   # LINE の要約に載せる注目銘柄の数
PENDING_FILE = "line_message.json"  # ページ公開後に送る LINE メッセージの一時保存先

# 東証の制限値幅：(基準値段がこの値未満なら, 値幅)
PRICE_LIMITS = [
    (100, 30), (200, 50), (500, 80), (700, 100), (1_000, 150), (1_500, 300),
    (2_000, 400), (3_000, 500), (5_000, 700), (7_000, 1_000), (10_000, 1_500),
    (15_000, 3_000), (20_000, 4_000), (30_000, 5_000), (50_000, 7_000),
    (70_000, 10_000), (100_000, 15_000), (150_000, 30_000), (200_000, 40_000),
    (300_000, 50_000), (500_000, 70_000), (700_000, 100_000), (1_000_000, 150_000),
    (1_500_000, 300_000), (2_000_000, 400_000), (3_000_000, 500_000), (5_000_000, 700_000),
]

TDNET_ROW_RE = re.compile(
    r'kjTime"[^>]*>([^<]*)<.*?kjCode"[^>]*>([^<]*)<.*?kjName"[^>]*>([^<]*)<'
    r'.*?kjTitle"[^>]*>\s*<a href="([^"]+)"[^>]*>(.*?)</a>',
    re.S,
)


# ---------------------------------------------------------------- データ取得

def fetch_tickers(markets):
    """JPX の上場銘柄一覧から {銘柄コード: 銘柄名} を取得する。"""
    # ファイルの URL や形式（xls/xlsx）が変わることがあるので、一覧ページからリンクを探す
    page = requests.get(JPX_LIST_PAGE, headers=HEADERS, timeout=60)
    page.raise_for_status()
    m = re.search(r'href="([^"]+data_j\.xlsx?)"', page.text)
    if not m:
        raise RuntimeError("JPX の上場銘柄一覧ファイルのリンクが見つかりません")
    res = requests.get(urljoin(JPX_LIST_PAGE, m.group(1)), headers=HEADERS, timeout=60)
    res.raise_for_status()
    df = pd.read_excel(io.BytesIO(res.content), dtype={"コード": str})
    df = df[df["市場・商品区分"].isin(markets)]
    return dict(zip(df["コード"].str.strip(), df["銘柄名"].str.strip()))


def _download_chunk(codes):
    symbols = [f"{c}.T" for c in codes]
    # auto_adjust=False: 配当で過去の株価を調整しない（ストップ高の判定や一般的なチャートと合わせるため）
    data = yf.download(symbols, period="1y", interval="1d", group_by="ticker",
                       auto_adjust=False, threads=True, progress=False)
    result = {}
    if data.empty:
        return result
    available = set(data.columns.get_level_values(0))
    for code, sym in zip(codes, symbols):
        if sym not in available:
            continue
        df = data[sym].dropna(subset=["Close", "Volume"])
        if len(df) >= MIN_ROWS:
            result[code] = df
    return result


def download_prices(codes):
    """過去1年分の日足を取得する。戻り値は {銘柄コード: DataFrame}。"""
    prices = {}
    for i in range(0, len(codes), CHUNK_SIZE):
        chunk = codes[i:i + CHUNK_SIZE]
        got = _download_chunk(chunk)
        # アクセス制限などでほとんど取れなかった場合は、少し待って1回だけやり直す
        if len(got) < len(chunk) * 0.5:
            time.sleep(30)
            got.update(_download_chunk([c for c in chunk if c not in got]))
        prices.update(got)
        print(f"株価取得 {i + len(chunk)}/{len(codes)}（取得済み {len(prices)}）", flush=True)
        time.sleep(1)
    return prices


def fetch_tdnet(day):
    """TDnet の適時開示一覧から、その日の開示を [(時刻, コード, 社名, PDFのURL, 表題), ...] で返す。"""
    rows = []
    for page in range(1, 100):
        url = TDNET_LIST_URL.format(page=page, day=day)
        res = requests.get(url, headers=HEADERS, timeout=30)
        if res.status_code == 404:
            break
        res.raise_for_status()
        res.encoding = "utf-8"
        found = TDNET_ROW_RE.findall(res.text)
        for t, code, name, href, title in found:
            title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
            rows.append((t.strip(), code.strip(), name.strip(), urljoin(url, href), title))
        if not found or f"I_list_{page + 1:03d}_" not in res.text:
            break
        time.sleep(0.5)
    return rows


# ---------------------------------------------------------------- 指標

def rsi(close, period):
    diff = close.diff()
    gain = diff.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-diff.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss)


def atr_pct(df, period):
    """ATR（1日の平均的な値幅）を株価に対する % で返す。"""
    prev = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - prev).abs(), (df["Low"] - prev).abs()],
                   axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean().iloc[-1] / df["Close"].iloc[-1] * 100


def change_pct(close, days=1):
    return (close.iloc[-1] / close.iloc[-1 - days] - 1) * 100


def turnover(df):
    return df["Close"].iloc[-1] * df["Volume"].iloc[-1]


def avg_turnover(df, days=20):
    return (df["Close"] * df["Volume"]).iloc[-days:].mean()


def price_limit(base):
    for upper, width in PRICE_LIMITS:
        if base < upper:
            return width
    return PRICE_LIMITS[-1][1]


def crossed(df, p, upward):
    s = df["Close"].rolling(p["short"]).mean()
    l = df["Close"].rolling(p["long"]).mean()
    if upward:
        return s.iloc[-2] <= l.iloc[-2] and s.iloc[-1] > l.iloc[-1]
    return s.iloc[-2] >= l.iloc[-2] and s.iloc[-1] < l.iloc[-1]


# ---------------------------------------------------------------- 条件
# 各関数は、条件に当てはまれば (補足テキスト, 並び順スコア) を返し、当てはまらなければ None を返す。
# スコアが大きいものほど上に表示する。

def check_stop_high(df, p):
    prev = df["Close"].iloc[-2]
    limit = prev + price_limit(prev)
    if df["Close"].iloc[-1] >= limit - 0.01:
        return ("ストップ高で引け", (2, turnover(df)))
    if p.get("include_touch") and df["High"].iloc[-1] >= limit - 0.01:
        return ("一時ストップ高", (1, turnover(df)))
    return None


def check_stop_low(df, p):
    prev = df["Close"].iloc[-2]
    limit = prev - price_limit(prev)
    if df["Close"].iloc[-1] <= limit + 0.01:
        return ("ストップ安で引け", (2, turnover(df)))
    if p.get("include_touch") and df["Low"].iloc[-1] <= limit + 0.01:
        return ("一時ストップ安", (1, turnover(df)))
    return None


def check_price_jump(df, p):
    chg = change_pct(df["Close"])
    return ("", chg) if chg >= p["threshold"] else None


def check_price_drop(df, p):
    chg = change_pct(df["Close"])
    return ("", -chg) if chg <= p["threshold"] else None


def check_price_jump_nd(df, p):
    chg = change_pct(df["Close"], p["days"])
    return (f"{p['days']}日で{chg:+.1f}%", chg) if chg >= p["threshold"] else None


def check_price_drop_nd(df, p):
    chg = change_pct(df["Close"], p["days"])
    return (f"{p['days']}日で{chg:+.1f}%", -chg) if chg <= p["threshold"] else None


def check_volume_spike(df, p):
    vol = df["Volume"]
    avg = vol.iloc[-p["period"] - 1:-1].mean()
    if avg <= 0:
        return None
    ratio = vol.iloc[-1] / avg
    return (f"出来高 平均の{ratio:.1f}倍", ratio) if ratio >= p["ratio"] else None


def check_daytrade(df, p):
    v = atr_pct(df, p["period"])
    if v < p["atr_pct"]:
        return None
    return (f"値幅 {v:.1f}%・売買代金 {avg_turnover(df) / 1e8:,.0f}億円", v)


def check_perfect_order(df, p):
    if len(df) < p["long"] + 2:
        return None
    s, m, l = (df["Close"].rolling(p[k]).mean() for k in ("short", "mid", "long"))
    ok = (s > m) & (m > l) & (s.diff() > 0) & (m.diff() > 0) & (l.diff() > 0)
    if not ok.iloc[-1]:
        return None
    streak = int(ok[::-1].astype(int).cumprod().sum())  # 何日連続で成立しているか
    if p.get("new_only") and streak > 1:
        return None
    detail = "本日形成" if streak == 1 else f"{streak}日連続"
    return (detail, (-streak, turnover(df)))


def check_golden_cross(df, p):
    return (f"{p['short']}日線が{p['long']}日線を上抜け", turnover(df)) if crossed(df, p, True) else None


def check_dead_cross(df, p):
    return (f"{p['short']}日線が{p['long']}日線を下抜け", turnover(df)) if crossed(df, p, False) else None


def check_rsi_oversold(df, p):
    v = rsi(df["Close"], p["period"]).iloc[-1]
    return (f"RSI {v:.1f}", -v) if v <= p["threshold"] else None


def check_rsi_overbought(df, p):
    v = rsi(df["Close"], p["period"]).iloc[-1]
    return (f"RSI {v:.1f}", v) if v >= p["threshold"] else None


def check_ma_deviation_low(df, p):
    ma = df["Close"].rolling(p["period"]).mean().iloc[-1]
    dev = (df["Close"].iloc[-1] / ma - 1) * 100
    return (f"乖離率 {dev:.1f}%", -dev) if dev <= p["threshold"] else None


def check_new_high_52w(df, p):
    close = df["Close"]
    if len(close) < 200:  # 1年分そろっていない銘柄は判定しない
        return None
    return ("終値ベース", turnover(df)) if close.iloc[-1] > close.iloc[:-1].max() else None


def check_new_low_52w(df, p):
    close = df["Close"]
    if len(close) < 200:
        return None
    return ("終値ベース", turnover(df)) if close.iloc[-1] < close.iloc[:-1].min() else None


CHECKS = {
    "stop_high": check_stop_high,
    "stop_low": check_stop_low,
    "price_jump": check_price_jump,
    "price_drop": check_price_drop,
    "price_jump_nd": check_price_jump_nd,
    "price_drop_nd": check_price_drop_nd,
    "volume_spike": check_volume_spike,
    "daytrade": check_daytrade,
    "perfect_order": check_perfect_order,
    "golden_cross": check_golden_cross,
    "dead_cross": check_dead_cross,
    "rsi_oversold": check_rsi_oversold,
    "rsi_overbought": check_rsi_overbought,
    "ma_deviation_low": check_ma_deviation_low,
    "new_high_52w": check_new_high_52w,
    "new_low_52w": check_new_low_52w,
}


# ---------------------------------------------------------------- スクリーニング

def screen(prices, config):
    """{条件キー: [(スコア, コード, 補足, 終値, 前日比), ...]}、判定日、{コード: 平均売買代金} を返す。"""
    uni = config["universe"]
    conds = {k: v for k, v in config["conditions"].items() if v.get("enabled") and k in CHECKS}
    # Yahoo は銘柄によって更新タイミングがずれるので、最も多くの銘柄でそろっている日付を判定日にする
    latest = pd.Series([df.index[-1] for df in prices.values()]).mode()[0]

    hits = {k: [] for k in conds}
    turnovers = {}
    for code, df in prices.items():
        df = df[df.index <= latest]
        if len(df) < MIN_ROWS or df.index[-1] != latest:  # 売買停止などで判定日のデータがない銘柄は除外
            continue
        close = df["Close"]
        # 値幅制限上ありえない段差は、株式分割・併合が Yahoo のデータに未反映とみなして除外
        ratio = close / close.shift(1)
        if ((ratio < 0.6) | (ratio > 1.8)).any():
            continue
        if close.iloc[-1] < uni["min_price"]:
            continue
        turnovers[code] = avg_turnover(df)
        for key, params in conds.items():
            # 売買代金の下限は条件ごとに上書きできる（ストップ高は小型株も見たい、など）
            if turnovers[code] < params.get("min_turnover", uni["min_turnover"]):
                continue
            r = CHECKS[key](df, params)
            if r:
                detail, score = r
                hits[key].append((score, code, detail, close.iloc[-1], change_pct(close)))

    for key in hits:
        hits[key].sort(key=lambda h: h[0], reverse=True)
    return hits, pd.Timestamp(latest).date(), turnovers


def collect_news(news_cfg, names, turnovers, since_date, today):
    """前営業日の指定時刻以降の適時開示を、キーワードでカテゴリ分けする。"""
    exclude = news_cfg.get("exclude", [])
    categories = news_cfg["categories"]
    grouped = {label: [] for label in categories}
    seen = set()
    day = since_date
    while day <= today:
        for t, code, name, url, title in fetch_tdnet(day):
            if day == since_date and t < news_cfg.get("from_time", "00:00"):
                continue
            code = code[:4]  # TDnet のコードは末尾に 0 が付いた5桁
            if code not in names or (code, title) in seen or any(x in title for x in exclude):
                continue
            for label, keywords in categories.items():
                if any(k in title for k in keywords):
                    seen.add((code, title))
                    grouped[label].append((turnovers.get(code, 0), code, f"{day.month}/{day.day} {t}", title, url))
                    break
        day += timedelta(days=1)
    for items in grouped.values():
        items.sort(key=lambda x: x[0], reverse=True)  # 売買代金の大きい（注目度の高い）銘柄から
    return grouped


def market_overview(symbols):
    """[{label, value, change, sign, date}, ...] を返す。取得できなかった指標は value が None。"""
    data = yf.download([s["symbol"] for s in symbols], period="10d", interval="1d",
                       group_by="ticker", auto_adjust=False, progress=False)
    rows = []
    for s in symbols:
        try:
            c = data[s["symbol"]]["Close"].dropna()
        except KeyError:
            c = pd.Series(dtype=float)
        if len(c) < 2:
            rows.append({"label": s["label"], "value": None})
            continue
        last, prev = c.iloc[-1], c.iloc[-2]
        d = c.index[-1]
        rows.append({
            "label": s["label"],
            "value": f"{last:,.2f}" if last < 1000 else f"{last:,.0f}",
            "change": f"{last - prev:+.2f}" if s.get("diff") else f"{(last / prev - 1) * 100:+.2f}%",
            "sign": int(last > prev) - int(last < prev),
            "date": f"{d.month}/{d.day}",
        })
    return rows


def find_focus(hits, news, config, turnovers):
    """複数の条件（ニュースを含む）に当てはまった銘柄を、当てはまった数の多い順に返す。"""
    tags = {}
    for key, items in hits.items():
        for _, code, *_ in items:
            tags.setdefault(code, []).append(config["conditions"][key]["label"])
    for label, items in (news or {}).items():
        for _, code, *_ in items:
            tag = f"ニュース：{label}"
            if tag not in tags.setdefault(code, []):
                tags[code].append(tag)
    focus = [(code, t) for code, t in tags.items() if len(t) >= 2]
    focus.sort(key=lambda x: (len(x[1]), turnovers.get(x[0], 0)), reverse=True)
    return focus


# ---------------------------------------------------------------- メッセージ

def build_summary(r):
    """LINE に送る短い要約。末尾の {url} はページ公開後に差し替える。"""
    d = r["data_date"]
    lines = [f"📈 {d.month}/{d.day}（{WEEKDAYS[d.weekday()]}）の株スクリーニング"]
    if r["stale"]:
        lines.append("⚠️ 前営業日の終値が未反映（その前日のデータで判定）")

    market = [m for m in (r.get("market") or []) if m["value"] is not None][:SUMMARY_MARKET_ITEMS]
    if market:
        lines.append("")
        lines += [f"{m['label']} {m['value']}（{m['change']}）" for m in market]

    conds = r["config"]["conditions"]
    lines.append("")
    lines.append(" / ".join(f"{conds[k]['label']} {len(v)}" for k, v in r["hits"].items()))
    if r.get("news"):
        lines.append(f"📰 ニュース {sum(len(v) for v in r['news'].values())}件")

    if r["focus"]:
        lines.append("")
        lines.append("🔥 注目（複数の条件に該当）")
        for code, tags in r["focus"][:SUMMARY_FOCUS_ITEMS]:
            lines.append(f"・{code} {r['names'].get(code, '')}（{len(tags)}条件）")

    lines += ["", "▼ 詳しくはこちら", "{url}"]
    return "\n".join(lines)


def line_credentials():
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    user_id = os.environ.get("LINE_USER_ID", "").strip()
    missing = [k for k, v in (("LINE_CHANNEL_ACCESS_TOKEN", token), ("LINE_USER_ID", user_id)) if not v]
    if missing:
        raise RuntimeError(f"{' と '.join(missing)} が設定されていません。"
                           "GitHub の Settings → Secrets and variables → Actions の Repository secrets を確認してください")
    return token, user_id


def send_line(texts):
    token, user_id = line_credentials()
    res = requests.post(
        LINE_PUSH_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"to": user_id, "messages": [{"type": "text", "text": t} for t in texts]},
        timeout=30,
    )
    if res.status_code != 200:
        raise RuntimeError(f"LINE 送信失敗: {res.status_code} {res.text}")


def is_market_closed(d):
    """土日・祝日・年末年始（12/31〜1/3）は東証の休場日。"""
    return d.weekday() >= 5 or jpholiday.is_holiday(d) or (d.month, d.day) in {(12, 31), (1, 1), (1, 2), (1, 3)}


def previous_weekday(today):
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


# ---------------------------------------------------------------- メイン

def run(args):
    with open(args.config, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if not args.dry_run:
        line_credentials()  # 時間のかかる株価取得の前に、LINE の設定漏れを検出する

    # 前営業日が祝日などで休場だった場合は、前回と同じ結果になるので送らない
    today = datetime.now(JST).date()
    expected_date = previous_weekday(today)
    Path(PENDING_FILE).unlink(missing_ok=True)
    if not args.dry_run and is_market_closed(expected_date):
        print(f"{expected_date} は休場日のため、スキップします")
        return

    names = fetch_tickers(config["universe"]["markets"])
    codes = sorted(names)
    if args.limit:
        codes = codes[:args.limit]
    print(f"対象銘柄 {len(codes)}件", flush=True)

    prices = download_prices(codes)
    if not prices:
        raise RuntimeError("株価データを1件も取得できませんでした")

    hits, data_date, turnovers = screen(prices, config)
    r = {
        "config": config,
        "names": names,
        "hits": hits,
        "report_date": expected_date,
        "data_date": data_date,
        "checked": len(turnovers),
        # Yahoo のデータ更新が遅れていると前営業日の終値がまだ入っていないことがある
        "stale": data_date < expected_date and not is_market_closed(expected_date),
        "generated_at": datetime.now(JST),
    }

    # 概況とニュースは取得に失敗しても（None にして）、銘柄の結果だけは届ける
    market_cfg = config.get("market", {})
    if market_cfg.get("enabled"):
        try:
            r["market"] = market_overview(market_cfg["symbols"])
        except Exception:
            traceback.print_exc()
            r["market"] = None
    news_cfg = config.get("news", {})
    if news_cfg.get("enabled"):
        try:
            r["news"] = collect_news(news_cfg, names, turnovers, expected_date, today)
        except Exception:
            traceback.print_exc()
            r["news"] = None

    r["focus"] = find_focus(hits, r.get("news"), config, turnovers)
    r["quotes"] = {code: (price, chg) for items in hits.values() for _, code, _, price, chg in items}

    page_path = write_site(args.out, r)
    summary = build_summary(r)
    print(f"レポートを作成しました: {Path(args.out) / page_path}")

    if args.dry_run:
        print("\n" + summary.replace("{url}", str((Path(args.out) / page_path).resolve())))
    elif args.url:
        send_summary(summary, page_path, args.url)
    else:
        # GitHub Actions では、ページを公開してから --send で送る
        Path(PENDING_FILE).write_text(json.dumps({"text": summary, "path": page_path}, ensure_ascii=False),
                                      encoding="utf-8")
        print(f"{PENDING_FILE} に送信内容を保存しました")


def send_summary(summary, page_path, base_url):
    url = base_url.rstrip("/") + "/" + page_path
    send_line([summary.replace("{url}", url)])
    print(f"LINE に送信しました: {url}")


def main():
    parser = argparse.ArgumentParser(description="条件に合った日本株を LINE に通知する")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="LINE に送らず、レポートを作って要約を画面に表示する")
    parser.add_argument("--limit", type=int, help="先頭 N 銘柄だけで試す（動作確認用）")
    parser.add_argument("--out", default="docs", help="レポートの出力先フォルダ")
    parser.add_argument("--url", help="公開ページの URL。指定するとすぐ LINE に送る")
    parser.add_argument("--send", metavar="FILE", help="保存しておいた送信内容を --url のページへのリンク付きで送る")
    args = parser.parse_args()
    try:
        if args.send:
            if not args.url:
                parser.error("--send には --url が必要です")
            pending = json.loads(Path(args.send).read_text(encoding="utf-8"))
            send_summary(pending["text"], pending["path"], args.url)
        else:
            run(args)
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
