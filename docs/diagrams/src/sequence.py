#!/usr/bin/env python3
"""작은 JSON 사양으로 시퀀스 다이어그램 SVG를 만든다(라이트·다크 테마).

표준 라이브러리만 쓰므로 fashion-planner의 다른 레포도 이 파일을 그대로 복사해 쓸 수 있다.

사용법:
    python sequence.py SPEC.json OUT.svg [--theme light|dark]

사양(JSON 객체):
    {
      "title": "제목(선택)",
      "lanes": [
        {"id": "ui", "name": "브라우저 UI", "sub": "127.0.0.1:8765", "kind": "client"},
        {"id": "orch", "name": "orchestrator", "sub": ":8765", "kind": "service"},
        {"id": "llm", "name": "OpenAI API", "sub": "선택", "kind": "external"},
        {"id": "db", "name": "SQLite", "sub": "실행 기록", "kind": "store"}
      ],
      "rows": [
        {"kind": "band", "text": "1단계 trend"},
        {"kind": "call", "from": "orch", "to": "trend", "lines": ["POST /v1/agents/trend/runs", "TrendQuery"]},
        {"kind": "return", "from": "trend", "to": "orch", "lines": ["200 TrendAnalysisResult"]},
        {"kind": "self", "from": "orch", "lines": ["응답 원문을 검증하고 저장"]},
        {"kind": "error", "from": "orch", "to": "ui", "lines": ["4xx/5xx problem 본문"]},
        {"kind": "note", "text": "바로 위 행 아래에 붙는 설명"}
      ]
    }

레인 kind: "service"(채움), "client"(테두리), "external"(점선 테두리, 외부 시스템),
"store"(테두리 + 원통 표시). 행 kind: band, call, return, self, error, note.
레인으로 들어가는 call은 활성 막대를 열고, 짝이 되는 return이나 error가 닫는다.
라벨은 한국어로 쓴다(DIAGRAM-STYLE, D12). 서비스명·코드 식별자·엔드포인트는 원문 그대로 둔다.
SVG는 글꼴 이름만 지정하고 글꼴 파일은 넣지 않는다(Pretendard, OFL, 로컬 설치).
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

FONT = "Pretendard, 'Noto Sans KR', 'Apple SD Gothic Neo', sans-serif"

THEMES = {
    "light": {
        "bg": "#ffffff", "ink": "#1f2328", "muted": "#57606a", "lifeline": "#d0d7de",
        "band": "#f6f8fa", "band_ink": "#1f2328",
        "service_fill": "#eef2ff", "service_border": "#4f46e5",
        "client_fill": "#ffffff", "client_border": "#1f2328",
        "external_fill": "#ffffff", "external_border": "#6b7280",
        "store_fill": "#ffffff", "store_border": "#1f2328",
        "activation": "#c7d2fe", "error": "#b91c1c",
    },
    "dark": {
        "bg": "#0d1117", "ink": "#e6edf3", "muted": "#8b949e", "lifeline": "#30363d",
        "band": "#161b22", "band_ink": "#e6edf3",
        "service_fill": "#1e1b4b", "service_border": "#818cf8",
        "client_fill": "#0d1117", "client_border": "#e6edf3",
        "external_fill": "#0d1117", "external_border": "#6b7280",
        "store_fill": "#0d1117", "store_border": "#e6edf3",
        "activation": "#3730a3", "error": "#f87171",
    },
}

LANE_GAP = 230
LEFT = 40
HEAD_TOP = 20
HEAD_H = 64
LINE_H = 20
FONT_SIZE = 14
BAND_H = 34
ROW_PAD = 16


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _text_width(text: str, size: int = FONT_SIZE) -> float:
    """캔버스 크기를 정할 때만 쓰는 대략의 글자 폭. 한글·전각 문자는 라틴 문자의 약 1.7배다."""
    wide = sum(1 for ch in text if ord(ch) >= 0x1100)
    return ((len(text) - wide) * 0.56 + wide * 0.95) * size


class Sequence:
    def __init__(self, spec: dict, theme: str):
        if theme not in THEMES:
            raise SystemExit(f"unknown theme {theme!r}")
        self.c = THEMES[theme]
        self.title = spec.get("title")
        self.lanes = spec["lanes"]
        self.rows = spec["rows"]
        ids = [lane["id"] for lane in self.lanes]
        if len(set(ids)) != len(ids):
            raise SystemExit("lane ids must be unique")
        self.x = {lane["id"]: LEFT + 110 + i * LANE_GAP for i, lane in enumerate(self.lanes)}
        for row in self.rows:
            for key in ("from", "to"):
                if key in row and row[key] not in self.x:
                    raise SystemExit(f"row refers to unknown lane {row[key]!r}")

    # ------------------------------------------------------------------ layout
    def _layout(self):
        y = HEAD_TOP + HEAD_H + 30 + (34 if self.title else 0)
        placed = []
        right = max(self.x.values()) + 140
        for row in self.rows:
            kind = row["kind"]
            if kind == "band":
                placed.append((row, y))
                y += BAND_H + 10
                continue
            if kind == "note":
                lines = [row["text"]]
                placed.append((row, y + LINE_H))
                y += LINE_H + 8
                right = max(right, LEFT + 110 + _text_width(row["text"]))
                continue
            lines = row.get("lines", [])
            height = len(lines) * LINE_H + ROW_PAD + (14 if kind == "self" else 0)
            anchor = y + len(lines) * LINE_H + 6
            placed.append((row, anchor))
            y += height
            start = self.x[row["from"]] + (40 if kind == "self" else 0)
            if kind != "self":
                start = min(self.x[row["from"]], self.x[row["to"]]) + 14
            right = max(right, start + max((_text_width(s) for s in lines), default=0) + 20)
        return placed, y + 30, right + LEFT

    def _activations(self, placed):
        """활성 막대: 레인으로 들어간 call이 막대를 열고 그 레인의 return·error가 닫는다."""
        stacks: dict[str, list[float]] = {}
        bars = []
        for row, y in placed:
            if row["kind"] == "call":
                stacks.setdefault(row["to"], []).append(y)
            elif row["kind"] in ("return", "error"):
                stack = stacks.get(row["from"])
                if stack:
                    bars.append((row["from"], stack.pop(), y))
        return bars

    # ------------------------------------------------------------------ drawing
    def _label(self, x, y, text, *, size=FONT_SIZE, weight="normal", fill=None, anchor="start", halo=True):
        fill = fill or self.c["ink"]
        halo_attr = (f' stroke="{self.c["bg"]}" stroke-width="4" stroke-linejoin="round" paint-order="stroke"'
                     if halo else "")
        return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" '
                f'fill="{fill}" text-anchor="{anchor}"{halo_attr}>{_esc(text)}</text>')

    def _lane_head(self, lane):
        c, x = self.c, self.x[lane["id"]]
        kind = lane.get("kind", "service")
        fill, border = c[f"{kind}_fill"], c[f"{kind}_border"]
        dash = ' stroke-dasharray="6 4"' if kind == "external" else ""
        out = [f'<rect x="{x - 100:.1f}" y="{HEAD_TOP + self._title_h()}" width="200" height="{HEAD_H}" rx="8" '
               f'fill="{fill}" stroke="{border}" stroke-width="1.6"{dash}/>']
        top = HEAD_TOP + self._title_h()
        if kind == "store":
            out.append(f'<ellipse cx="{x - 82:.1f}" cy="{top + 14}" rx="8" ry="3" fill="none" stroke="{border}"/>'
                       f'<path d="M{x - 90:.1f},{top + 14} v12 a8,3 0 0 0 16,0 v-12" fill="none" stroke="{border}"/>')
        out.append(self._label(x, top + 28, lane["name"], size=15, weight="bold", anchor="middle", halo=False))
        if lane.get("sub"):
            out.append(self._label(x, top + 48, lane["sub"], size=12, fill=c["muted"], anchor="middle", halo=False))
        return out

    def _title_h(self):
        return 34 if self.title else 0

    def render(self) -> str:
        c = self.c
        placed, height, width = self._layout()
        out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
               f'viewBox="0 0 {width:.0f} {height:.0f}" role="img">']
        if self.title:
            out.append(f'<title>{_esc(self.title)}</title>')
        out.append('<defs>'
                   f'<marker id="call" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto">'
                   f'<path d="M0,0 L10,5 L0,10 z" fill="{c["ink"]}"/></marker>'
                   f'<marker id="ret" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto">'
                   f'<path d="M0,0 L10,5 L0,10" fill="none" stroke="{c["ink"]}" stroke-width="1.5"/></marker>'
                   f'<marker id="err" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto">'
                   f'<path d="M0,0 L10,5 L0,10" fill="none" stroke="{c["error"]}" stroke-width="1.5"/></marker>'
                   '</defs>')
        out.append(f'<rect width="100%" height="100%" fill="{c["bg"]}"/>')
        if self.title:
            out.append(self._label(LEFT, HEAD_TOP + 18, self.title, size=18, weight="bold", halo=False))
        top = HEAD_TOP + self._title_h() + HEAD_H
        for lane in self.lanes:
            x = self.x[lane["id"]]
            out.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{height - 16:.0f}" '
                       f'stroke="{c["lifeline"]}" stroke-width="1.5" stroke-dasharray="5 5"/>')
        bands = [(row, y) for row, y in placed if row["kind"] == "band"]
        for row, y in bands:
            out.append(f'<rect x="{LEFT - 20}" y="{y}" width="{width - 2 * (LEFT - 20):.0f}" height="{BAND_H}" '
                       f'rx="6" fill="{c["band"]}"/>')
        for lane, y1, y2 in self._activations(placed):
            out.append(f'<rect x="{self.x[lane] - 6:.1f}" y="{y1 - 4:.1f}" width="12" height="{y2 - y1 + 8:.1f}" '
                       f'rx="2" fill="{c["activation"]}" opacity="0.9"/>')
        for row, y in bands:  # 활성 막대 위에 밴드 색 테두리를 둘러 그려 막대가 글자를 가리지 않게 한다
            out.append(f'<text x="{LEFT - 8:.1f}" y="{y + 22:.1f}" font-family="{FONT}" font-size="14" '
                       f'font-weight="bold" fill="{c["band_ink"]}" stroke="{c["band"]}" stroke-width="5" '
                       f'stroke-linejoin="round" paint-order="stroke">{_esc(row["text"])}</text>')
        for lane in self.lanes:
            out.extend(self._lane_head(lane))
        for row, y in placed:
            out.extend(self._row(row, y))
        out.append('</svg>')
        return "\n".join(out) + "\n"

    def _row(self, row, y):
        c, kind = self.c, row["kind"]
        if kind == "band":
            return []
        if kind == "note":
            return [self._label(LEFT + 110, y - 4, row["text"], size=13, fill=c["muted"])]
        lines = row.get("lines", [])
        n = len(lines)
        if kind == "self":
            x = self.x[row["from"]]
            out = [f'<path d="M{x + 8:.1f},{y - n * LINE_H - 2:.1f} h28 v{n * LINE_H + 8:.1f} h-26" fill="none" '
                   f'stroke="{c["ink"]}" stroke-width="1.4" marker-end="url(#call)"/>']
            for j, s in enumerate(lines):
                out.append(self._label(x + 44, y - (n - 1 - j) * LINE_H - 4, s,
                                       weight="bold" if j == 0 else "normal"))
            return out
        x1, x2 = self.x[row["from"]], self.x[row["to"]]
        d = 1 if x2 > x1 else -1
        xa, xb = x1 + d * 7, x2 - d * 7
        stroke = c["error"] if kind == "error" else c["ink"]
        marker = {"call": "call", "return": "ret", "error": "err"}[kind]
        dash = ' stroke-dasharray="7 5"' if kind in ("return", "error") else ""
        out = [f'<line x1="{xa:.1f}" y1="{y:.1f}" x2="{xb:.1f}" y2="{y:.1f}" stroke="{stroke}" stroke-width="1.8"'
               f'{dash} marker-end="url(#{marker})"/>']
        tx = min(x1, x2) + 14
        for j, s in enumerate(lines):
            out.append(self._label(tx, y - 8 - (n - 1 - j) * LINE_H, s,
                                   weight="bold" if j == 0 and kind == "call" else "normal",
                                   fill=c["error"] if kind == "error" else None))
        return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("spec", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--theme", choices=sorted(THEMES), default="light")
    args = parser.parse_args(argv)
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    args.out.write_text(Sequence(spec, args.theme).render(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
