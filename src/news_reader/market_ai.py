"""AI-assisted market summary window with a deterministic local fallback."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import scrolledtext, ttk
from typing import Callable

from news_reader.llm_client import (
    ChatMessage,
    GeminiClient,
    LlmRateLimitError,
)
from news_reader.news import read_news


SYSTEM_INSTRUCTION = """你是台美股票市場儀表板內的研究助理。請使用繁體中文回答。
數據分析只能根據使用者訊息中的市場快照；若已啟用 Google Search，新聞與事件必須來自搜尋結果。
不可虛構即時價格、新聞、事件或資料來源，引用新聞時要說明新聞日期與來源。
清楚區分「資料事實」與「分析推論」，指出缺漏或可能過期的資料。
不要承諾報酬、不要下確定性買賣指令，最後以一句話提醒內容僅供資訊參考。
回答務求精簡、可掃讀；涉及數字時保留快照中的單位。"""

WEB_SEARCH_TERMS = (
    "網路",
    "新聞",
    "最新",
    "消息",
    "事件",
    "搜尋",
    "查詢",
    "怎麼了",
    "怎麼回事",
    "為什麼",
    "為何",
    "原因",
    "發生什麼",
    "異動",
    "大漲",
    "大跌",
    "暴漲",
    "暴跌",
    "search",
    "news",
)


def should_use_web_search(question: str) -> bool:
    normalized = question.casefold()
    return any(term in normalized for term in WEB_SEARCH_TERMS)


def fetch_news_context(
    query: str,
    *,
    limit: int = 8,
    timeout: int = 15,
) -> tuple[str, list[tuple[str, str]]]:
    """Fetch current headlines through the project's Google News RSS reader."""
    items = read_news(query, "zh-TW", "TW", limit, timeout)
    lines = []
    sources: list[tuple[str, str]] = []
    for index, item in enumerate(items, start=1):
        lines.append(
            f"[{index}] {item.published or '--'}｜{item.source or '未知來源'}｜"
            f"{item.title}\n{item.link}"
        )
        if item.link and (item.title, item.link) not in sources:
            sources.append((item.title, item.link))
    if not lines:
        return "", []
    return (
        "Google News RSS 即時新聞標題（僅能視為標題層級證據，"
        "不可虛構內文）：\n" + "\n".join(lines),
        sources,
    )


def build_quota_fallback(
    question: str,
    snapshot: dict,
    news_context: str,
) -> str:
    """Keep a useful, evidence-only answer visible when Gemini is rate limited."""
    lines = [
        "Gemini 免費額度目前忙碌，以下先顯示可驗證資料；尚未由 LLM 綜合推論。",
        "",
        "市場快照",
    ]
    matched = []
    for row in snapshot.get("stocks", []):
        name = str(row.get("name", ""))
        terms = [part for part in name.split() if len(part) >= 2]
        if any(term.casefold() in question.casefold() for term in terms):
            matched.append(row)
    if matched:
        for row in matched:
            lines.append(
                f"- {row.get('name')}: {_change_text(row)}；"
                f"行情時間 {row.get('market_time', '--')}"
            )
    else:
        lines.append("- 快照中找不到與問題直接對應的個股。")
    indices = snapshot.get("indices", [])
    if indices:
        lines.append(
            "- 主要指數："
            + "、".join(
                f"{row.get('name')} {_change_text(row)}" for row in indices
            )
        )
    if news_context:
        lines.extend(["", news_context])
    else:
        lines.extend(["", "目前也無法取得相關新聞標題。"])
    lines.extend(
        [
            "",
            "解讀限制",
            "- 新聞標題只能確認市場正在報導的事項，不能取代完整新聞內文。",
            "- 額度恢復後可再次送出問題，取得行情與新聞的交叉分析。",
            "",
            "以上僅供資訊參考，不構成投資建議。",
        ]
    )
    return "\n".join(lines)


def _number(value: object, digits: int = 2) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _change_text(row: dict) -> str:
    return f"{_number(row.get('price'))}（{float(row.get('change_percent') or 0):+.2f}%）"


def render_snapshot(snapshot: dict) -> str:
    """Create a bounded, auditable text representation for the model."""
    lines = [
        f"快照建立時間：{snapshot.get('generated_at', '--')}",
        f"市場時段：{snapshot.get('session', '--')}",
        f"開啟分析時的頁面：{snapshot.get('active_page', '--')}",
        f"Dashboard 行情來源：{snapshot.get('quote_source', '--')}",
        f"Dashboard 狀態：{snapshot.get('quote_status', '--')}",
        "",
        "【台股四大指數】",
    ]
    for row in snapshot.get("indices", []):
        lines.append(
            f"- {row.get('name')}: {_change_text(row)}；K線 {row.get('grain', '--')}；"
            f"行情標記 {row.get('market_time', '--')}"
        )
    breadth = snapshot.get("breadth")
    lines.append("\n【上市上櫃漲跌家數】")
    if breadth:
        lines.append(
            f"- 上漲 {breadth['up']:,}、持平 {breadth['flat']:,}、下跌 {breadth['down']:,}；"
            f"資料日 上市 {breadth['twse_date']}／上櫃 {breadth['tpex_date']}"
        )
    else:
        lines.append("- 暫無資料")
    lines.append("\n【三大法人（億元）】")
    institutions = snapshot.get("institutions", [])
    if institutions:
        for row in institutions:
            lines.append(
                f"- {row['name']}: 買進 {_number(row['buy'])}、賣出 {_number(row['sell'])}、"
                f"買賣超 {float(row['net']):+.2f}；資料日 {row['date']}"
            )
    else:
        lines.append("- 暫無資料")
    for title, key in (
        ("觀察中的台股個股", "stocks"),
        ("ETF 摘要", "etfs"),
        ("台指近月期貨", "futures"),
        ("美股四大指數", "us_indices"),
        ("台灣企業 ADR／ADS", "adrs"),
        ("觀察中的美股個股", "us_stocks"),
    ):
        lines.append(f"\n【{title}】")
        rows = snapshot.get(key, [])
        if not rows:
            lines.append("- 暫無資料")
            continue
        for row in rows:
            lines.append(f"- {row.get('name')}: {_change_text(row)}；{row.get('market_time', '--')}")
    errors = snapshot.get("errors", [])
    if errors:
        lines.append("\n【資料限制】")
        lines.extend(f"- {item}" for item in errors)
    return "\n".join(lines)


def build_local_summary(snapshot: dict) -> str:
    """Produce a useful summary even when no cloud API key is available."""
    indices = snapshot.get("indices", [])
    us_indices = snapshot.get("us_indices", [])
    analysis_indices = (
        us_indices
        if snapshot.get("active_page") == "美股" and us_indices
        else indices
    )
    gainers = [row for row in analysis_indices if (row.get("change_percent") or 0) > 0]
    decliners = [row for row in analysis_indices if (row.get("change_percent") or 0) < 0]
    lines = [
        "今日市場摘要（本機規則分析）",
        "",
        "資料事實",
        f"- 市場時段：{snapshot.get('session', '--')}；快照：{snapshot.get('generated_at', '--')}",
    ]
    if indices:
        lines.append(
            "- 四大指數：" + "、".join(f"{row['name']} {_change_text(row)}" for row in indices)
        )
    else:
        lines.append("- 四大指數目前尚無可用行情。")
    breadth = snapshot.get("breadth")
    if breadth:
        lines.append(
            f"- 上市＋上櫃：上漲 {breadth['up']:,} 家、持平 {breadth['flat']:,} 家、"
            f"下跌 {breadth['down']:,} 家。"
        )
    institutions = snapshot.get("institutions", [])
    if institutions:
        lines.append(
            "- 法人買賣超：" + "、".join(f"{row['name']} {row['net']:+.2f} 億" for row in institutions)
        )
    if us_indices:
        lines.append(
            "- 美股四大指數："
            + "、".join(f"{row['name']} {_change_text(row)}" for row in us_indices)
        )
    lines.extend(["", "分析推論"])
    if gainers and not decliners:
        lines.append("- 四大指數同步走高，指數面動能偏多；仍需搭配成交量與漲跌家數確認廣度。")
    elif decliners and not gainers:
        lines.append("- 四大指數同步走低，短線風險偏好較弱，宜留意跌勢是否擴散。")
    elif gainers and decliners:
        lines.append("- 四大指數漲跌分歧，盤勢較偏輪動，單一指數不宜代表整體市場。")
    else:
        lines.append("- 指數變化不足，暫時無法判斷方向。")
    if breadth:
        if breadth["up"] > breadth["down"] * 1.25:
            lines.append("- 上漲家數明顯多於下跌家數，市場廣度偏正向。")
        elif breadth["down"] > breadth["up"] * 1.25:
            lines.append("- 下跌家數明顯多於上漲家數，市場廣度偏弱。")
        else:
            lines.append("- 漲跌家數接近，市場廣度未呈現明顯單邊。")
    if snapshot.get("errors"):
        lines.append("- 部分資料取得失敗，以上判讀可信度較低，請查看下方資料限制。")
    lines.extend(["", "此摘要依公開行情自動整理，僅供資訊參考，不構成投資建議。"])
    return "\n".join(lines)


class MarketAiWindow:
    """Toplevel for market summary and bounded conversational analysis."""

    def __init__(
        self,
        parent: tk.Tk,
        snapshot_loader: Callable[[], dict],
        theme_getter: Callable[[], dict],
    ) -> None:
        self.snapshot_loader = snapshot_loader
        self.theme_getter = theme_getter
        self.client = GeminiClient()
        self.snapshot: dict | None = None
        self.history: list[ChatMessage] = []
        self.queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.loading = False
        self.closed = False

        self.window = tk.Toplevel(parent)
        self.window.title("AI 盤勢分析｜今日摘要與問答")
        self.window.geometry("980x760")
        self.window.minsize(760, 620)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.status_text = tk.StringVar(value="準備建立市場快照…")
        self.provider_text = tk.StringVar(value=self.client.provider_label)
        self.question_text = tk.StringVar()
        self.build_ui()
        self.apply_theme()
        self.refresh_analysis()
        self.window.after(150, self.process_results)

    def build_ui(self) -> None:
        self.frame = ttk.Frame(self.window, style="TFrame", padding=16)
        self.frame.pack(fill="both", expand=True)
        header = ttk.Frame(self.frame, style="Header.TFrame", padding=(18, 14))
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(
            header, text="AI 盤勢分析", style="Header.TLabel",
            font=("Microsoft JhengHei UI", 20, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header, textvariable=self.status_text, style="Muted.TLabel",
            font=("Microsoft JhengHei UI", 9),
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(
            header, textvariable=self.provider_text, style="Muted.TLabel",
            font=("Microsoft JhengHei UI", 9),
        ).grid(row=2, column=0, sticky="w", pady=(2, 0))
        ttk.Button(
            header, text="重新分析", style="Flat.TButton", command=self.refresh_analysis,
        ).grid(row=0, column=1, rowspan=3, sticky="e")
        header.columnconfigure(0, weight=1)

        body = ttk.Panedwindow(self.frame, orient="vertical")
        body.pack(fill="both", expand=True)
        summary_panel = ttk.Frame(body, style="Card.TFrame", padding=12)
        chat_panel = ttk.Frame(body, style="Card.TFrame", padding=12)
        body.add(summary_panel, weight=3)
        body.add(chat_panel, weight=2)
        ttk.Label(
            summary_panel, text="今日市場摘要", style="CardTitle.TLabel",
            font=("Microsoft JhengHei UI", 12, "bold"),
        ).pack(anchor="w", pady=(0, 6))
        self.summary_box = scrolledtext.ScrolledText(
            summary_panel, wrap="word", height=16, borderwidth=0,
            font=("Microsoft JhengHei UI", 10), padx=10, pady=10,
        )
        self.summary_box.pack(fill="both", expand=True)
        self.summary_box.configure(state="disabled")

        ttk.Label(
            chat_panel, text="市場問答", style="CardTitle.TLabel",
            font=("Microsoft JhengHei UI", 12, "bold"),
        ).pack(anchor="w", pady=(0, 6))
        self.chat_box = scrolledtext.ScrolledText(
            chat_panel, wrap="word", height=8, borderwidth=0,
            font=("Microsoft JhengHei UI", 10), padx=10, pady=10,
        )
        self.chat_box.pack(fill="both", expand=True)
        self.chat_box.configure(state="disabled")
        ask = ttk.Frame(chat_panel, style="Card.TFrame")
        ask.pack(fill="x", pady=(8, 0))
        self.question_entry = ttk.Entry(ask, textvariable=self.question_text)
        self.question_entry.pack(side="left", fill="x", expand=True)
        self.question_entry.bind("<Return>", lambda _event: self.ask_question())
        self.send_button = ttk.Button(
            ask, text="送出問題", style="Flat.TButton", command=self.ask_question,
        )
        self.send_button.pack(side="left", padx=(8, 0))
        ttk.Label(
            self.frame,
            text=(
                "事件型問題會先讀取 Google News RSS；必要時才使用 Google Search；"
                "內容僅供資訊參考。"
            ),
            style="Muted.TLabel", font=("Microsoft JhengHei UI", 9),
        ).pack(anchor="w", pady=(8, 0))

    def apply_theme(self) -> None:
        theme = self.theme_getter()
        self.window.configure(bg=theme["bg"])
        for widget in (self.summary_box, self.chat_box):
            widget.configure(
                bg=theme["surface_alt"], fg=theme["text"],
                insertbackground=theme["text"], selectbackground=theme["selected"],
            )

    def close(self) -> None:
        self.closed = True
        self.window.destroy()

    def _set_text(self, widget: scrolledtext.ScrolledText, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text)
        widget.configure(state="disabled")

    def _append_chat(self, speaker: str, text: str) -> None:
        self.chat_box.configure(state="normal")
        if self.chat_box.get("1.0", "end-1c").strip():
            self.chat_box.insert("end", "\n\n")
        self.chat_box.insert("end", f"{speaker}\n{text}")
        self.chat_box.see("end")
        self.chat_box.configure(state="disabled")

    def refresh_analysis(self) -> None:
        if self.loading or self.closed:
            return
        self.loading = True
        self.status_text.set("正在讀取行情、法人與市場廣度…")
        threading.Thread(target=self._analysis_worker, daemon=True).start()

    def _analysis_worker(self) -> None:
        try:
            snapshot = self.snapshot_loader()
            local_summary = build_local_summary(snapshot)
            self.queue.put(("snapshot", (snapshot, local_summary)))
            if self.client.is_configured:
                prompt = "請根據以下市場快照，產生今日盤勢總結、主要風險與值得觀察的訊號：\n\n" + render_snapshot(snapshot)
                answer = self.client.generate(
                    [ChatMessage("user", prompt)],
                    system_instruction=SYSTEM_INSTRUCTION,
                    max_output_tokens=3000,
                )
                self.queue.put(("analysis", answer))
        except Exception as exc:
            self.queue.put(("error", exc))
        finally:
            self.queue.put(("idle", None))

    def ask_question(self) -> None:
        question = self.question_text.get().strip()
        if not question or self.loading:
            return
        if self.snapshot is None:
            self.status_text.set("請等待市場快照載入完成。")
            return
        self.question_text.set("")
        self._append_chat("你", question)
        if not self.client.is_configured:
            self._append_chat(
                "系統",
                "尚未設定 GEMINI_API_KEY，因此目前只能顯示本機摘要；設定後重新開啟此視窗即可問答。",
            )
            return
        self.loading = True
        use_google_search = should_use_web_search(question)
        self.status_text.set(
            "正在取得相關新聞並交叉分析…"
            if use_google_search
            else "Gemini 正在分析你的問題…"
        )
        threading.Thread(
            target=self._question_worker,
            args=(question, use_google_search),
            daemon=True,
        ).start()

    def _question_worker(self, question: str, use_google_search: bool) -> None:
        try:
            context = "目前市場快照如下：\n" + render_snapshot(self.snapshot or {})
            search_instruction = ""
            news_context = ""
            news_sources: list[tuple[str, str]] = []
            use_grounding = False
            if use_google_search:
                snapshot_date = str(
                    (self.snapshot or {}).get("generated_at", "")
                )[:10]
                news_query = f"{question} {snapshot_date}".strip()
                try:
                    news_context, news_sources = fetch_news_context(news_query)
                except Exception:
                    news_context = ""
                    news_sources = []
                use_grounding = not bool(news_context)
                search_instruction = (
                    "\n\n此問題在詢問今日事件或原因。請使用下方新聞標題與市場快照"
                    "交叉分析，優先採用快照日期當天及前一交易日的資訊，"
                    "說明可驗證的直接原因；若標題不足以證明確切原因，必須明說，"
                    "不可只用價格走勢推測消息面。"
                )
                if news_context:
                    search_instruction += f"\n\n{news_context}"
            messages = [
                *self.history[-6:],
                ChatMessage(
                    "user",
                    (
                        f"{context}{search_instruction}\n\n"
                        f"使用者目前問題：{question}"
                    ),
                ),
            ]
            try:
                answer = self.client.generate(
                    messages,
                    system_instruction=SYSTEM_INSTRUCTION,
                    max_output_tokens=4096,
                    use_google_search=use_grounding,
                )
            except LlmRateLimitError:
                if news_context:
                    answer = build_quota_fallback(
                        question,
                        self.snapshot or {},
                        news_context,
                    )
                    news_sources = []
                elif use_grounding:
                    try:
                        answer = self.client.generate(
                            messages,
                            system_instruction=SYSTEM_INSTRUCTION,
                            max_output_tokens=4096,
                            use_google_search=False,
                        )
                        answer = (
                            "[Google Search 額度不足，本次僅依 Dashboard 快照分析。]"
                            "\n\n" + answer
                        )
                    except LlmRateLimitError:
                        answer = build_quota_fallback(
                            question,
                            self.snapshot or {},
                            "",
                        )
                else:
                    answer = build_quota_fallback(
                        question,
                        self.snapshot or {},
                        "",
                    )
            if news_sources:
                answer += "\n\n新聞與網路來源（Google News RSS）\n" + "\n".join(
                    f"[{index}] {title}\n{uri}"
                    for index, (title, uri) in enumerate(
                        news_sources[:8], start=1
                    )
                )
            self.queue.put(("answer", (question, answer)))
        except Exception as exc:
            self.queue.put(("error", exc))
        finally:
            self.queue.put(("idle", None))

    def process_results(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "snapshot":
                    self.snapshot, local_summary = payload
                    self._set_text(self.summary_box, local_summary)
                    self.status_text.set(
                        f"市場快照：{self.snapshot.get('generated_at', '--')}｜"
                        + ("等待 Gemini 分析…" if self.client.is_configured else "使用本機摘要")
                    )
                elif kind == "analysis":
                    self._set_text(
                        self.summary_box,
                        str(payload) + "\n\n---\n本機規則摘要可於無金鑰或雲端失敗時使用。",
                    )
                    self.status_text.set("Gemini 分析完成｜按「重新分析」才會再次使用額度")
                elif kind == "answer":
                    question, answer = payload
                    self.history.append(ChatMessage("user", str(question)))
                    self.history.append(ChatMessage("assistant", answer))
                    self._append_chat("AI 研究助理", answer)
                    self.status_text.set("問答完成")
                elif kind == "error":
                    message = str(payload)
                    self._append_chat("系統", f"雲端分析未完成：{message}")
                    self.status_text.set("已保留本機摘要；雲端分析暫不可用")
                elif kind == "idle":
                    self.loading = False
        except queue.Empty:
            pass
        if not self.closed:
            self.window.after(150, self.process_results)
