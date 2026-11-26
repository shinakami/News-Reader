"""VIX history, relative sentiment and shared Tk chart views."""
import csv
import io
import math
import re
from urllib.error import HTTPError
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk
from news_reader.display import fit_window


def daily_history(market, today, read):
    values = {}
    if market == "us":
        raw = read("https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv")
        for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))):
            values[datetime.strptime(row["DATE"], "%m/%d/%Y").date()] = float(row["CLOSE"])
    else:
        month = today
        for _ in range(5):
            url = f"https://www.taifex.com.tw/file/taifex/Dailydownload/vix/log2data/{month:%Y%m}new.txt"
            try:
                raw = read(url)
            except HTTPError as exc:
                if exc.code != 404 or month.month != today.month:
                    raise
                raw = b""
            for day, price in re.findall(rb"(?m)^([0-9]{8})[ \t]+[0-9]{8}[ \t]+([0-9.]+)[ \t]+[0-9.]+", raw):
                values[datetime.strptime(day.decode(), "%Y%m%d").date()] = float(price)
            if len(values) >= 61:
                break
            month = month.replace(day=1) - timedelta(days=1)
    return sorted((d, v) for d, v in values.items() if d <= today and math.isfinite(v) and v > 0)[-61:]


def thresholds(history, day):
    sample = [v for d, v in history if d < day][-60:]
    if len(sample) != 60:
        return ()
    ordered = sorted(sample)
    return tuple(ordered[math.ceil(60*p)-1] for p in (.25, .75, .90))


class VixPanel:
    def __init__(self, parent, name, quote, theme, today):
        self.name, self.quote, self.theme, self.today = name, quote, theme, today
        self.history, self.points = [], {}
        self.day = self.price = self.window = None
        self.mode = tk.StringVar(value="今日盤中")
        self.status = tk.StringVar(value="等待市場情緒資料")
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.pack(fill="x")
        ttk.Button(frame, text="開啟獨立視窗", command=self.open).pack(side="right")
        ttk.Label(frame, textvariable=self.status, style="Muted.TLabel").pack(anchor="w")
        self.mini = tk.Canvas(frame, height=40, width=120, bg=theme["surface_alt"], highlightthickness=0, cursor="hand2")
        self.mini.pack(fill="x", expand=True)
        self.bind_inspector(self.mini)
        self.mini.bind("<Double-Button-1>", lambda _: self.open())
        self.mini.bind("<Configure>", lambda _: self.draw(self.mini, False))

    def update(self, points=(), close=None):
        today = self.today()
        self.points = {t:v for t,v in self.points.items() if t.date() == today}
        for t, v in points:
            if t.date() == today and math.isfinite(v) and v > 0:
                self.points[t] = v
        if close:
            self.day, self.price = close
        elif self.points:
            t = max(self.points)
            self.day, self.price = t.date(), self.points[t]
        self.redraw()

    def redraw(self):
        cuts = thresholds(self.history, self.day or self.today())
        mood = ("平靜", "常態", "警戒", "高度緊張")[sum(self.price >= c for c in cuts)] if cuts and self.price else "資料不足"
        self.status.set("相對近 60 日：" + mood)
        self.draw(self.mini, False)
        if self.window and self.window.winfo_exists():
            self.draw(self.large, True)

    def open(self):
        if self.window and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            return
        self.window = w = tk.Toplevel(self.mini)
        w.title(self.name + "｜波動率走勢")
        fit_window(w, (660, 350), (420, 260))
        bar = ttk.Frame(w, padding=8)
        bar.pack(fill="x")
        ttk.Label(bar, textvariable=self.quote, style="CardTitle.TLabel").pack(anchor="w")
        for mode in ("今日盤中", "近 60 日收盤"):
            ttk.Button(bar, text=mode, command=lambda m=mode: (self.mode.set(m), self.draw(self.large, True))).pack(side="left")
        self.large = tk.Canvas(w, bg=self.theme["surface_alt"], highlightthickness=0, cursor="crosshair")
        self.large.pack(fill="both", expand=True)
        self.bind_inspector(self.large)
        self.large.bind("<Configure>", lambda _: self.draw(self.large, True))

    def bind_inspector(self, canvas):
        canvas.samples = {}
        canvas.selected_time = None
        def inspect(event):
            x, y = event.x, event.y
            candidates = []
            for item in canvas.find_overlapping(x-5, y-5, x+5, y+5):
                for tag in canvas.gettags(item):
                    if tag in canvas.samples:
                        candidates.append(canvas.samples[tag])
            canvas.selected_time = (min(candidates, key=lambda p: (x-p[0])**2+(y-p[1])**2)[2]
                                    if candidates else None)
            self.show_selection(canvas)
        canvas.bind("<Button-1>", inspect)

    def show_selection(self, canvas):
        canvas.delete("inspection")
        for x, y, stamp, value in canvas.samples.values():
            if stamp != canvas.selected_time:
                continue
            canvas.create_oval(x-4,y-4,x+4,y+4,outline=self.theme["text"],width=2,tags="inspection")
            date_format = "%Y-%m-%d %H:%M:%S" if isinstance(stamp, datetime) else "%Y-%m-%d"
            text = canvas.create_text(x+8,y-8,anchor="sw",fill=self.theme["text"],
                                      text=f"{stamp:{date_format}}  ｜  {value:.2f} 點",tags="inspection")
            l,t,r,b = canvas.bbox(text)
            dx = max(4-l, min(0, canvas.winfo_width()-4-r))
            dy = max(3-t, min(0, canvas.winfo_height()-3-b))
            canvas.move(text, dx, dy)
            box = canvas.create_rectangle(l+dx-3,t+dy-2,r+dx+3,b+dy+2,fill=self.theme["surface"],
                                          outline=self.theme["accent"],tags="inspection")
            canvas.tag_lower(box,text)
            break

    def draw(self, canvas, detail):
        canvas.delete("all")
        canvas.samples.clear()
        daily = detail and self.mode.get() == "近 60 日收盤"
        rows = self.history[-60:] if daily else sorted(self.points.items())
        w, h = canvas.winfo_width(), canvas.winfo_height()
        color = self.theme["muted"]
        def label(x,y,s,anchor="w"):
            canvas.create_text(x,y,text=s,anchor=anchor,fill=color)
        if not rows:
            label(8,h/2,"尚無收盤歷史" if daily else "今日尚無盤中觀測")
            return
        cuts = thresholds(self.history, self.day or self.today()) if detail else ()
        if detail:
            label(10,12,self.mode.get() + "｜" + self.status.get())
            label(10,34,("平靜 < %.2f ≤ 常態 < %.2f ≤ 警戒 < %.2f ≤ 高度緊張" % cuts) if cuts else "歷史樣本不足 60 日")
        times = list(range(len(rows))) if daily else [t.timestamp() for t,v in rows]
        vals = [v for _,v in rows]
        lo, hi = min([*vals,*cuts]), max([*vals,*cuts])
        left, right = (54, w-18) if detail else (6,w-6)
        top, bottom = (60,h-28) if detail else (4,h-16)
        def y(v):
            return (top+bottom)/2 if hi == lo else bottom-(v-lo)/(hi-lo)*(bottom-top)
        if detail:
            for c in cuts:
                canvas.create_line(left,y(c),right,y(c),fill=color,dash=(2,4))
                label(left-4,y(c),f"{c:.2f}","e")
        prev = None
        for index,(t,v) in enumerate(zip(times,vals)):
            x = left+(t-times[0])/max(1,times[-1]-times[0])*(right-left)
            p = (x,y(v))
            tag = f"sample{index}"
            canvas.samples[tag] = (*p, rows[index][0], v)
            if prev and (daily or t-prev[0] <= 120):
                canvas.create_line(*prev[1],*p,fill=self.theme["accent"],width=2,tags=(f"sample{index-1}", tag))
            canvas.create_oval(x-1.4,p[1]-1.4,x+1.4,p[1]+1.4,fill=self.theme["accent"],outline="",tags=tag)
            prev = t,p
        fmt = "%Y-%m-%d" if daily else "%H:%M:%S"
        label(left,h-8,rows[0][0].strftime(fmt))
        if len(rows)>1:
            label(right,h-8,rows[-1][0].strftime(fmt),"e")
        self.show_selection(canvas)
