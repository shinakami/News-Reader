"""Shared Yahoo Taiwan batch transport for index and security quotes."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Sequence

YAHOO_TW_CHART_URL = "https://tw.stock.yahoo.com/_td-stock/api/resource/FinanceChartService.ApacLibraCharts"


def fetch_yahoo_tw_chart_items(
    symbols: Sequence[str],
    timeout: int,
    retries: int,
    period: str = "1m",
    range_name: str = "1d",
) -> dict[str, dict]:
    """Fetch Yahoo Taiwan charts in bounded batches and return them by symbol."""
    unique_symbols = list(dict.fromkeys(symbols))
    if not unique_symbols:
        return {}
    attempts = max(0, retries) + 1
    chunks = [
        unique_symbols[index:index + 10]
        for index in range(0, len(unique_symbols), 10)
    ]

    def fetch_chunk(chunk: list[str]) -> list[dict]:
        encoded_symbols = urllib.parse.quote(
            json.dumps(chunk, ensure_ascii=False, separators=(",", ":")),
            safe="",
        )
        resource = (
            f"{YAHOO_TW_CHART_URL};autoRefresh={int(time.time() * 1000)}"
            f";period={period};range={range_name}"
            f";symbols={encoded_symbols};type=null"
        )
        query = urllib.parse.urlencode(
            {
                "device": "desktop",
                "ecma": "modern",
                "intl": "tw",
                "lang": "zh-Hant-TW",
                "region": "TW",
                "site": "finance",
                "tz": "Asia/Taipei",
                "returnMeta": "true",
            }
        )
        request = urllib.request.Request(
            f"{resource}?{query}",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://tw.stock.yahoo.com/",
                "Cache-Control": "no-cache",
                "Connection": "close",
            },
        )
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                return payload.get("data") or []
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code in {400, 404}:
                    break
            except (OSError, json.JSONDecodeError) as exc:
                last_error = exc
            if attempt < attempts - 1:
                time.sleep(0.3 * (attempt + 1))
        raise RuntimeError(f"Yahoo 台灣批次行情載入失敗：{last_error}")

    def fetch_resilient_chunk(chunk: list[str]) -> list[dict]:
        try:
            return fetch_chunk(chunk)
        except RuntimeError:
            if len(chunk) <= 1:
                return []
            middle = len(chunk) // 2
            return [
                *fetch_resilient_chunk(chunk[:middle]),
                *fetch_resilient_chunk(chunk[middle:]),
            ]

    items: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(3, len(chunks))) as executor:
        futures = [
            executor.submit(fetch_resilient_chunk, chunk)
            for chunk in chunks
        ]
        for future in as_completed(futures):
            rows = future.result()
            for item in rows:
                symbol = item.get("symbol")
                if symbol:
                    items[symbol] = item
    if not items:
        raise RuntimeError("Yahoo 台灣批次行情未回傳任何資料")
    return items
