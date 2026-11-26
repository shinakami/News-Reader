"""Local wallpaper decoding. Workers never access Tk or an audio device."""
from collections import deque
from pathlib import Path
from threading import Event, Thread
from time import monotonic

from PIL import Image, ImageColor, ImageOps


def on_background(frame, color):
    """Resolve transparent pixels against the wallpaper, never the GIF palette."""
    if frame.mode != "RGBA":
        return frame
    base = Image.new("RGB", frame.size, color)
    base.paste(frame, mask=frame.getchannel("A"))
    return base


def compose(frame, spec):
    """Produce a ready-to-upload wallpaper without calling Tk."""
    size, mode, opacity, color = spec
    frame = on_background(frame, color)
    resample = Image.Resampling.BILINEAR
    if mode == "fill":
        layout = ImageOps.fit(frame, size, method=resample)
    elif mode == "stretch":
        layout = frame.resize(size, resample)
    else:
        layout = Image.new("RGB", size, color)
        if mode == "tile":
            layout.paste(frame, (0, 0))
            # Doubling avoids millions of paste calls for tiny GIF tiles.
            w, h = min(frame.width, size[0]), min(frame.height, size[1])
            while w < size[0]:
                layout.paste(layout.crop((0, 0, min(w, size[0]-w), h)), (w, 0))
                w *= 2
            while h < size[1]:
                layout.paste(layout.crop((0, 0, size[0], min(h, size[1]-h))), (0, h))
                h *= 2
        else:
            img = ImageOps.contain(frame, size, method=resample) if mode == "fit" else frame
            layout.paste(img, ((size[0]-img.width)//2, (size[1]-img.height)//2))
    if opacity == 100:
        return layout
    rgb = ImageColor.getrgb(color)
    return layout.point([round((v*opacity + bg*(100-opacity))/100)
                         for bg in rgb for v in range(256)])


def media_frames(path):
    """Yield independent frames, retaining GIF transparency until compositing."""
    if Path(path).suffix.lower() == ".mp4":
        import imageio_ffmpeg

        stream = imageio_ffmpeg.read_frames(
            str(path), pix_fmt="rgb24", input_params=["-threads", "2", "-filter_threads", "1", "-stream_loop", "-1"],
            output_params=["-an", "-vf", "fps=20"],
        )
        try:
            size = next(stream)["size"]
            for raw in stream:
                yield Image.frombytes("RGB", size, raw), 0.05
        finally:
            stream.close()
    else:
        with Image.open(path) as source:
            while True:
                frame = source.convert("RGBA" if source.mode == "RGBA" or
                                       "transparency" in source.info else "RGB")
                if not getattr(source, "is_animated", False):
                    yield frame, 0
                    return
                yield frame, max(0.02, (source.info.get("duration") or 100) / 1000)
                try:
                    source.seek(source.tell() + 1)
                except EOFError:
                    source.seek(0)


class MediaPlayer:
    def __init__(self, path, specs=()):
        self.stop = Event()
        self.specs = specs
        # Drop superseded frames if Tk is busy rendering market data.
        self.latest = deque(maxlen=1)
        Thread(target=self._decode, args=(path,), daemon=True).start()

    def _play_gif(self, path):
        # Cache finished RGB surfaces, not full-resolution RGBA source frames.
        # Pillow may use four bytes/pixel internally even for RGB.
        budget = 96 * 1024 * 1024
        with Image.open(path) as source:
            animated = getattr(source, "is_animated", False)
            cache, used, count, index = {}, 0, None, 0
            signature, cacheable = None, True
            deadline = monotonic()
            while not self.stop.is_set():
                specs = self.specs
                if animated and not specs:
                    # Fully transparent or minimized: no GUI surface to paint.
                    self.stop.wait(.05)
                    deadline = monotonic()
                    continue
                if specs != signature:
                    cache.clear()
                    signature, used, cacheable = specs, 0, True
                hit = cache.get(index)
                if hit is None:
                    try:
                        source.seek(index)
                    except EOFError:
                        count, index = index, 0
                        continue
                    frame = source.convert("RGBA" if source.mode == "RGBA" or
                                           "transparency" in source.info else "RGB")
                    delay = max(.02, (source.info.get("duration") or 100)/1000) if animated else 0
                    prepared = {spec: compose(frame, spec) for spec in specs}
                    size = sum(im.width * im.height * 4 for im in prepared.values())
                    if cacheable and prepared and used + size <= budget and index < 256:
                        cache[index] = prepared, delay
                        used += size
                    elif cacheable:
                        cache.clear()
                        cacheable = False
                else:
                    prepared, delay = hit
                    frame = None  # Tk already owns the source for this animation.
                self.latest.append((frame, prepared, None, not animated))
                if not animated:
                    return
                deadline = max(monotonic(), deadline + delay)
                while self.specs == specs and not self.stop.is_set():
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        break
                    self.stop.wait(min(.05, remaining))
                if self.specs == specs:
                    index = (index+1) % count if count else index+1
                else:
                    deadline = monotonic()

    def _decode(self, path):
        if Path(path).suffix.lower() == ".gif":
            try:
                self._play_gif(path)
            except Exception as exc:
                self.latest.append((None, {}, str(exc), True))
            return
        frames = media_frames(path)
        try:
            deadline = None
            for image, delay in frames:
                now = monotonic()
                deadline = max(now, (deadline if deadline is not None else now) + delay)
                rendered_specs = None
                while not self.stop.is_set():
                    specs = self.specs
                    if specs != rendered_specs:
                        prepared = {}
                        for spec in specs:
                            if self.stop.is_set():
                                return
                            prepared[spec] = compose(image, spec)
                        self.latest.append((image, prepared, None, not delay))
                        rendered_specs = specs
                    if not delay:
                        return
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        break
                    # A GIF may hold one frame for seconds. Re-layout that
                    # same frame on resize instead of waiting for the next.
                    self.stop.wait(min(.05, remaining))
                if self.stop.is_set():
                    return
            else:
                raise ValueError("找不到可播放的影格")
        except Exception as exc:
            self.latest.append((None, {}, str(exc), True))
        finally:
            frames.close()
