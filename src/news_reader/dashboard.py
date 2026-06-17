#!/usr/bin/env python3
"""Generate a static HTML dashboard from the latest market snapshot."""

from __future__ import annotations

import argparse
import html
import json
import sys
import webbrowser
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from news_reader.stock_monitor import fetch_market_data


DEFAULT_OUTPUT = "dashboard.html"


def fmt_number(value: float | int | None, digits: int = 2) -> str:
    if value is None:
        return "--"
    return f"{value:,.{digits}f}"


def fmt_signed(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "--"
    return f"{value:+,.{digits}f}"


def quote_to_dict(quote: Any) -> dict[str, Any]:
    data = asdict(quote)
    data["change"] = quote.change
    data["change_percent"] = quote.change_percent
    if hasattr(quote, "display_price"):
        data["display_price"] = quote.display_price
    return data


def render_dashboard(
    indices: list[dict[str, Any]],
    etfs: list[dict[str, Any]] | None,
    api_time: str,
    data_source: str,
    generated_at: str,
) -> str:
    payload = {
        "indices": indices,
        "etfs": etfs or [],
        "apiTime": api_time,
        "dataSource": data_source,
        "generatedAt": generated_at,
    }
    payload_json = json.dumps(payload, ensure_ascii=False)
    max_abs_change = max(
        [abs(item.get("change_percent") or 0) for item in indices] + [1]
    )

    cards = []
    for item in indices:
        change = item.get("change")
        change_percent = item.get("change_percent")
        direction = "up" if (change or 0) >= 0 else "down"
        width = min(100, abs(change_percent or 0) / max_abs_change * 100)
        cards.append(
            f"""
            <section class="metric-card">
              <div class="metric-top">
                <h2>{html.escape(item["name"])}</h2>
                <span>{html.escape(item.get("market_time") or "--")}</span>
              </div>
              <div class="metric-value">{fmt_number(item.get("price"))}</div>
              <div class="metric-change {direction}">
                {fmt_signed(change)} / {fmt_signed(change_percent)}%
              </div>
              <div class="range-line">
                <span>{fmt_number(item.get("low"))}</span>
                <div><b class="{direction}" style="width:{width:.1f}%"></b></div>
                <span>{fmt_number(item.get("high"))}</span>
              </div>
            </section>
            """
        )

    rows = []
    for item in etfs or []:
        change = item.get("change")
        direction = "up" if (change or 0) >= 0 else "down"
        rows.append(
            f"""
            <tr>
              <td>{html.escape(item["code"])}</td>
              <td>{html.escape(item["name"])}</td>
              <td>{fmt_number(item.get("display_price"))}</td>
              <td class="{direction}">{fmt_signed(change)}</td>
              <td class="{direction}">{fmt_signed(item.get("change_percent"))}%</td>
              <td>{fmt_number(item.get("high"))}</td>
              <td>{fmt_number(item.get("low"))}</td>
              <td>{fmt_number(item.get("volume"), 0)}</td>
              <td>{html.escape(item.get("market_time") or "--")}</td>
            </tr>
            """
        )

    etf_section = (
        f"""
        <section class="panel">
          <div class="panel-title">
            <h2>市值前五大 ETF</h2>
            <span>價格、漲跌幅、最高最低與成交量</span>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>代號</th>
                  <th>名稱</th>
                  <th>現價/估價</th>
                  <th>漲跌</th>
                  <th>漲跌幅</th>
                  <th>最高</th>
                  <th>最低</th>
                  <th>量</th>
                  <th>時間</th>
                </tr>
              </thead>
              <tbody>{''.join(rows)}</tbody>
            </table>
          </div>
        </section>
        """
        if etfs
        else ""
    )

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>台股監控 Dashboard</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f6f8;
      --surface: #ffffff;
      --text: #1d252d;
      --muted: #66717d;
      --line: #d9e0e7;
      --up: #b42318;
      --down: #067647;
      --accent: #2563eb;
      font-family: "Microsoft JhengHei", "Segoe UI", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
    }}
    header {{
      padding: 24px clamp(16px, 4vw, 40px) 16px;
      border-bottom: 1px solid var(--line);
      background: var(--surface);
    }}
    header h1 {{
      margin: 0 0 8px;
      font-size: clamp(24px, 3vw, 34px);
      letter-spacing: 0;
    }}
    header p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.6;
    }}
    main {{
      padding: 24px clamp(16px, 4vw, 40px) 40px;
      display: grid;
      gap: 20px;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(4, minmax(180px, 1fr));
      gap: 14px;
    }}
    .metric-card, .panel {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 1px 2px rgba(16, 24, 40, 0.05);
    }}
    .metric-card {{ padding: 16px; }}
    .metric-top {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: start;
    }}
    .metric-top h2, .panel-title h2 {{
      margin: 0;
      font-size: 16px;
      letter-spacing: 0;
    }}
    .metric-top span, .panel-title span {{
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }}
    .metric-value {{
      margin-top: 16px;
      font-size: 30px;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
    }}
    .metric-change {{
      margin-top: 8px;
      font-size: 14px;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
    }}
    .up {{ color: var(--up); }}
    .down {{ color: var(--down); }}
    .range-line {{
      margin-top: 14px;
      display: grid;
      grid-template-columns: auto 1fr auto;
      gap: 8px;
      align-items: center;
      color: var(--muted);
      font-size: 11px;
      font-variant-numeric: tabular-nums;
    }}
    .range-line div {{
      height: 7px;
      background: #e8edf3;
      border-radius: 999px;
      overflow: hidden;
    }}
    .range-line b {{
      display: block;
      height: 100%;
      min-width: 2px;
      background: currentColor;
      border-radius: inherit;
    }}
    .panel {{
      padding: 18px;
      overflow: hidden;
    }}
    .panel-title {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 14px;
    }}
    .bar-list {{
      display: grid;
      gap: 12px;
    }}
    .bar-row {{
      display: grid;
      grid-template-columns: 140px 1fr 80px;
      gap: 12px;
      align-items: center;
      font-size: 14px;
    }}
    .bar-track {{
      height: 14px;
      background: #e8edf3;
      border-radius: 999px;
      overflow: hidden;
    }}
    .bar-track span {{
      display: block;
      height: 100%;
      min-width: 2px;
      background: var(--accent);
    }}
    .table-wrap {{ overflow-x: auto; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
      font-variant-numeric: tabular-nums;
    }}
    th, td {{
      padding: 10px 8px;
      border-bottom: 1px solid var(--line);
      text-align: right;
      white-space: nowrap;
    }}
    th:nth-child(1), th:nth-child(2),
    td:nth-child(1), td:nth-child(2) {{
      text-align: left;
    }}
    th {{ color: var(--muted); font-weight: 600; }}
    .meta {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.7;
    }}
    @media (max-width: 1000px) {{
      .summary {{ grid-template-columns: repeat(2, minmax(180px, 1fr)); }}
    }}
    @media (max-width: 620px) {{
      .summary {{ grid-template-columns: 1fr; }}
      .bar-row {{ grid-template-columns: 1fr; }}
      .panel-title {{ display: block; }}
      .metric-value {{ font-size: 26px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>台股監控 Dashboard</h1>
    <p>根據目前抓到的四大指數與 ETF 即時快照產生。資料來源：{html.escape(data_source)}；行情時間：{html.escape(api_time or "--")}；產生時間：{html.escape(generated_at)}</p>
  </header>
  <main>
    <section class="summary">
      {''.join(cards)}
    </section>
    <section class="panel">
      <div class="panel-title">
        <h2>四大指數漲跌幅比較</h2>
        <span>以目前快照相對昨收計算</span>
      </div>
      <div class="bar-list" id="changeBars"></div>
    </section>
    {etf_section}
    <section class="panel meta">
      <strong>資料與方法</strong><br>
      指數與 ETF 資料由程式透過 `--source` 指定來源抓取；`auto` 模式會優先使用 TWSE，失敗時切換 Yahoo。漲跌與漲跌幅以目前價格相對昨收計算。若 ETF 即時成交價缺漏，程式會沿用既有邏輯，以買一/賣一中間價或昨收作為估價。
    </section>
  </main>
  <script>
    const DASHBOARD_DATA = {payload_json};
    const maxAbs = Math.max(1, ...DASHBOARD_DATA.indices.map(item => Math.abs(item.change_percent || 0)));
    const bars = document.getElementById("changeBars");
    DASHBOARD_DATA.indices.forEach(item => {{
      const row = document.createElement("div");
      row.className = "bar-row";
      const pct = item.change_percent || 0;
      const width = Math.min(100, Math.abs(pct) / maxAbs * 100);
      row.innerHTML = `
        <strong>${{item.name}}</strong>
        <div class="bar-track"><span style="width:${{width}}%; background:${{pct >= 0 ? "var(--up)" : "var(--down)"}}"></span></div>
        <span class="${{pct >= 0 ? "up" : "down"}}">${{pct >= 0 ? "+" : ""}}${{pct.toFixed(2)}}%</span>
      `;
      bars.appendChild(row);
    }});
  </script>
</body>
</html>
"""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用目前抓到的市場資料產生 HTML Dashboard。")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT, help="輸出的 HTML 檔案路徑。")
    parser.add_argument(
        "--source",
        choices=["auto", "twse", "yahoo"],
        default="auto",
        help="資料來源，預設 auto；TWSE 失敗時自動切 Yahoo。",
    )
    parser.add_argument("--timeout", type=int, default=15, help="網路逾時秒數，預設 15。")
    parser.add_argument("--retries", type=int, default=2, help="連線失敗時重試次數，預設 2。")
    parser.add_argument("--no-etf", action="store_true", help="不顯示 ETF 區塊。")
    parser.add_argument("--verify-ssl", action="store_true", help="強制驗證 TWSE SSL 憑證。")
    parser.add_argument("--open", action="store_true", help="產生後用預設瀏覽器開啟。")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args(argv)
    try:
        indices, api_time, data_source, etfs = fetch_market_data(
            args.timeout,
            args.verify_ssl,
            max(0, args.retries),
            args.source,
            not args.no_etf,
        )
    except RuntimeError as exc:
        print(f"產生 Dashboard 時抓取資料失敗：{exc}", file=sys.stderr)
        return 1

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html_text = render_dashboard(
        [quote_to_dict(item) for item in indices],
        [quote_to_dict(item) for item in etfs] if etfs else None,
        api_time,
        data_source,
        generated_at,
    )

    output_path = Path(args.output).expanduser()
    output_path.write_text(html_text, encoding="utf-8")
    print(f"Dashboard 已產生：{output_path.resolve()}")

    if args.open:
        webbrowser.open(output_path.resolve().as_uri())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
