#!/usr/bin/env python3
"""Render README benchmark assets from docs/benchmarks/summary.json."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / "docs/benchmarks/summary.json").read_text())
OUT = ROOT / "docs/assets"
OUT.mkdir(parents=True, exist_ok=True)

BG = "#F7F8F5"
INK = "#172027"
MUTED = "#5E6B73"
LINE = "#D9DEDA"
TEAL = "#137C74"
BLUE = "#2D5BBA"
CORAL = "#D85C41"
GOLD = "#B98216"
WHITE = "#FFFFFF"


def font(size: int, bold: bool = False):
    candidates = [
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/SFCompact.ttf",
        "/System/Library/Fonts/SFNSMono.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


F14 = font(28)
F16 = font(32)
F18 = font(36)
F20 = font(40, True)
F24 = font(48, True)
F34 = font(68, True)
F42 = font(84, True)


def rounded(draw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def fit(draw, text, max_width, selected_font):
    words = text.split()
    lines = []
    current = []
    for word in words:
        trial = " ".join(current + [word])
        if current and draw.textbbox((0, 0), trial, font=selected_font)[2] > max_width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def save_benchmark():
    image = Image.new("RGB", (1800, 1050), BG)
    draw = ImageDraw.Draw(image)
    draw.text((100, 68), "QWEN CODING HARNESS  /  INITIAL EVALUATION", font=F14, fill=TEAL)
    draw.text((100, 122), "Feedback loops changed the outcome", font=F42, fill=INK)
    draw.text(
        (104, 225),
        "100 public coding tasks  •  local open-weight model  •  deterministic checks",
        font=F18,
        fill=MUTED,
    )

    entries = [
        ("One-shot public pass", DATA["one_shot_public_pass"], CORAL),
        ("Harness public pass", DATA["harness_public_pass"], GOLD),
        ("No known counterexample", DATA["no_known_counterexample"], TEAL),
    ]
    chart_left, chart_top, chart_width = 100, 350, 1000
    for index, (label, value, color) in enumerate(entries):
        y = chart_top + index * 170
        draw.text((chart_left, y), label, font=F20, fill=INK)
        rounded(draw, (chart_left, y + 62, chart_left + chart_width, y + 112), 25, "#E4E8E4")
        rounded(
            draw,
            (chart_left, y + 62, chart_left + chart_width * value / 100, y + 112),
            25,
            color,
        )
        draw.text((chart_left + chart_width + 42, y + 45), f"{value}/100", font=F34, fill=color)

    rounded(draw, (1360, 348, 1700, 820), 18, WHITE, LINE, 2)
    draw.text((1410, 400), "+41", font=F42, fill=TEAL)
    draw.text((1410, 500), "tasks recovered", font=F20, fill=INK)
    draw.line((1410, 570, 1650, 570), fill=LINE, width=2)
    draw.text((1410, 612), "4", font=F34, fill=CORAL)
    draw.text((1480, 630), "oracle-rejected", font=F16, fill=MUTED)
    draw.text((1410, 700), "4", font=F34, fill=CORAL)
    draw.text((1480, 718), "not passed", font=F16, fill=MUTED)

    footnote = (
        "Not an official hidden-test score or pass@1 result. Independent oracle coverage: "
        "19 of 41 recovered candidates; 4 additional public-test passes were rejected."
    )
    draw.line((100, 920, 1700, 920), fill=LINE, width=2)
    draw.multiline_text((100, 950), "\n".join(fit(draw, footnote, 1550, F14)), font=F14, fill=MUTED, spacing=8)
    image.save(OUT / "benchmark-comparison.png", optimize=True)


def save_debugger_loop():
    image = Image.new("RGB", (1800, 860), BG)
    draw = ImageDraw.Draw(image)
    draw.text((100, 65), "REAL REPAIR TRACE  /  PROBLEM 3423", font=F14, fill=BLUE)
    draw.text((100, 120), "A failing candidate became a verified candidate", font=F34, fill=INK)
    draw.text((100, 215), "The repair loop was exercised, not simulated.", font=F18, fill=MUTED)

    stages = [
        ("01", "ONE-SHOT", "Stopped at the\n16,384-token cap", CORAL),
        ("02", "FIRST CANDIDATE", "Public case failed\nactual 18  •  expected 21", GOLD),
        ("03", "DEBUGGER", "Repaired segment-tree\nlayout and ordering", BLUE),
        ("04", "VERIFIED", "Public 2/2  •  oracle\n2,000 random cases", TEAL),
    ]
    card_width, gap, start_x, top = 370, 42, 100, 340
    for index, (number, title, body, color) in enumerate(stages):
        left = start_x + index * (card_width + gap)
        rounded(draw, (left, top, left + card_width, top + 330), 18, WHITE, LINE, 2)
        rounded(draw, (left + 28, top + 28, left + 92, top + 92), 12, color)
        draw.text((left + 43, top + 41), number, font=F14, fill=WHITE)
        draw.text((left + 28, top + 125), title, font=F16, fill=color)
        draw.multiline_text((left + 28, top + 190), body, font=F16, fill=INK, spacing=12)
        if index < len(stages) - 1:
            x = left + card_width + 8
            draw.line((x, top + 165, x + 26, top + 165), fill=MUTED, width=4)
            draw.polygon([(x + 26, top + 155), (x + 42, top + 165), (x + 26, top + 175)], fill=MUTED)

    draw.text(
        (100, 745),
        "Candidate generation and repair used the local model; the post-freeze oracle did not edit the solution.",
        font=F14,
        fill=MUTED,
    )
    image.save(OUT / "debugger-loop.png", optimize=True)


if __name__ == "__main__":
    save_benchmark()
    save_debugger_loop()
    print(f"Rendered assets in {OUT}")
