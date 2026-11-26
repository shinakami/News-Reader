import itertools
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from collections import deque
from threading import Event, Thread
from time import monotonic, sleep
import tkinter as tk
from tkinter import ttk

import imageio_ffmpeg
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from news_reader.background_media import MediaPlayer, compose, media_frames
from news_reader.stock_dynamic import StockDynamicApp


class WallpaperTests(unittest.TestCase):
    def test_short_gif_replays_without_decoding_or_compositing_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "loop.gif"
            Image.new("RGB", (4, 4), "red").save(path, save_all=True, duration=40,
                append_images=[Image.new("RGB", (4, 4), "blue")])
            spec = ((12, 8), "fill", 100, "#000000")
            with patch("news_reader.background_media.compose", wraps=compose) as prepare:
                player = MediaPlayer.__new__(MediaPlayer)
                player.stop, player.latest, player.specs = Event(), deque(maxlen=1), (spec,)
                worker = Thread(target=player._decode, args=(path,), daemon=True)
                worker.start()
                try:
                    observed = set()
                    until = monotonic() + 2
                    while monotonic() < until and len(observed) != 2:
                        if player.latest:
                            raw, surfaces, error, _ = player.latest[-1]
                            self.assertIsNone(error)
                            if raw is None:
                                observed.add(surfaces[spec].getpixel((0, 0)))
                        sleep(.005)
                    self.assertEqual(observed, {(255, 0, 0), (0, 0, 255)})
                    self.assertEqual(prepare.call_count, 2)
                finally:
                    player.stop.set()
                    worker.join(2)

    def test_gif_transparent_palette_entry_does_not_become_a_color_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "transparent.gif"
            image = Image.new("P", (2, 1), 0)
            image.putpalette([131, 254, 179, 0, 255, 0] + [0]*762)
            image.putpixel((1, 0), 1)
            image.save(path, transparency=0)
            frames = media_frames(path)
            try:
                frame, _ = next(frames)
                for mode in ("fill", "fit", "stretch", "center", "tile"):
                    for opacity in (35, 100):
                        out = compose(frame, ((2, 1), mode, opacity, "#101820"))
                        self.assertEqual(out.getpixel((0, 0)), (16, 24, 32))
                        self.assertGreater(out.getpixel((1, 0))[1], 24)
            finally:
                frames.close()

    def test_long_gif_frame_recomposes_on_resize(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "slow.gif"
            Image.new("RGB", (4, 4), "red").save(path, save_all=True, duration=5000,
                append_images=[Image.new("RGB", (4, 4), "blue")])
            player = MediaPlayer.__new__(MediaPlayer)
            player.stop, player.latest = Event(), deque(maxlen=1)
            def resize(size):
                spec = (size, "fill", 100, "#000000")
                player.specs = (spec,)
                return spec
            first = resize((40, 30))
            worker = Thread(target=player._decode, args=(path,), daemon=True)
            worker.start()
            try:
                for spec in (first, None):
                    if spec is None:
                        spec = resize((80, 60))
                    until = monotonic() + 1
                    while monotonic() < until:
                        if player.latest and spec in player.latest[-1][1]:
                            break
                        sleep(.01)
                    self.assertIn(spec, player.latest[-1][1])
                    self.assertEqual(player.latest[-1][1][spec].getpixel((0, 0)), (255, 0, 0))
            finally:
                player.stop.set()
                worker.join(2)

    def test_padded_card_wallpaper_stays_aligned_after_resize(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.addCleanup(root.destroy)
        card = ttk.Frame(root, padding=(16, 10))
        card.pack(fill="both", expand=True, padx=18, pady=12)
        button = ttk.Button(card, text="走勢詳情")
        button.pack()
        tile = tk.Canvas(card, width=1, height=1, bd=0, highlightthickness=0)
        photo = tk.PhotoImage(width=400, height=200)
        for size in ("360x180", "260x120"):
            root.geometry(size)
            root.update()
            x = root.winfo_rootx() - card.winfo_rootx()
            y = root.winfo_rooty() - card.winfo_rooty()
            StockDynamicApp.place_wallpaper_tile(tile, photo, x, y)
            root.update_idletasks()
            self.assertEqual((tile.winfo_width(), tile.winfo_height()),
                             (card.winfo_width(), card.winfo_height()))
            dx, dy = tile.coords("wallpaper")
            self.assertEqual((tile.winfo_rootx()+dx, tile.winfo_rooty()+dy),
                             (root.winfo_rootx(), root.winfo_rooty()))
            self.assertLess(card.winfo_children().index(tile), card.winfo_children().index(button))

    def test_prepared_frames_preserve_modes_and_opacity(self):
        source = Image.new("RGB", (2, 1), "red")
        source.putpixel((1, 0), (0, 0, 255))
        for mode in ("fill", "fit", "stretch", "center", "tile"):
            frame = compose(source, ((7, 5), mode, 50, "#000000"))
            self.assertEqual(frame.size, (7, 5))
            self.assertLessEqual(max(frame.getextrema()[0]), 128)
            if mode == "tile":
                self.assertEqual(frame.getpixel((6, 4)), (128, 0, 0))
                self.assertEqual(frame.getpixel((5, 4)), (0, 0, 128))
            if mode in ("center", "fit"):
                self.assertEqual(frame.getpixel((0, 4)), (0, 0, 0))

    def test_gif_rewinds_with_frame_durations(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wall.gif"
            red, blue = (Image.new("RGB", (16, 16), c) for c in ("red", "blue"))
            red.save(path, save_all=True, append_images=[blue], duration=[100, 200])
            stream = media_frames(path)
            try:
                frames = [(f.getpixel((0, 0)), dt) for f, dt in itertools.islice(stream, 3)]
            finally:
                stream.close()
            self.assertEqual(frames, [((255, 0, 0), .1), ((0, 0, 255), .2), ((255, 0, 0), .1)])

    def test_mp4_with_audio_loops_as_rgb_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "影片.mp4"
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-f", "lavfi",
                            "-i", "testsrc2=size=32x32:rate=10", "-f", "lavfi", "-i",
                            "sine=frequency=440", "-t", "1", "-c:v", "libx264", "-c:a", "aac",
                            str(path)], check=True, timeout=20)
            stream = media_frames(path)
            try:
                frames = [f.tobytes() for f, _ in itertools.islice(stream, 45)]
            finally:
                stream.close()
            self.assertEqual(len(frames), 45)
            self.assertGreater(len(set(frames)), 1)
