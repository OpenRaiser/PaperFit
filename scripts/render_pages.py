#!/usr/bin/env python3
"""
PDF 页面渲染脚本 - 安全版本
将 PDF 渲染为高 DPI 图片，同时确保不会污染终端输出
"""

import sys
import os
import io
import re
import contextlib
from pathlib import Path

# =============================================================================
# 终端安全保护 - 在导入其他库之前先设置
# =============================================================================

class SafeOutput:
    """安全的输出包装器，过滤所有可能干扰终端的字符"""

    # ANSI 转义序列模式
    ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    # 其他控制字符（保留基本的换行和制表符）
    CONTROL_CHARS = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]')

    def __init__(self, stream):
        self.stream = stream
        self.buffer = ""

    def write(self, text):
        # 清理文本中的有害字符
        cleaned = self.ANSI_ESCAPE.sub('', text)
        cleaned = self.CONTROL_CHARS.sub('', cleaned)
        self.stream.write(cleaned)
        self.stream.flush()

    def flush(self):
        self.stream.flush()

    def isatty(self):
        return False  # 伪装成非终端，防止库输出颜色代码

# 保存原始 stderr
_original_stderr = sys.stderr

# 用安全包装器替换 stderr
sys.stderr = SafeOutput(_original_stderr)

# 同时捕获 stdout 以防万一
_original_stdout = sys.stdout
sys.stdout = SafeOutput(_original_stdout)

# =============================================================================
# 现在可以安全地导入其他库
# =============================================================================

import argparse
import json
from typing import List, Optional

# 尝试导入 pdf2image，如果失败则提供有用的错误信息
try:
    from pdf2image import convert_from_path
    from pdf2image.exceptions import (
        PDFInfoNotInstalledError,
        PDFPageCountError,
        PDFSyntaxError
    )
except ImportError as e:
    # 恢复原始 stderr 以便显示错误
    sys.stderr = _original_stderr
    print(f"错误: 无法导入 pdf2image: {e}", file=sys.stderr)
    print("请安装: pip install pdf2image", file=sys.stderr)
    print("同时需要安装 poppler (macOS: brew install poppler)", file=sys.stderr)
    sys.exit(1)


def setup_logging(log_dir: Optional[Path] = None) -> Path:
    """设置日志记录，所有输出重定向到文件而非终端"""
    if log_dir is None:
        log_dir = Path("data/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "render_pages.log"
    return log_file


class TeeOutput:
    """同时将输出写入文件和终端（可选）"""
    def __init__(self, log_file: Path, silent: bool = True):
        self.log_file = log_file
        self.silent = silent
        self.log_handle = open(log_file, 'a', encoding='utf-8')

    def write(self, text: str):
        # 总是写入日志文件
        self.log_handle.write(text)
        self.log_handle.flush()
        # 只有在非静默模式下才输出到终端
        if not self.silent:
            _original_stdout.write(text)

    def flush(self):
        self.log_handle.flush()
        if not self.silent:
            _original_stdout.flush()

    def close(self):
        self.log_handle.close()


def sanitize_filename(filename: str) -> str:
    """清理文件名，移除有害字符"""
    # 移除非字母数字字符（保留基本标点）
    return re.sub(r'[^\w\-.]', '_', filename)


def target_page_filename(page_index: int, fmt: str) -> str:
    """生成稳定页图文件名，供后续检测链路消费。"""
    normalized_fmt = str(fmt or "png").lstrip(".")
    return f"page_{page_index:03d}.{normalized_fmt}"


def render_pages(
    pdf_path: Path,
    output_dir: Path,
    dpi: int = 150,
    fmt: str = "png",
    max_pages: Optional[int] = None,
    silent: bool = True,
    log_file: Optional[Path] = None
) -> List[Path]:
    """
    将 PDF 渲染为图片

    Args:
        pdf_path: PDF 文件路径
        output_dir: 输出目录
        dpi: 分辨率
        fmt: 图片格式
        max_pages: 最大渲染页数（None 表示全部）
        silent: 是否静默（不输出到终端）
        log_file: 日志文件路径

    Returns:
        生成的图片路径列表
    """
    # 创建输出目录
    output_dir.mkdir(parents=True, exist_ok=True)

    # 设置日志
    if log_file is None:
        log_file = output_dir / "render.log"

    # 重定向输出
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    tee = TeeOutput(log_file, silent=silent)
    sys.stdout = tee
    sys.stderr = tee

    output_paths = []

    try:
        print(f"开始渲染: {pdf_path}")
        print(f"输出目录: {output_dir}")
        print(f"DPI: {dpi}, 格式: {fmt}")

        normalized_fmt = str(fmt or "png").lstrip(".").lower()
        pil_format = {"jpg": "JPEG", "jpeg": "JPEG"}.get(normalized_fmt, normalized_fmt.upper())

        for stale_page in output_dir.glob(f"page_*.{normalized_fmt}"):
            stale_page.unlink()

        # 直接获取 PIL 图像再由 PaperFit 自己落盘。
        # 避免 pdf2image 的 output_folder + paths_only 路径在部分 PDF 上产出空白页图。
        images = convert_from_path(
            pdf_path,
            dpi=dpi,
            fmt=fmt,
            first_page=1,
            last_page=max_pages,
            thread_count=2,
        )

        for page_index, image in enumerate(images, start=1):
            target_path = output_dir / target_page_filename(page_index, normalized_fmt)
            if target_path.exists():
                target_path.unlink()
            image.save(target_path, pil_format)
            output_paths.append(target_path)
            print(f"已生成: {target_path.name}")

        print(f"\n渲染完成: {len(output_paths)} 页")

    except PDFInfoNotInstalledError:
        error_msg = "错误: poppler 未安装。macOS: brew install poppler, Ubuntu: apt-get install poppler-utils"
        print(error_msg)
        raise RuntimeError(error_msg)

    except PDFPageCountError as e:
        error_msg = f"错误: 无法获取 PDF 页数: {e}"
        print(error_msg)
        raise RuntimeError(error_msg)

    except PDFSyntaxError as e:
        error_msg = f"错误: PDF 格式错误: {e}"
        print(error_msg)
        raise RuntimeError(error_msg)

    except Exception as e:
        error_msg = f"渲染失败: {type(e).__name__}: {e}"
        print(error_msg)
        raise RuntimeError(error_msg)

    finally:
        # 恢复原始输出
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        tee.close()

    return output_paths


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="将 PDF 页面渲染为高 DPI 图片",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python render_pages.py input.pdf -o page_images/
  python render_pages.py input.pdf --dpi 200 --fmt png -o output/
        """
    )

    parser.add_argument("pdf", help="输入 PDF 文件路径")
    parser.add_argument("-o", "--output", default="page_images",
                        help="输出目录 (默认: page_images)")
    parser.add_argument("--dpi", type=int, default=150,
                        help="渲染分辨率 DPI (默认: 150)")
    parser.add_argument("--fmt", default="png",
                        help="图片格式 (默认: png)")
    parser.add_argument("--max-pages", type=int, default=None,
                        help="最大渲染页数 (默认: 全部)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="详细输出 (默认: 静默)")
    parser.add_argument("--log", default="data/logs/render_pages.log",
                        help="日志文件路径")

    args = parser.parse_args()

    # 路径处理
    pdf_path = Path(args.pdf)
    output_dir = Path(args.output)
    log_file = Path(args.log)

    # 验证输入
    if not pdf_path.exists():
        # 恢复原始 stderr 以便显示错误
        sys.stderr = _original_stderr
        print(f"错误: PDF 文件不存在: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    # 创建日志目录
    log_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        # 渲染页面
        output_paths = render_pages(
            pdf_path=pdf_path,
            output_dir=output_dir,
            dpi=args.dpi,
            fmt=args.fmt,
            max_pages=args.max_pages,
            silent=not args.verbose,
            log_file=log_file
        )

        # 输出结果（仅 JSON 到 stdout，方便其他程序解析）
        result = {
            "success": True,
            "pages_rendered": len(output_paths),
            "output_dir": str(output_dir.absolute()),
            "output_files": [str(p.absolute()) for p in output_paths],
            "log_file": str(log_file.absolute())
        }

        # 恢复原始 stdout 以便输出 JSON
        sys.stdout = _original_stdout
        print(json.dumps(result, indent=2, ensure_ascii=False))

    except Exception as e:
        # 恢复原始 stderr 以便显示错误
        sys.stderr = _original_stderr
        error_result = {
            "success": False,
            "error": str(e),
            "log_file": str(log_file.absolute())
        }
        print(json.dumps(error_result, indent=2, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
