"""Tk display sizing in 96-DPI logical pixels (do not override OS font scaling)."""


def display_scale(window):
    return max(1.0, window.winfo_fpixels("1i") / 96.0)


def fit_window(window, preferred, minimum):
    scale = display_scale(window)
    # Leave space for the window frame, title bar and desktop taskbar.
    usable = (max(1, window.winfo_screenwidth() - round(32 * scale)),
              max(1, window.winfo_screenheight() - round(80 * scale)))
    size = tuple(min(round(n * scale), cap) for n, cap in zip(preferred, usable))
    window.minsize(*(min(n, cap) for n, cap in zip(minimum, size)))
    window.geometry(f"{size[0]}x{size[1]}")
