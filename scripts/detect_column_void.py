#!/usr/bin/env python3
"""
双栏页图「列内竖向空洞」检测（OpenCV + 行向墨迹投影）

用于辅助 A5 类缺陷：在渲染后的 PNG 上按栏分割，统计每行墨迹占比，
找出栏内连续大面积「几乎无墨迹」的竖带，输出 JSON 供 layout-detective 合并。

依赖:
    pip install opencv-python-headless numpy

用法（在论文项目根目录，经 CLI）:
    paperfit run scripts/detect_column_void.py data/pages/page_004.png
    paperfit run scripts/detect_column_void.py data/pages --glob 'page_*.png' --output reports/column_void.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import cv2
    import numpy as np
except ImportError as e:
    print("Error: 需要 OpenCV 与 NumPy。", e, file=sys.stderr)
    print("安装: pip install opencv-python-headless numpy", file=sys.stderr)
    sys.exit(1)

try:
    import yaml
except ImportError:
    yaml = None


DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "layout_rules.yaml"
FALLBACK_DEFAULTS = {
    "min_void_ratio": 0.30,
    "min_void_lines": 8,
    "void_row_thresh": 0.035,
    "smooth_rows": 7,
    "merge_gap_pixels": 12,
}


@dataclass
class VoidSegment:
    y0: int
    y1: int
    height_px: int
    ratio_of_column: float


def load_detection_defaults(rules_path: Optional[Path] = None) -> Dict[str, Any]:
    defaults = dict(FALLBACK_DEFAULTS)
    path = rules_path or DEFAULT_RULES_PATH
    if yaml is None or not path.is_file():
        return defaults

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    intra = data.get("intra_column_void") or {}
    defaults["min_void_ratio"] = float(
        intra.get("min_void_ratio_of_column", defaults["min_void_ratio"])
    )
    defaults["min_void_lines"] = int(
        intra.get("min_void_height_lines", defaults["min_void_lines"])
    )
    return defaults


def _sorted_glob(directory: Path, pattern: str) -> List[Path]:
    paths = sorted(directory.glob(pattern), key=lambda p: p.name.lower())
    return [p for p in paths if p.is_file()]


def page_index_from_name(name: str) -> Optional[int]:
    m = re.search(r"page_(\d+)", name, re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1), 10)


def rel_path_if_under(path: Path, cwd: Path) -> str:
    try:
        return str(path.resolve().relative_to(cwd.resolve()))
    except ValueError:
        return str(path.resolve())


def find_gutter_x(gray: np.ndarray, margin_lo: float = 0.33, margin_hi: float = 0.67) -> int:
    """在页面水平中部寻找最「亮」的竖线区域中心，作为双栏中缝。"""
    h, w = gray.shape[:2]
    x0 = max(1, int(w * margin_lo))
    x1 = min(w - 2, int(w * margin_hi))
    # 每列取 5px 宽的窗口平均亮度，抗噪
    win = max(3, w // 200 | 1)  # odd
    if win % 2 == 0:
        win += 1
    best_x = (x0 + x1) // 2
    best_score = -1.0
    for xc in range(x0, x1):
        x_lo = max(0, xc - win // 2)
        x_hi = min(w, xc + win // 2 + 1)
        score = float(gray[:, x_lo:x_hi].mean())
        if score > best_score:
            best_score = score
            best_x = xc
    return int(best_x)


def column_row_ink_ratio(col_gray: np.ndarray, ink_threshold: int) -> np.ndarray:
    """每行墨迹像素占比 [0,1]。"""
    ink = (col_gray < ink_threshold).astype(np.float32)
    return ink.mean(axis=1)


def smooth_1d(a: np.ndarray, k: int) -> np.ndarray:
    k = max(1, k | 1)
    if k == 1:
        return a
    kernel = np.ones(k, dtype=np.float32) / float(k)
    pad = k // 2
    padded = np.pad(a, (pad, pad), mode="edge")
    out = np.convolve(padded, kernel, mode="valid")
    return out.astype(np.float32)


def find_void_segments(
    row_ink: np.ndarray,
    col_h: int,
    void_row_thresh: float,
    min_void_ratio: float,
    min_gap_px: int,
    merge_gap_px: int,
) -> List[VoidSegment]:
    is_void = row_ink < void_row_thresh
    segments: List[Tuple[int, int]] = []
    i = 0
    n = len(is_void)
    while i < n:
        if not is_void[i]:
            i += 1
            continue
        j = i
        while j < n and is_void[j]:
            j += 1
        if j - i >= min_gap_px:
            segments.append((i, j))
        i = j
    # 合并间隔很小的段
    merged: List[Tuple[int, int]] = []
    for y0, y1 in segments:
        if not merged:
            merged.append((y0, y1))
            continue
        py0, py1 = merged[-1]
        if y0 - py1 <= merge_gap_px:
            merged[-1] = (py0, y1)
        else:
            merged.append((y0, y1))
    out: List[VoidSegment] = []
    for y0, y1 in merged:
        hpx = y1 - y0
        ratio = hpx / float(max(1, col_h))
        if ratio >= min_void_ratio and hpx >= min_gap_px:
            out.append(
                VoidSegment(
                    y0=int(y0),
                    y1=int(y1),
                    height_px=int(hpx),
                    ratio_of_column=float(round(ratio, 4)),
                )
            )
    return out


def ink_threshold_from_image(gray: np.ndarray) -> int:
    """Otsu 二值化取墨迹阈值；失败时用固定值。"""
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    t, _ = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if t <= 0 or t >= 255:
        return 200
    # 墨迹为暗：取略低于 Otsu 分界，使略浅灰字仍算墨迹
    return int(min(220, max(120, t + 15)))


def analyze_page(
    image_path: Path,
    split_x: Optional[int],
    void_row_thresh: float,
    min_void_ratio: float,
    min_void_lines: int,
    smooth_rows: int,
    merge_gap_px: int,
    cwd: Path,
) -> Dict[str, Any]:
    bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if bgr is None:
        return {
            "file": str(image_path.resolve()),
            "page_image": rel_path_if_under(image_path, cwd),
            "page_index": page_index_from_name(image_path.name),
            "error": "无法读取图像",
            "a5_candidates": [],
        }

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    ink_thr = ink_threshold_from_image(gray)

    if split_x is None:
        gx = find_gutter_x(gray)
    else:
        gx = int(np.clip(split_x, w * 0.2, w * 0.8))

    left_gray = gray[:, :gx]
    right_gray = gray[:, gx:]
    page_index = page_index_from_name(image_path.name)
    page_image_rel = rel_path_if_under(image_path, cwd)

    # 估计行高：用梯度粗略估计，用于 min_gap_px
    grad = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    row_act = np.abs(grad).mean(axis=1)
    peaks = np.where(row_act > np.percentile(row_act, 85))[0]
    line_h = 18
    if len(peaks) > 10:
        d = np.diff(np.sort(peaks))
        d = d[d > 3]
        if len(d):
            line_h = int(np.median(d))
    min_gap_px = max(12, int(min_void_lines * line_h * 0.85))

    cols_info: Dict[str, Any] = {}
    candidates: List[Dict[str, Any]] = []

    for name, crop in ("left", left_gray), ("right", right_gray):
        ch, cw = crop.shape[:2]
        row_ink = column_row_ink_ratio(crop, ink_thr)
        row_ink_s = smooth_1d(row_ink, smooth_rows)
        voids = find_void_segments(
            row_ink_s,
            col_h=ch,
            void_row_thresh=void_row_thresh,
            min_void_ratio=min_void_ratio,
            min_gap_px=min_gap_px,
            merge_gap_px=merge_gap_px,
        )
        max_ratio = max((v.ratio_of_column for v in voids), default=0.0)
        cols_info[name] = {
            "width_px": int(cw),
            "height_px": int(ch),
            "void_segments": [asdict(v) for v in voids],
            "max_void_ratio": float(round(max_ratio, 4)),
            "mean_row_ink": float(round(float(row_ink_s.mean()), 4)),
        }
        for v in voids:
            if v.ratio_of_column >= min_void_ratio:
                y0f = round(v.y0 / float(max(1, ch)), 4)
                y1f = round(v.y1 / float(max(1, ch)), 4)
                candidates.append(
                    {
                        "suggested_defect_id": "A5",
                        "column": name,
                        "y0": v.y0,
                        "y1": v.y1,
                        "y0_frac": y0f,
                        "y1_frac": y1f,
                        "void_ratio_of_column": v.ratio_of_column,
                        "confidence": "high" if v.ratio_of_column >= 0.4 else "medium",
                    }
                )

    return {
        "file": str(image_path.resolve()),
        "page_image": page_image_rel,
        "page_index": page_index,
        "width": int(w),
        "height": int(h),
        "split_x": int(gx),
        "ink_threshold": int(ink_thr),
        "estimated_line_height_px": int(line_h),
        "min_gap_px_used": int(min_gap_px),
        "columns": cols_info,
        "a5_candidates": candidates,
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="OpenCV 双栏页图列内竖向空洞检测（行向墨迹投影）"
    )
    p.add_argument(
        "path",
        help="单张 PNG/JPG，或包含页图的目录",
    )
    p.add_argument(
        "--glob",
        default="page_*.png",
        help="当 path 为目录时使用的 glob（默认 page_*.png）",
    )
    p.add_argument("--output", "-o", help="写入 JSON 报告路径")
    p.add_argument(
        "--layout-rules",
        default=None,
        help="layout_rules.yaml 路径；默认读取仓库 config/layout_rules.yaml",
    )
    p.add_argument(
        "--split-x",
        type=int,
        default=None,
        help="强制中缝 x 像素（默认自动检测亮缝）",
    )
    p.add_argument(
        "--min-void-ratio",
        type=float,
        default=None,
        help="空洞高度占该栏高度比例下限（与 layout_rules intra_column_void 对齐）",
    )
    p.add_argument(
        "--min-void-lines",
        type=int,
        default=None,
        help="最少相当于多少行正文高度的空洞（用于换算 min_gap_px）",
    )
    p.add_argument(
        "--void-row-thresh",
        type=float,
        default=None,
        help="平滑后一行墨迹占比低于该值视为「无墨迹」",
    )
    p.add_argument(
        "--smooth-rows",
        type=int,
        default=None,
        help="行序列移动平均窗口（奇数）",
    )
    p.add_argument(
        "--merge-gap-pixels",
        type=int,
        default=None,
        help="两段空洞之间间隔（像素行）小于等于该值则合并为一段",
    )
    args = p.parse_args()
    defaults = load_detection_defaults(
        Path(args.layout_rules).resolve() if args.layout_rules else None
    )
    min_void_ratio = float(
        args.min_void_ratio
        if args.min_void_ratio is not None
        else defaults["min_void_ratio"]
    )
    min_void_lines = int(
        args.min_void_lines
        if args.min_void_lines is not None
        else defaults["min_void_lines"]
    )
    void_row_thresh = float(
        args.void_row_thresh
        if args.void_row_thresh is not None
        else defaults["void_row_thresh"]
    )
    smooth_rows = int(
        args.smooth_rows
        if args.smooth_rows is not None
        else defaults["smooth_rows"]
    ) | 1
    merge_gap_px = max(
        1,
        int(
            args.merge_gap_pixels
            if args.merge_gap_pixels is not None
            else defaults["merge_gap_pixels"]
        ),
    )

    root = Path(args.path)
    if not root.exists():
        print(f"路径不存在: {root}", file=sys.stderr)
        return 1

    if root.is_dir():
        files = _sorted_glob(root, args.glob)
        if not files:
            files = sorted(root.iterdir())
            files = [f for f in files if f.suffix.lower() in (".png", ".jpg", ".jpeg")]
        if not files:
            print(f"目录下无匹配图像: {root} ({args.glob})", file=sys.stderr)
            return 1
    else:
        files = [root]

    cwd = Path.cwd()

    run_params = {
        "path": str(root),
        "glob": args.glob if root.is_dir() else None,
        "layout_rules": str(Path(args.layout_rules).resolve()) if args.layout_rules else str(DEFAULT_RULES_PATH),
        "min_void_ratio": min_void_ratio,
        "min_void_lines": min_void_lines,
        "void_row_thresh": void_row_thresh,
        "smooth_rows": smooth_rows,
        "merge_gap_pixels": merge_gap_px,
        "split_x": args.split_x,
    }

    pages: List[Dict[str, Any]] = []
    for fp in files:
        pages.append(
            analyze_page(
                fp,
                split_x=args.split_x,
                void_row_thresh=void_row_thresh,
                min_void_ratio=min_void_ratio,
                min_void_lines=min_void_lines,
                smooth_rows=smooth_rows,
                merge_gap_px=merge_gap_px,
                cwd=cwd,
            )
        )

    report: Dict[str, Any] = {
        "schema_version": "1.0",
        "script": "detect_column_void",
        "opencv_version": cv2.__version__,
        "page_count": len(pages),
        "run": {
            "cwd": str(cwd.resolve()),
            "argv": sys.argv,
            "params": run_params,
        },
        "pages": pages,
    }

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        outp = Path(args.output)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(text, encoding="utf-8")
        print(f"已写入 {outp.resolve()}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
