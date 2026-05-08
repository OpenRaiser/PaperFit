#!/usr/bin/env python3
"""
CV-based Visual Defect Detector

基于 OpenCV 的自动化视觉缺陷检测，辅助 layout-detective-agent 进行视觉验收。

功能：
1. 留白检测 - 自动计算页面留白比例
2. 溢出检测 - 检测内容是否溢出页面边界
3. 对齐检测 - 检测双栏高度差、表格对齐等
4. 浮动体检测 - 识别图表位置与引用距离

依赖:
    - opencv-python
    - numpy

用法:
    python cv_detector.py <page_image> [--defect-type TYPE] [--threshold VALUE]

示例:
    python cv_detector.py data/pages/page_001.png --defect-type whitespace
    python cv_detector.py data/pages/page_004.png --defect-type overflow
"""

import sys
import json
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field

try:
    import cv2
    import numpy as np
except ImportError as e:
    print(f"Error: Missing required library. {e}")
    print("Install with: pip install opencv-python numpy")
    sys.exit(1)

try:
    import yaml
except ImportError:
    yaml = None


DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "layout_rules.yaml"
FALLBACK_DETECTION_DEFAULTS = {
    "content_binary_threshold": 240,
    "float_kernel_size": 15,
    "float_min_width_ratio": 0.30,
    "float_min_height_px": 150,
    "float_max_height_ratio": 0.80,
    "float_min_density": 0.10,
    "text_band_density_factor": 0.55,
    "text_band_min_rows": 12,
    "caption_band_min_height_px": 12,
    "caption_band_max_height_px": 80,
    "caption_band_min_width_ratio": 0.20,
    "caption_band_max_width_ratio": 0.85,
    "whitespace_pixel_threshold": 240,
    "trailing_whitespace_threshold": 0.20,
    "trailing_whitespace_major_ratio": 0.30,
    "bottom_region_start_ratio": 0.70,
    "bottom_whitespace_threshold": 0.15,
    "overflow_margin_px": 20,
    "overflow_dark_pixel_threshold": 50,
    "overflow_min_pixels": 100,
    "column_binarize_threshold": 200,
    "column_imbalance_threshold": 0.10,
    "column_imbalance_major_threshold": 0.15,
    "float_clustering_min_distance_px": 100,
    "density_shift_threshold": 0.18,
    "density_shift_major_delta": 0.08,
    "float_dominated_threshold": 0.65,
    "short_line_binarize_threshold": 200,
    "short_line_projection_threshold": 10,
    "short_line_width_ratio": 0.30,
}


def load_detection_defaults(rules_path: Optional[Path] = None) -> Dict[str, Any]:
    defaults = dict(FALLBACK_DETECTION_DEFAULTS)
    path = rules_path or DEFAULT_RULES_PATH
    if yaml is None or not path.is_file():
        return defaults

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    whitespace = data.get("whitespace") or {}
    defaults["trailing_whitespace_threshold"] = float(
        whitespace.get("trailing_whitespace_max_ratio", defaults["trailing_whitespace_threshold"])
    )

    detector_rules = data.get("cv_detector") or {}
    for key, value in detector_rules.items():
        if key not in defaults:
            continue
        if isinstance(defaults[key], int):
            defaults[key] = int(value)
        else:
            defaults[key] = float(value)
    return defaults


# ============================================================
# 检测结果定义
# ============================================================

@dataclass
class DefectDetection:
    """缺陷检测结果"""
    defect_id: str
    category: str  # A/B/C/D/E
    severity: str  # minor/major/critical
    page: int
    confidence: float
    bbox: Optional[Tuple[int, int, int, int]] = None  # (x1, y1, x2, y2)
    description: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)


# ============================================================
# 核心检测器
# ============================================================

class CVDefectDetector:
    """基于 OpenCV 的缺陷检测器"""

    def __init__(self, image_path: str, page_number: int = 0, rules_path: Optional[Path] = None):
        self.image_path = Path(image_path)
        self.page_number = page_number
        self.image: Optional[np.ndarray] = None
        self.gray: Optional[np.ndarray] = None
        self.detections: List[DefectDetection] = []
        self.thresholds = load_detection_defaults(rules_path)

        # 页面尺寸（英寸）- A4 默认
        self.page_width_inch = 8.27
        self.page_height_inch = 11.69
        self.dpi = 220  # 与 render_pages.py 保持一致

    def _content_binary(self, threshold: Optional[int] = None) -> np.ndarray:
        if self.gray is None and not self.load_image():
            return np.zeros((1, 1), dtype=np.uint8)
        threshold = int(
            threshold if threshold is not None else self.thresholds["content_binary_threshold"]
        )
        _, binary = cv2.threshold(self.gray, threshold, 255, cv2.THRESH_BINARY_INV)
        return binary

    def _find_float_bboxes(self, binary: np.ndarray) -> List[Tuple[int, int, int, int]]:
        h, w = binary.shape
        kernel_size = int(self.thresholds["float_kernel_size"])
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
        closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        float_bboxes = []
        min_float_width = w * float(self.thresholds["float_min_width_ratio"])
        min_float_height = int(self.thresholds["float_min_height_px"])
        max_float_height = h * float(self.thresholds["float_max_height_ratio"])
        min_float_density = float(self.thresholds["float_min_density"])
        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            if cw > min_float_width and ch > min_float_height and ch < max_float_height:
                roi = closed[y:y + ch, x:x + cw]
                density = np.sum(roi > 0) / float(max(cw * ch, 1))
                if density > min_float_density:
                    float_bboxes.append((x, y, cw, ch))
        float_bboxes.sort(key=lambda b: b[1])
        return float_bboxes

    def summarize_page_structure(self) -> Dict[str, Any]:
        if self.gray is None and not self.load_image():
            return {}

        binary = self._content_binary()
        h, w = binary.shape
        row_density = np.mean(binary > 0, axis=1)
        thirds = np.array_split(row_density, 3)
        band_means = [float(np.mean(band)) if len(band) else 0.0 for band in thirds]
        density_shift = max(band_means) - min(band_means) if band_means else 0.0

        float_bboxes = self._find_float_bboxes(binary)
        float_area = sum(cw * ch for _, _, cw, ch in float_bboxes)
        content_area = int(np.sum(binary > 0))
        page_area = int(h * w)

        return {
            "page_width": int(w),
            "page_height": int(h),
            "content_area_ratio": float(round(content_area / float(max(page_area, 1)), 4)),
            "float_bbox_count": len(float_bboxes),
            "float_area_ratio": float(round(float_area / float(max(page_area, 1)), 4)),
            "band_density_top": float(round(band_means[0], 4)) if len(band_means) > 0 else 0.0,
            "band_density_mid": float(round(band_means[1], 4)) if len(band_means) > 1 else 0.0,
            "band_density_bottom": float(round(band_means[2], 4)) if len(band_means) > 2 else 0.0,
            "density_shift_ratio": float(round(density_shift, 4)),
        }

    def extract_object_blocks(self) -> List[Dict[str, Any]]:
        if self.gray is None and not self.load_image():
            return []

        binary = self._content_binary()
        h, w = binary.shape
        float_bboxes = self._find_float_bboxes(binary)
        blocks: List[Dict[str, Any]] = []

        for x, y, bw, bh in float_bboxes:
            aspect_ratio = float(bw / float(max(bh, 1)))
            area_ratio = float(round((bw * bh) / float(max(h * w, 1)), 4))
            kind = "figure_like" if aspect_ratio >= 1.2 else "table_like"
            blocks.append(
                {
                    "kind": kind,
                    "bbox": [int(x), int(y), int(x + bw), int(y + bh)],
                    "area_ratio": area_ratio,
                    "aspect_ratio": float(round(aspect_ratio, 4)),
                }
            )

        # Extract coarse text bands by scanning row density outside float-heavy zones.
        row_density = np.mean(binary > 0, axis=1)
        threshold = max(0.01, float(np.mean(row_density) * float(self.thresholds["text_band_density_factor"])))
        min_rows = int(self.thresholds["text_band_min_rows"])
        in_band = False
        start = 0
        bands: List[Tuple[int, int]] = []
        for idx, val in enumerate(row_density):
            if val > threshold and not in_band:
                in_band = True
                start = idx
            elif val <= threshold and in_band:
                in_band = False
                if idx - start >= min_rows:
                    bands.append((start, idx))
        if in_band and len(row_density) - start >= min_rows:
            bands.append((start, len(row_density)))

        for y0, y1 in bands:
            band_h = y1 - y0
            band_width = int(np.sum(np.any(binary[y0:y1, :] > 0, axis=0)))
            width_ratio = float(band_width / float(max(w, 1)))
            kind = (
                "caption_like"
                if (
                    int(self.thresholds["caption_band_min_height_px"]) <= band_h <= int(self.thresholds["caption_band_max_height_px"])
                    and float(self.thresholds["caption_band_min_width_ratio"]) <= width_ratio <= float(self.thresholds["caption_band_max_width_ratio"])
                )
                else "text_band"
            )
            blocks.append(
                {
                    "kind": kind,
                    "bbox": [0, int(y0), int(w), int(y1)],
                    "area_ratio": float(round((w * (y1 - y0)) / float(max(h * w, 1)), 4)),
                    "width_ratio": float(round(width_ratio, 4)),
                }
            )

        blocks.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
        return blocks

    def load_image(self) -> bool:
        """加载并预处理图像"""
        if not self.image_path.exists():
            return False

        self.image = cv2.imread(str(self.image_path))
        if self.image is None:
            return False

        self.gray = cv2.cvtColor(self.image, cv2.COLOR_BGR2GRAY)
        return True

    def detect_whitespace(self, threshold: Optional[float] = None) -> List[DefectDetection]:
        """
        检测页面留白比例

        Args:
            threshold: 留白比例阈值，超过则报告

        Returns:
            检测结果列表
        """
        if self.gray is None and not self.load_image():
            return []

        # A2 is trailing whitespace, not the full-page white-pixel ratio. In
        # academic PDFs most pixels are naturally white because of margins and
        # line spacing; using whole-page white pixels makes normal pages look
        # like 80-95% whitespace and blocks template migration falsely.
        pixel_threshold = int(self.thresholds["whitespace_pixel_threshold"])
        threshold = float(
            threshold if threshold is not None else self.thresholds["trailing_whitespace_threshold"]
        )
        _, binary = cv2.threshold(self.gray, pixel_threshold, 255, cv2.THRESH_BINARY_INV)
        content_rows = np.where(np.any(binary > 0, axis=1))[0]
        if len(content_rows) == 0:
            content_bottom = 0
            trailing_pixels = self.gray.size
        else:
            content_bottom = int(content_rows.max())
            trailing_pixels = int(max(0, self.gray.shape[0] - content_bottom - 1) * self.gray.shape[1])
        total_pixels = self.gray.size
        whitespace_ratio = trailing_pixels / float(max(total_pixels, 1))

        detection = DefectDetection(
            defect_id="A2-trailing-whitespace",
            category="A",
            severity="minor" if whitespace_ratio < max(0.45, float(self.thresholds["trailing_whitespace_major_ratio"])) else "major",
            page=self.page_number,
            confidence=1.0,
            description=f"页面留白比例：{whitespace_ratio:.2%}",
            metrics={
                "whitespace_ratio": whitespace_ratio,
                "trailing_pixels": int(trailing_pixels),
                "total_pixels": int(total_pixels),
                "content_bottom_px": int(content_bottom),
                "threshold": threshold,
            }
        )

        if whitespace_ratio > threshold:
            self.detections.append(detection)

        return [detection] if whitespace_ratio > threshold else []

    def detect_trailing_whitespace_bottom(
        self,
        threshold: Optional[float] = None
    ) -> List[DefectDetection]:
        """
        检测页面底部留白（末页留白专用）

        分析页面底部 30% 区域的留白比例
        """
        if self.gray is None and not self.load_image():
            return []

        h, w = self.gray.shape
        start_ratio = float(self.thresholds["bottom_region_start_ratio"])
        threshold = float(
            threshold if threshold is not None else self.thresholds["bottom_whitespace_threshold"]
        )
        pixel_threshold = int(self.thresholds["whitespace_pixel_threshold"])
        bottom_region = self.gray[int(h * start_ratio):, :]

        white_pixels = np.sum(bottom_region > pixel_threshold)
        total_pixels = bottom_region.size
        bottom_whitespace_ratio = white_pixels / total_pixels

        detection = DefectDetection(
            defect_id="A2-bottom-whitespace",
            category="A",
            severity="minor",
            page=self.page_number,
            confidence=1.0,
            description=f"页面底部留白比例：{bottom_whitespace_ratio:.2%}",
            metrics={
                "bottom_whitespace_ratio": bottom_whitespace_ratio,
                "region_start_ratio": start_ratio,
            }
        )

        if bottom_whitespace_ratio > threshold:
            self.detections.append(detection)
            return [detection]
        return []

    def detect_overflow(
        self,
        margin_bbox: Optional[Tuple[int, int, int, int]] = None
    ) -> List[DefectDetection]:
        """
        检测内容是否溢出页面边界

        使用边缘检测识别靠近页面边缘的文本/图形
        """
        if self.image is None and not self.load_image():
            return []

        h, w, _ = self.image.shape

        # 默认边距：页面边缘向内 20 像素
        if margin_bbox is None:
            margin = int(self.thresholds["overflow_margin_px"])
            margin_bbox = (margin, margin, w - margin, h - margin)
        dark_threshold = int(self.thresholds["overflow_dark_pixel_threshold"])
        min_pixels = int(self.thresholds["overflow_min_pixels"])

        # 检测超出边界的黑色像素
        # 上边缘
        top_overflow = np.sum(self.image[:margin_bbox[1], :, :] < dark_threshold)
        # 下边缘
        bottom_overflow = np.sum(self.image[margin_bbox[3]:, :, :] < dark_threshold)
        # 左边缘
        left_overflow = np.sum(self.image[:, :margin_bbox[0], :] < dark_threshold)
        # 右边缘
        right_overflow = np.sum(self.image[:, margin_bbox[2]:, :] < dark_threshold)

        detections = []

        if right_overflow > min_pixels:
            detections.append(DefectDetection(
                defect_id="D1-overfull-hbox",
                category="D",
                severity="major",
                page=self.page_number,
                confidence=0.8,
                bbox=(margin_bbox[2], 0, w, h),
                description=f"右侧溢出检测：{right_overflow} 像素",
                metrics={"right_overflow_pixels": int(right_overflow)}
            ))

        if bottom_overflow > min_pixels:
            detections.append(DefectDetection(
                defect_id="D1-bottom-overflow",
                category="D",
                severity="major",
                page=self.page_number,
                confidence=0.8,
                bbox=(0, margin_bbox[3], w, h),
                description=f"底部溢出检测：{bottom_overflow} 像素",
                metrics={"bottom_overflow_pixels": int(bottom_overflow)}
            ))

        self.detections.extend(detections)
        return detections

    def detect_double_column_imbalance(
        self,
        column_boundary: Optional[int] = None
    ) -> List[DefectDetection]:
        """
        检测双栏页面高度不平衡

        计算左右两栏的内容高度差
        """
        if self.gray is None and not self.load_image():
            return []

        h, w = self.gray.shape

        # 默认中线位置
        if column_boundary is None:
            column_boundary = w // 2

        # 二值化，识别内容区域
        _, binary = cv2.threshold(
            self.gray,
            int(self.thresholds["column_binarize_threshold"]),
            255,
            cv2.THRESH_BINARY_INV,
        )

        # 左栏内容
        left_col = binary[:, :column_boundary]
        # 右栏内容
        right_col = binary[:, column_boundary:]

        # 计算每栏的内容高度（有内容的行数）
        left_content_rows = np.any(left_col > 0, axis=1)
        right_content_rows = np.any(right_col > 0, axis=1)

        left_height = np.sum(left_content_rows)
        right_height = np.sum(right_content_rows)

        # 计算高度差比例
        max_height = max(left_height, right_height)
        if max_height > 0:
            height_diff_ratio = abs(left_height - right_height) / max_height
        else:
            height_diff_ratio = 0

        detection = DefectDetection(
            defect_id="A4-column-imbalance",
            category="A",
            severity="minor" if height_diff_ratio < max(0.45, float(self.thresholds["column_imbalance_major_threshold"])) else "major",
            page=self.page_number,
            confidence=0.9,
            description=f"双栏高度差：{height_diff_ratio:.2%} (左={left_height}px, 右={right_height}px)",
            metrics={
                "left_column_height": int(left_height),
                "right_column_height": int(right_height),
                "height_diff_ratio": height_diff_ratio,
            }
        )

        if height_diff_ratio > float(self.thresholds["column_imbalance_threshold"]):
            self.detections.append(detection)
            return [detection]
        return []

    def detect_float_clustering(
        self,
        min_distance: Optional[int] = None
    ) -> List[DefectDetection]:
        """
        检测浮动体（图/表）堆叠

        识别连续出现且间距过小的浮动体

        使用内容密度分析：浮动体通常是矩形块，内容密度均匀且高于周围空白
        """
        if self.gray is None and not self.load_image():
            return []
        min_distance = int(
            min_distance if min_distance is not None else self.thresholds["float_clustering_min_distance_px"]
        )

        binary = self._content_binary()
        float_bboxes = self._find_float_bboxes(binary)

        # 按 Y 坐标排序
        float_bboxes.sort(key=lambda b: b[1])

        # 检测堆叠（垂直距离过小）
        clustered_detections = []
        for i in range(len(float_bboxes) - 1):
            curr_bbox = float_bboxes[i]
            next_bbox = float_bboxes[i + 1]

            # 计算垂直间距
            curr_bottom = curr_bbox[1] + curr_bbox[3]
            next_top = next_bbox[1]
            vertical_gap = next_top - curr_bottom

            # 检查 X 方向是否有重叠（确保是同一栏的浮动体）
            x_overlap = (
                max(curr_bbox[0], next_bbox[0]) <
                min(curr_bbox[0] + curr_bbox[2], next_bbox[0] + next_bbox[2])
            )

            if 0 <= vertical_gap < min_distance and x_overlap:
                clustered_detections.append(DefectDetection(
                    defect_id="B3-float-clustering",
                    category="B",
                    severity="minor",
                    page=self.page_number,
                    confidence=0.85,
                    bbox=(curr_bbox[0], curr_bbox[1],
                          next_bbox[0] + next_bbox[2], next_bbox[1] + next_bbox[3]),
                    description=f"浮动体堆叠：垂直间距 {vertical_gap}px < {min_distance}px",
                    metrics={
                        "vertical_gap": int(vertical_gap),
                        "float_count": 2,
                    }
                ))

        self.detections.extend(clustered_detections)
        return clustered_detections

    def detect_density_shift(self, threshold: Optional[float] = None) -> List[DefectDetection]:
        if self.gray is None and not self.load_image():
            return []

        threshold = float(
            threshold if threshold is not None else self.thresholds["density_shift_threshold"]
        )
        summary = self.summarize_page_structure()
        shift = float(summary.get("density_shift_ratio") or 0.0)
        if shift <= threshold:
            return []

        h, w = self.gray.shape
        detection = DefectDetection(
            defect_id="A6-density-shift",
            category="A",
            severity="minor" if shift <= max(0.50, threshold + float(self.thresholds["density_shift_major_delta"])) else "major",
            page=self.page_number,
            confidence=0.78,
            bbox=(0, 0, w, h),
            description=f"页内密度突变：band density shift {shift:.2f} > {threshold:.2f}",
            metrics=summary,
        )
        self.detections.append(detection)
        return [detection]

    def detect_float_dominated_page(self, threshold: Optional[float] = None) -> List[DefectDetection]:
        if self.gray is None and not self.load_image():
            return []

        threshold = float(
            threshold if threshold is not None else self.thresholds["float_dominated_threshold"]
        )
        summary = self.summarize_page_structure()
        float_ratio = float(summary.get("float_area_ratio") or 0.0)
        float_count = int(summary.get("float_bbox_count") or 0)
        if float_ratio <= threshold or float_count == 0:
            return []

        h, w = self.gray.shape
        detection = DefectDetection(
            defect_id="B5-float-dominated-page",
            category="B",
            severity="major",
            page=self.page_number,
            confidence=0.82,
            bbox=(0, 0, w, h),
            description=f"浮动体页正文支撑不足：float area ratio {float_ratio:.2f} > {threshold:.2f}",
            metrics=summary,
        )
        self.detections.append(detection)
        return [detection]

    def detect_lone_short_line(
        self,
        line_threshold: Optional[int] = None
    ) -> List[DefectDetection]:
        """
        检测孤行寡行（段落末尾短行）

        分析文本行长度，识别异常短的最后行
        """
        if self.gray is None and not self.load_image():
            return []
        line_threshold = int(
            line_threshold if line_threshold is not None else self.thresholds["short_line_projection_threshold"]
        )
        short_line_width_ratio = float(self.thresholds["short_line_width_ratio"])

        # 二值化
        _, binary = cv2.threshold(
            self.gray,
            int(self.thresholds["short_line_binarize_threshold"]),
            255,
            cv2.THRESH_BINARY_INV,
        )

        # 水平投影，识别文本行
        h_proj = np.sum(binary > 0, axis=1)

        # 识别文本行区间
        lines = []
        in_line = False
        line_start = 0

        for i, proj in enumerate(h_proj):
            if proj > line_threshold and not in_line:
                in_line = True
                line_start = i
            elif proj <= line_threshold and in_line:
                in_line = False
                lines.append((line_start, i))

        # 分析每行宽度（简化：使用投影和作为代理）
        short_lines = []
        for i, (start, end) in enumerate(lines):
            row = binary[start:end, :]
            line_width = np.sum(np.any(row > 0, axis=0))
            page_width = binary.shape[1]

            if line_width < page_width * short_line_width_ratio:
                short_lines.append((i, line_width, page_width))

        # 如果段落末尾有短行，报告孤行
        if short_lines:
            # 检查是否连续出现（孤行特征）
            for idx, width, page_w in short_lines[-2:]:  # 检查最后两行
                if width < page_w * short_line_width_ratio:
                    detection = DefectDetection(
                        defect_id="A1-widow-orphan",
                        category="A",
                        severity="minor",
                        page=self.page_number,
                        confidence=0.75,
                        description=f"检测到短行：宽度 {width:.0f}px / 页面 {page_w:.0f}px = {width/page_w:.0%}",
                        metrics={
                            "line_width": int(width),
                            "page_width": int(page_w),
                            "ratio": width / page_w,
                            "width_ratio_threshold": short_line_width_ratio,
                            "projection_threshold": line_threshold,
                        }
                    )
                    self.detections.append(detection)
                    return [detection]

        return []

    def run_all_detections(self) -> List[DefectDetection]:
        """运行所有检测"""
        if not self.load_image():
            return []

        self.detections = []

        # 执行所有检测
        self.detect_whitespace()
        self.detect_trailing_whitespace_bottom()
        self.detect_overflow()
        self.detect_float_clustering()
        self.detect_float_dominated_page()
        self.detect_lone_short_line()
        self.detect_double_column_imbalance()
        self.detect_density_shift()

        return self.detections

    def to_report(self) -> Dict[str, Any]:
        """生成检测报告"""
        return {
            "status": "success" if self.detections else "clean",
            "image_path": str(self.image_path),
            "page_number": self.page_number,
            "detection_count": len(self.detections),
            "page_metrics": self.summarize_page_structure() if self.gray is not None else {},
            "object_blocks": self.extract_object_blocks() if self.gray is not None else [],
            "detections": [
                {
                    "defect_id": d.defect_id,
                    "category": d.category,
                    "severity": d.severity,
                    "page": d.page,
                    "confidence": d.confidence,
                    "description": d.description,
                    "metrics": d.metrics,
                    "bbox": list(d.bbox) if d.bbox else None,
                }
                for d in self.detections
            ],
        }


# ============================================================
# 批量检测器
# ============================================================

class BatchCVDetector:
    """批量 CV 检测器"""

    def __init__(self, pages_dir: str, rules_path: Optional[Path] = None):
        self.pages_dir = Path(pages_dir)
        self.rules_path = rules_path
        self.results: List[Dict] = []

    def run_batch(self) -> Dict[str, Any]:
        """批量检测所有页面"""
        page_files = sorted(self.pages_dir.glob("page_*.png"))

        for page_file in page_files:
            # 从文件名解析页码
            page_num = int(page_file.stem.split("_")[1])

            detector = CVDefectDetector(str(page_file), page_number=page_num, rules_path=self.rules_path)
            detector.run_all_detections()
            self.results.append(detector.to_report())

        # 汇总统计
        total_detections = sum(r["detection_count"] for r in self.results)
        category_counts = {}

        for r in self.results:
            for d in r["detections"]:
                cat = d["category"]
                category_counts[cat] = category_counts.get(cat, 0) + 1

        return {
            "status": "completed",
            "pages_analyzed": len(page_files),
            "total_detections": total_detections,
            "category_breakdown": category_counts,
            "page_results": self.results,
        }


# ============================================================
# 命令行接口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="CV-based visual defect detector for LaTeX PDF pages"
    )
    parser.add_argument("page_image", help="Path to page image (PNG)")
    parser.add_argument(
        "--defect-type",
        "-t",
        choices=["whitespace", "overflow", "clustering", "short-line", "column-imbalance", "all"],
        default="all",
        help="Type of defect to detect"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.20,
        help="Detection threshold"
    )
    parser.add_argument(
        "--batch-dir",
        help="Directory containing page images for batch processing"
    )
    parser.add_argument(
        "--json",
        "-j",
        action="store_true",
        help="Output JSON format only"
    )
    parser.add_argument(
        "--layout-rules",
        help="Path to layout_rules.yaml"
    )

    args = parser.parse_args()

    # 批量模式
    if args.batch_dir:
        batch_detector = BatchCVDetector(args.batch_dir, rules_path=Path(args.layout_rules) if args.layout_rules else None)
        report = batch_detector.run_batch()
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    # 单页模式
    detector = CVDefectDetector(
        args.page_image,
        rules_path=Path(args.layout_rules) if args.layout_rules else None,
    )

    if not detector.load_image():
        print(f"Error: Failed to load image: {args.page_image}")
        sys.exit(1)

    detections = []

    if args.defect_type == "whitespace":
        detections = detector.detect_whitespace(args.threshold)
    elif args.defect_type == "overflow":
        detections = detector.detect_overflow()
    elif args.defect_type == "clustering":
        detections = detector.detect_float_clustering()
    elif args.defect_type == "short-line":
        detections = detector.detect_lone_short_line()
    elif args.defect_type == "column-imbalance":
        detections = detector.detect_double_column_imbalance()
    else:
        detections = detector.run_all_detections()

    report = detector.to_report()

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"\nCV Defect Detection Report")
        print("=" * 50)
        print(f"Image: {args.page_image}")
        print(f"Page: {detector.page_number}")
        print(f"Detections: {len(detections)}")
        print()

        if detections:
            for d in detections:
                print(f"  [{d.category}] {d.defect_id}")
                print(f"    Severity: {d.severity}")
                print(f"    Confidence: {d.confidence:.0%}")
                print(f"    {d.description}")
                print()
        else:
            print("  No defects detected.")

    sys.exit(0 if report["status"] == "clean" else 1)


if __name__ == "__main__":
    main()
