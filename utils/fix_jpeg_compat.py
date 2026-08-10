#!/usr/bin/env python3
"""
批量规范化 JPEG，规避部分 Android/模拟器程序的 JPEG 兼容性问题。

处理策略：
1. 重新编码为 8-bit Baseline JPEG；
2. 统一使用常见的 YCbCr 4:2:0 色度采样；
3. 由 Pillow 重新生成标准 JFIF/EXIF 段顺序；
4. 默认只保留方向、GPS 和拍摄日期相关 EXIF，减少异常元数据风险；
5. 小文件通过合法 JPEG COM 段填充到至少 128 KiB，绕过旧版 Luban
   “100 KiB 以下不压缩、直接回写公共 Pictures 原图”导致的权限崩溃；
6. 写入后重新读取并核对 GPS 和日期，核对失败则删除输出文件。

需要：Python 3.9+、Pillow 10+
安装：py -m pip install -U Pillow
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

try:
    from PIL import Image, UnidentifiedImageError
except ImportError:
    print("缺少 Pillow。请运行：py -m pip install -U Pillow", file=sys.stderr)
    raise SystemExit(2)


JPEG_SUFFIXES = {".jpg", ".jpeg"}
DEFAULT_MIN_KIB = 128
JPEG_COMMENT_MAX_PAYLOAD = 65533
PADDING_LABEL = b"CODEX_JPEG_COMPAT_PADDING_V1 "

# TIFF/EXIF IFD 指针
EXIF_IFD_POINTER = 0x8769
GPS_IFD_POINTER = 0x8825

# 0th IFD 中应保留的字段
ORIENTATION = 0x0112
DATETIME = 0x0132

# Exif 子 IFD 中的时间字段
EXIF_DATE_TAGS = {
    0x9003,  # DateTimeOriginal
    0x9004,  # DateTimeDigitized
    0x9010,  # OffsetTime
    0x9011,  # OffsetTimeOriginal
    0x9012,  # OffsetTimeDigitized
    0x9290,  # SubSecTime
    0x9291,  # SubSecTimeOriginal
    0x9292,  # SubSecTimeDigitized
}


class VerificationError(RuntimeError):
    """输出 JPEG 校验失败。"""


def normalized(value: Any) -> Any:
    """把 EXIF 值转为可稳定比较的递归结构。"""
    if isinstance(value, dict):
        return tuple(sorted((int(k), normalized(v)) for k, v in value.items()))
    if isinstance(value, (tuple, list)):
        return tuple(normalized(v) for v in value)
    if isinstance(value, bytes):
        return ("bytes", value.hex())
    # IFDRational 等类型用 numerator/denominator 精确比较。
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        return ("rational", int(value.numerator), int(value.denominator))
    return value


def safe_get_ifd(exif: Image.Exif, pointer: int) -> dict[int, Any]:
    try:
        result = exif.get_ifd(pointer)
    except Exception as exc:
        raise VerificationError(
            f"无法读取 EXIF 子目录 0x{pointer:04X}：{exc}"
        ) from exc
    return dict(result) if result else {}


def critical_snapshot(exif: Image.Exif) -> dict[str, Any]:
    """提取必须原样保留的 GPS 和拍摄时间，用于写入前后核对。"""
    exif_ifd = safe_get_ifd(exif, EXIF_IFD_POINTER)
    gps_ifd = safe_get_ifd(exif, GPS_IFD_POINTER)
    return {
        "DateTime": normalized(exif.get(DATETIME)),
        "ExifDates": normalized(
            {tag: value for tag, value in exif_ifd.items() if tag in EXIF_DATE_TAGS}
        ),
        "GPS": normalized(gps_ifd),
    }


def build_compatible_exif(source: Image.Exif, keep_all: bool) -> Image.Exif:
    """
    重建 EXIF。

    keep_all=False 时仅保留兼容性所需的方向、日期和完整 GPS IFD；
    keep_all=True 时保留源文件的全部 EXIF，同时仍由 Pillow 重新序列化。
    """
    if keep_all:
        # tobytes() 后再 load()，避免把源对象内部的文件偏移直接带到新文件。
        rebuilt = Image.Exif()
        payload = source.tobytes()
        if payload:
            rebuilt.load(payload)
        return rebuilt

    rebuilt = Image.Exif()

    if ORIENTATION in source:
        rebuilt[ORIENTATION] = source[ORIENTATION]
    if DATETIME in source:
        rebuilt[DATETIME] = source[DATETIME]

    exif_ifd = safe_get_ifd(source, EXIF_IFD_POINTER)
    date_ifd = {
        tag: value for tag, value in exif_ifd.items() if tag in EXIF_DATE_TAGS
    }
    if date_ifd:
        rebuilt[EXIF_IFD_POINTER] = date_ifd

    gps_ifd = safe_get_ifd(source, GPS_IFD_POINTER)
    if gps_ifd:
        # 保留 GPS IFD 全部字段，包括南北纬/东西经参考、海拔和测量时间等。
        rebuilt[GPS_IFD_POINTER] = gps_ifd

    return rebuilt


def iter_jpegs(input_path: Path, output_path: Path) -> Iterable[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() not in JPEG_SUFFIXES:
            raise ValueError(f"输入文件不是 .jpg/.jpeg：{input_path}")
        yield input_path
        return

    if not input_path.is_dir():
        raise FileNotFoundError(f"输入路径不存在：{input_path}")

    output_resolved = output_path.resolve()
    files: list[Path] = []
    for path in input_path.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in JPEG_SUFFIXES:
            continue
        # 如果输出目录位于输入目录内，避免再次处理已生成的文件。
        try:
            path.resolve().relative_to(output_resolved)
        except ValueError:
            files.append(path)
    yield from sorted(files)


def relative_output(source: Path, input_path: Path, output_path: Path) -> Path:
    if input_path.is_file():
        if output_path.suffix.lower() in JPEG_SUFFIXES:
            return output_path
        return output_path / source.name
    return output_path / source.relative_to(input_path)


def find_sos_offset(data: bytes) -> int:
    """返回 JPEG 的 SOS 标记起始偏移，用于在扫描数据前插入合法 COM 段。"""
    if not data.startswith(b"\xff\xd8"):
        raise VerificationError("文件没有 JPEG SOI 标记")

    i = 2
    while i + 1 < len(data):
        marker_start = i
        if data[i] != 0xFF:
            raise VerificationError("SOS 之前出现了异常 JPEG 数据")
        while i < len(data) and data[i] == 0xFF:
            i += 1
        if i >= len(data):
            break

        marker = data[i]
        i += 1
        if marker == 0xDA:
            return marker_start
        if marker in {0x00, 0x01, 0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if i + 2 > len(data):
            raise VerificationError("JPEG 段长度字段不完整")

        length = int.from_bytes(data[i : i + 2], "big")
        if length < 2 or i + length > len(data):
            raise VerificationError("JPEG 段长度越界")
        i += length

    raise VerificationError("JPEG 中没有找到 SOS 扫描标记")


def pad_jpeg_to_min_size(path: Path, minimum_bytes: int) -> int:
    """
    用一个或多个合法 JPEG COM 段把文件补到指定大小。

    COM 段位于 SOS 前，不参与像素解码；这比在 EOI 后追加垃圾数据更规范。
    返回实际增加的字节数。
    """
    if minimum_bytes <= 0:
        return 0

    data = path.read_bytes()
    if len(data) >= minimum_bytes:
        return 0

    insert_at = find_sos_offset(data)
    segments: list[bytes] = []
    added = 0

    while len(data) + added < minimum_bytes:
        required = minimum_bytes - (len(data) + added)
        # 每个 COM 段由 FF FE、2字节长度和 payload 组成，共 payload+4 字节。
        payload_size = min(JPEG_COMMENT_MAX_PAYLOAD, max(0, required - 4))
        if payload_size:
            payload = (PADDING_LABEL + b" " * payload_size)[:payload_size]
        else:
            payload = b""
        segment = (
            b"\xff\xfe"
            + (payload_size + 2).to_bytes(2, "big")
            + payload
        )
        segments.append(segment)
        added += len(segment)

    path.write_bytes(data[:insert_at] + b"".join(segments) + data[insert_at:])
    return added


def jpeg_structure(path: Path) -> dict[str, Any]:
    """读取 SOF 采样方式及 APP0/APP1 顺序，不依赖外部工具。"""
    data = path.read_bytes()
    if not data.startswith(b"\xff\xd8"):
        raise VerificationError("文件没有 JPEG SOI 标记")

    app_order: list[int] = []
    sof_marker: int | None = None
    sampling: tuple[tuple[int, int], ...] | None = None
    i = 2

    while i + 1 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        while i < len(data) and data[i] == 0xFF:
            i += 1
        if i >= len(data):
            break

        marker = data[i]
        i += 1
        if marker in {0x00, 0x01, 0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if i + 2 > len(data):
            raise VerificationError("JPEG 段长度字段不完整")

        length = int.from_bytes(data[i : i + 2], "big")
        if length < 2 or i + length > len(data):
            raise VerificationError("JPEG 段长度越界")
        payload = data[i + 2 : i + length]

        if marker in {0xE0, 0xE1}:
            app_order.append(marker)

        # SOF0=Baseline DCT；SOF1/SOF2 等也在这个范围。
        if 0xC0 <= marker <= 0xCF and marker not in {0xC4, 0xC8, 0xCC}:
            sof_marker = marker
            if len(payload) < 6:
                raise VerificationError("SOF 段不完整")
            components = payload[5]
            factors = []
            for component in range(components):
                offset = 6 + component * 3
                if offset + 2 >= len(payload):
                    raise VerificationError("SOF 分量数据不完整")
                sample_byte = payload[offset + 1]
                factors.append((sample_byte >> 4, sample_byte & 0x0F))
            sampling = tuple(factors)

        i += length
        if marker == 0xDA:  # SOS：后面为熵编码数据
            break

    return {
        "sof": sof_marker,
        "sampling": sampling,
        "app_order": tuple(app_order),
    }


def verify_output(
    output: Path,
    expected_size: tuple[int, int],
    expected_critical: dict[str, Any],
    minimum_bytes: int,
) -> None:
    if minimum_bytes > 0 and output.stat().st_size < minimum_bytes:
        raise VerificationError(
            f"文件只有 {output.stat().st_size} 字节，低于要求的 {minimum_bytes} 字节"
        )

    try:
        with Image.open(output) as check:
            if check.format != "JPEG":
                raise VerificationError(f"输出格式是 {check.format!r}，不是 JPEG")
            if check.size != expected_size:
                raise VerificationError(
                    f"尺寸改变：应为 {expected_size}，实际为 {check.size}"
                )
            if check.mode not in {"RGB", "L"}:
                raise VerificationError(f"异常颜色模式：{check.mode}")
            actual_critical = critical_snapshot(check.getexif())
            check.load()  # 强制完整解码，发现截断或损坏
    except VerificationError:
        raise
    except Exception as exc:
        raise VerificationError(f"输出文件无法完整解码：{exc}") from exc

    if actual_critical != expected_critical:
        raise VerificationError(
            "GPS 或拍摄日期写入前后不一致；为防止信息丢失，已拒绝该输出"
        )

    structure = jpeg_structure(output)
    if structure["sof"] != 0xC0:
        raise VerificationError(
            f"不是 Baseline JPEG（SOF 标记为 {structure['sof']!r}）"
        )

    # 彩色 JPEG 的目标采样应为 Y=2x2、Cb=1x1、Cr=1x1，即 4:2:0。
    if structure["sampling"] not in {((2, 2), (1, 1), (1, 1)), ((1, 1),)}:
        raise VerificationError(
            f"没有生成 4:2:0 JPEG，采样因子为 {structure['sampling']!r}"
        )

    app_order = structure["app_order"]
    if 0xE0 not in app_order or (0xE1 in app_order and app_order.index(0xE0) > app_order.index(0xE1)):
        raise VerificationError("JFIF(APP0) 没有位于 EXIF(APP1) 之前")


def process_one(
    source: Path,
    destination: Path,
    quality: int,
    minimum_bytes: int,
    keep_all_exif: bool,
    overwrite: bool,
) -> tuple[str, str]:
    if destination.exists() and not overwrite:
        return "SKIPPED", "输出已存在（使用 --overwrite 可重新生成）"

    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None

    try:
        with Image.open(source) as image:
            if image.format != "JPEG":
                raise VerificationError("扩展名是 JPG，但实际格式不是 JPEG")

            source_exif = image.getexif()
            expected_critical = critical_snapshot(source_exif)
            output_exif = build_compatible_exif(source_exif, keep_all_exif)
            expected_size = image.size

            # load() 在写入前发现源文件截断等问题；convert("RGB") 移除 CMYK、
            # 调色板等少见模式，生成 Android 兼容性最好的三通道 JPEG。
            image.load()
            rgb = image.convert("RGB")

            fd, temp_name = tempfile.mkstemp(
                prefix=f".{destination.stem}.",
                suffix=".tmp.jpg",
                dir=destination.parent,
            )
            os.close(fd)

            save_kwargs: dict[str, Any] = {
                "format": "JPEG",
                "quality": quality,
                "subsampling": "4:2:0",
                "progressive": False,
                "optimize": False,
                "exif": output_exif.tobytes(),
            }
            dpi = image.info.get("dpi")
            if (
                isinstance(dpi, tuple)
                and len(dpi) == 2
                and all(isinstance(v, (int, float)) and v > 0 for v in dpi)
            ):
                save_kwargs["dpi"] = dpi

            rgb.save(temp_name, **save_kwargs)

        temp_path = Path(temp_name)
        added_bytes = pad_jpeg_to_min_size(temp_path, minimum_bytes)
        verify_output(
            temp_path,
            expected_size,
            expected_critical,
            minimum_bytes,
        )
        os.replace(temp_path, destination)
        temp_name = None

        # 尽量保留原文件的访问/修改时间；失败不影响图片正确性。
        try:
            shutil.copystat(source, destination)
        except OSError:
            pass

        size_note = (
            f"，已合法填充 {added_bytes} 字节"
            if added_bytes
            else "，原文件大小已超过兼容阈值"
        )
        return (
            "OK",
            f"Baseline 4:2:0 JPEG，GPS/日期校验通过{size_note}",
        )

    except (UnidentifiedImageError, OSError, ValueError, VerificationError) as exc:
        return "FAILED", str(exc)
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


def default_output(input_path: Path) -> Path:
    if input_path.is_file():
        return input_path.parent / f"{input_path.stem}_fixed.jpg"
    return input_path.parent / f"{input_path.name}_fixed"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "批量规范化 JPG、保留并验证 GPS/日期，同时绕过旧版 Luban "
            "100 KiB 小文件回写公共目录导致的权限崩溃。"
        )
    )
    parser.add_argument("input", type=Path, help="一个 JPG 文件，或包含 JPG 的目录")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="输出文件/目录；省略时在输入路径旁创建 *_fixed",
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=92,
        help="JPEG 质量 1-100，默认 92",
    )
    parser.add_argument(
        "--min-kib",
        type=int,
        default=DEFAULT_MIN_KIB,
        help=(
            f"输出文件最小大小（KiB），默认 {DEFAULT_MIN_KIB}；"
            "不足时添加合法 JPEG COM 段，设为 0 可关闭"
        ),
    )
    parser.add_argument(
        "--keep-all-exif",
        action="store_true",
        help="保留全部 EXIF；默认只保留方向、GPS和日期，以获得更强兼容性",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="允许覆盖输出目录中的同名文件（不会覆盖输入文件）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = args.input.expanduser().resolve()
    output_path = (
        args.output.expanduser().resolve()
        if args.output is not None
        else default_output(input_path)
    )

    if not 1 <= args.quality <= 100:
        print("--quality 必须在 1 到 100 之间", file=sys.stderr)
        return 2

    if args.min_kib < 0:
        print("--min-kib 不能小于 0", file=sys.stderr)
        return 2
    minimum_bytes = args.min_kib * 1024

    if input_path == output_path:
        print("输出路径不能与输入路径相同；本工具不会原地覆盖原图。", file=sys.stderr)
        return 2

    try:
        sources = list(iter_jpegs(input_path, output_path))
    except (ValueError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if not sources:
        print("没有找到 .jpg/.jpeg 文件。", file=sys.stderr)
        return 1

    report_root = (
        output_path.parent
        if input_path.is_file() and output_path.suffix.lower() in JPEG_SUFFIXES
        else output_path
    )
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / "jpeg_fix_report.csv"

    rows: list[dict[str, str]] = []
    counts = {"OK": 0, "SKIPPED": 0, "FAILED": 0}

    for index, source in enumerate(sources, 1):
        destination = relative_output(source, input_path, output_path)
        if source.resolve() == destination.resolve():
            status, message = "FAILED", "输出文件不能覆盖输入文件"
        else:
            status, message = process_one(
                source=source,
                destination=destination,
                quality=args.quality,
                minimum_bytes=minimum_bytes,
                keep_all_exif=args.keep_all_exif,
                overwrite=args.overwrite,
            )
        counts[status] += 1
        rows.append(
            {
                "status": status,
                "source": str(source),
                "output": str(destination),
                "message": message,
            }
        )
        print(f"[{index}/{len(sources)}] {status:7} {source} — {message}")

    with report_path.open("w", encoding="utf-8-sig", newline="") as report_file:
        writer = csv.DictWriter(
            report_file,
            fieldnames=["status", "source", "output", "message"],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(
        f"\n完成：成功 {counts['OK']}，跳过 {counts['SKIPPED']}，"
        f"失败 {counts['FAILED']}。"
    )
    print(f"报告：{report_path}")
    return 1 if counts["FAILED"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
