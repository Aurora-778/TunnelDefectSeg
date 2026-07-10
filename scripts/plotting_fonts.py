"""Small cross-platform Matplotlib font selection helper.

No font files are bundled.  Callers can use the returned flag to avoid passing
Chinese glyphs to Matplotlib when the host has no CJK-capable font installed.
"""

from __future__ import annotations

from matplotlib import font_manager


CHINESE_FONT_CANDIDATES = (
    "Microsoft YaHei",
    "SimHei",
    "Noto Sans CJK SC",
    "WenQuanYi Zen Hei",
    "Arial Unicode MS",
)


def configure_chinese_font(plt) -> tuple[bool, str]:
    """Configure an installed CJK font, or return a safe ASCII fallback flag."""

    installed = {font.name for font in font_manager.fontManager.ttflist}
    plt.rcParams["axes.unicode_minus"] = False
    for font_name in CHINESE_FONT_CANDIDATES:
        if font_name in installed:
            plt.rcParams["font.sans-serif"] = [font_name]
            return True, f"已使用中文字体：{font_name}"
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
    return False, "未检测到中文字体，PNG 图表使用英文安全 fallback。"
