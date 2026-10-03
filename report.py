"""スクリーニング結果を、スマホで見やすい HTML ページにする。"""
import html
from datetime import date
from pathlib import Path

WEEKDAYS = "月火水木金土日"

CSS = """
:root {
  --bg: #f4f5f7; --card: #ffffff; --text: #1d2129; --muted: #6b7280; --border: #e3e6ea;
  --chip: #eef0f3; --link: #0b63ce; --up: #d1242f; --down: #0b63ce;
  --warn-bg: #fff4e0; --warn: #8a5300; --accent: #06c755;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1115; --card: #181b21; --text: #e6e8eb; --muted: #9aa1ab; --border: #2a2f38;
    --chip: #242a33; --link: #6cb0ff; --up: #ff6b61; --down: #6cb0ff;
    --warn-bg: #3a2a10; --warn: #ffc56b;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text); line-height: 1.5;
  font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", "Yu Gothic UI", Meiryo, sans-serif;
  -webkit-text-size-adjust: 100%;
}
a { color: var(--link); }
.wrap { max-width: 760px; margin: 0 auto; padding: 0 16px 48px; }
header { padding: 20px 0 12px; }
h1 { font-size: 20px; margin: 0; display: flex; align-items: center; gap: 8px; }
h1::before { content: ""; width: 6px; height: 20px; border-radius: 3px; background: var(--accent); }
.sub { color: var(--muted); font-size: 13px; margin-top: 4px; }
.warn { background: var(--warn-bg); color: var(--warn); padding: 10px 12px; border-radius: 10px; font-size: 13px; margin-top: 12px; }
nav {
  position: sticky; top: 0; z-index: 10; background: var(--bg);
  display: flex; gap: 6px; overflow-x: auto; padding: 10px 16px; margin: 0 -16px;
  border-bottom: 1px solid var(--border); scrollbar-width: none;
}
nav::-webkit-scrollbar { display: none; }
.chip {
  flex: none; padding: 5px 12px; border-radius: 999px; background: var(--card);
  border: 1px solid var(--border); font-size: 13px; color: var(--text); text-decoration: none; white-space: nowrap;
}
.chip b { margin-left: 4px; font-variant-numeric: tabular-nums; }
.chip.zero { opacity: .45; }
.chip.hot { border-color: var(--accent); }
section { margin-top: 24px; scroll-margin-top: 64px; }
h2 { font-size: 16px; margin: 0 0 8px; display: flex; align-items: baseline; gap: 8px; }
h2 .count { font-size: 13px; color: var(--muted); font-weight: normal; }
.note { font-size: 12px; color: var(--muted); margin: -4px 0 8px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
.market { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; }
.tile { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 8px 12px; }
.tile .label { font-size: 12px; color: var(--muted); display: flex; justify-content: space-between; gap: 4px; }
.tile .value { font-size: 17px; font-weight: 600; font-variant-numeric: tabular-nums; }
.tile .chg { font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }
.up { color: var(--up); } .down { color: var(--down); } .flat { color: var(--muted); }
ul.list { list-style: none; margin: 0; padding: 0; }
ul.list li {
  padding: 10px 14px; border-top: 1px solid var(--border);
  display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 2px 12px; align-items: center;
}
ul.list li:first-child { border-top: 0; }
.name { font-weight: 600; color: var(--text); text-decoration: none; overflow-wrap: anywhere; }
.code { font-size: 12px; color: var(--muted); font-weight: normal; margin-right: 6px; font-variant-numeric: tabular-nums; }
.price { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; font-size: 14px; }
.price .chg { display: inline-block; min-width: 58px; font-weight: 600; }
.time { font-size: 12px; color: var(--muted); white-space: nowrap; }
.detail { grid-column: 1 / -1; font-size: 12px; color: var(--muted); }
a.detail { color: var(--link); font-size: 13px; }
.tags { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 4px; margin-top: 2px; }
.tag { font-size: 11px; padding: 1px 8px; border-radius: 999px; background: var(--chip); }
.empty { padding: 12px 14px; margin: 0; color: var(--muted); font-size: 13px; }
details > summary {
  padding: 10px 14px; border-top: 1px solid var(--border); color: var(--link);
  cursor: pointer; font-size: 13px; list-style: none; text-align: center;
}
details > summary::-webkit-details-marker { display: none; }
details[open] > summary { display: none; }
details > ul.list li:first-child { border-top: 1px solid var(--border); }
footer { margin-top: 32px; font-size: 12px; color: var(--muted); }
footer p { margin: 4px 0; }
"""


def esc(s):
    return html.escape(str(s), quote=True)


def sign_class(v):
    return "up" if v > 0 else "down" if v < 0 else "flat"


def fmt_date(d):
    return f"{d:%Y/%m/%d}（{WEEKDAYS[d.weekday()]}）"


def stock_name(code, names):
    return (f'<a class="name" href="https://kabutan.jp/stock/?code={esc(code)}" target="_blank" rel="noopener">'
            f'<span class="code">{esc(code)}</span>{esc(names.get(code, ""))}</a>')


def price_cell(price, chg):
    return f'<span class="price">{price:,.0f}円 <span class="chg {sign_class(chg)}">{chg:+.1f}%</span></span>'


def card_list(rows, initial, empty="該当なし"):
    """最初の initial 件だけ表示し、残りは「残り N件を表示」で開けるようにする。"""
    if not rows:
        return f'<div class="card"><p class="empty">{esc(empty)}</p></div>'
    more = ""
    if len(rows) > initial:
        rest = rows[initial:]
        more = f'<details><summary>残り {len(rest)}件を表示</summary><ul class="list">{"".join(rest)}</ul></details>'
    return f'<div class="card"><ul class="list">{"".join(rows[:initial])}</ul>{more}</div>'


def section(anchor, title, count, body, note=""):
    note_html = f'<p class="note">{esc(note)}</p>' if note else ""
    return (f'<section id="{esc(anchor)}"><h2>{esc(title)}<span class="count">{count}件</span></h2>'
            f'{note_html}{body}</section>')


def market_section(market):
    if market is None:
        body = '<div class="card"><p class="empty">取得に失敗しました</p></div>'
    else:
        tiles = []
        for m in market:
            if m["value"] is None:
                tiles.append(f'<div class="tile"><div class="label">{esc(m["label"])}</div>'
                             f'<div class="value flat">—</div></div>')
                continue
            tiles.append(
                f'<div class="tile"><div class="label"><span>{esc(m["label"])}</span><span>{esc(m["date"])}</span></div>'
                f'<div class="value">{esc(m["value"])}</div>'
                f'<div class="chg {sign_class(m["sign"])}">{esc(m["change"])}</div></div>')
        body = f'<div class="market">{"".join(tiles)}</div>'
    return f'<section id="market"><h2>マーケット概況</h2>{body}</section>'


def focus_section(focus, quotes, names, initial):
    rows = []
    for code, tags in focus:
        right = price_cell(*quotes[code]) if code in quotes else "<span></span>"
        tag_html = "".join(f'<span class="tag">{esc(t)}</span>' for t in tags)
        rows.append(f'<li>{stock_name(code, names)}{right}<span class="tags">{tag_html}</span></li>')
    return section("focus", "注目：複数の条件に該当", len(focus), card_list(rows, initial),
                   note="当てはまる条件やニュースが多い順に並べています")


def condition_section(key, label, items, names, initial):
    rows = []
    for _, code, detail, price, chg in items:
        detail_html = f'<span class="detail">{esc(detail)}</span>' if detail else ""
        rows.append(f'<li>{stock_name(code, names)}{price_cell(price, chg)}{detail_html}</li>')
    return section(key, label, len(items), card_list(rows, initial))


def news_section(index, label, items, names, initial):
    rows = [f'<li>{stock_name(code, names)}<span class="time">{esc(when)}</span>'
            f'<a class="detail" href="{esc(url)}" target="_blank" rel="noopener">{esc(title)}</a></li>'
            for _, code, when, title, url in items]
    return section(f"news{index}", f"ニュース：{label}", len(items), card_list(rows, initial))


def page(title, body, root):
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>
<body><div class="wrap">
{body}
<footer>
<p><a href="{root}index.html">最新のレポート</a>　<a href="{root}archive.html">過去のレポート</a></p>
<p>銘柄名をタップすると株探のページ、ニュースの表題をタップすると開示資料（PDF）が開きます。</p>
<p>データ: Yahoo Finance・JPX・TDnet。条件に合う銘柄を機械的に抽出したもので、売買の推奨ではありません。</p>
</footer>
</div></body>
</html>
"""


def render_report(r, root):
    conds = r["config"]["conditions"]
    initial = r["config"]["notify"]["max_per_condition"]
    names = r["names"]
    news = r.get("news")

    nav = []
    body = []
    if "market" in r:
        nav.append('<a class="chip" href="#market">概況</a>')
        body.append(market_section(r["market"]))

    nav.append(f'<a class="chip hot{" zero" if not r["focus"] else ""}" href="#focus">注目<b>{len(r["focus"])}</b></a>')
    body.append(focus_section(r["focus"], r["quotes"], names, initial))

    for key, items in r["hits"].items():
        label = conds[key]["label"]
        nav.append(f'<a class="chip{" zero" if not items else ""}" href="#{esc(key)}">{esc(label)}<b>{len(items)}</b></a>')
        body.append(condition_section(key, label, items, names, conds[key].get("max_items", initial)))

    if "news" in r:
        if news is None:
            nav.append('<a class="chip" href="#news0">ニュース</a>')
            body.append('<section id="news0"><h2>ニュース</h2><div class="card"><p class="empty">'
                        '適時開示の取得に失敗しました</p></div></section>')
        else:
            total = sum(len(v) for v in news.values())
            nav.append(f'<a class="chip{" zero" if not total else ""}" href="#news0">ニュース<b>{total}</b></a>')
            limit = r["config"]["news"].get("max_items", initial)
            for i, (label, items) in enumerate(news.items()):
                body.append(news_section(i, label, items, names, limit))

    warn = ""
    if r["stale"]:
        warn = (f'<div class="warn">⚠️ {r["report_date"]:%m/%d} の終値がまだ配信されていないため、'
                f'{r["data_date"]:%m/%d} の終値で判定しています</div>')
    header = (f'<header><h1>株スクリーニング</h1>'
              f'<div class="sub">{fmt_date(r["data_date"])}終値ベース・判定 {r["checked"]:,}銘柄・'
              f'{r["generated_at"]:%m/%d %H:%M} 更新</div>{warn}</header>')
    content = header + f'<nav>{"".join(nav)}</nav>' + "".join(body)
    return page(f"株スクリーニング {r['data_date']:%m/%d}", content, root)


def render_archive(dates):
    rows = [f'<li><a class="name" href="reports/{d}.html">{fmt_date(date.fromisoformat(d))}</a></li>' for d in dates]
    body = f'<header><h1>過去のレポート</h1></header><section>{card_list(rows, len(rows), "まだありません")}</section>'
    return page("過去のレポート", body, "")


def write_site(out_dir, r):
    """日付別のページ・最新ページ・過去一覧を書き出し、日付別ページの相対パスを返す。"""
    out = Path(out_dir)
    (out / "reports").mkdir(parents=True, exist_ok=True)
    rel = f"reports/{r['report_date']:%Y-%m-%d}.html"
    (out / rel).write_text(render_report(r, "../"), encoding="utf-8")
    (out / "index.html").write_text(render_report(r, ""), encoding="utf-8")
    dates = sorted((p.stem for p in (out / "reports").glob("*.html")), reverse=True)
    (out / "archive.html").write_text(render_archive(dates), encoding="utf-8")
    return rel
