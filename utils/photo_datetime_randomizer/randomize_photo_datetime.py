#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Randomize photo dates while keeping each direct-folder group consistent.

The input directory is scanned recursively for JPG, JPEG and PNG files. Photos
whose direct parent directory is the same receive the same calendar date and
nearby shooting times. By default, modified copies are written to a new sibling
directory so the source photos remain untouched.
"""

from __future__ import print_function

import argparse
import csv
from datetime import date, datetime, time, timedelta
import os
from pathlib import Path
import random
import re
import sys

# Reuse the EXIF writer maintained at the repository root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from exif_gps_utils import write_datetime_exif, write_png_exif


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
DEFAULT_START_DATE = date(2026, 6, 1)
DEFAULT_END_DATE = date(2026, 7, 25)
DEFAULT_START_TIME = time(8, 30, 0)
DEFAULT_END_TIME = time(17, 0, 0)
DEFAULT_MAX_GROUP_SPAN_MINUTES = 10
MANIFEST_NAME = "照片日期修改记录.csv"


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "递归修改 JPG/JPEG/PNG 的拍摄日期；同一直接父目录中的照片日期相同、时间相近。"
        )
    )
    parser.add_argument("input_dir", help="包含照片及子文件夹的根目录")
    parser.add_argument(
        "--output-dir",
        help="输出目录；默认在输入目录旁创建“原目录名_日期已修改”",
    )
    parser.add_argument(
        "--start-date",
        default=DEFAULT_START_DATE.isoformat(),
        help="最早日期，格式 YYYY-MM-DD（默认 2026-06-01）",
    )
    parser.add_argument(
        "--end-date",
        default=DEFAULT_END_DATE.isoformat(),
        help="最晚日期，格式 YYYY-MM-DD（默认 2026-07-25）",
    )
    parser.add_argument(
        "--start-time",
        default=DEFAULT_START_TIME.strftime("%H:%M:%S"),
        help="每天最早时间，格式 HH:MM 或 HH:MM:SS（默认 08:30）",
    )
    parser.add_argument(
        "--end-time",
        default=DEFAULT_END_TIME.strftime("%H:%M:%S"),
        help="每天最晚时间，格式 HH:MM 或 HH:MM:SS（默认 17:00）",
    )
    parser.add_argument(
        "--max-group-span-minutes",
        type=int,
        default=DEFAULT_MAX_GROUP_SPAN_MINUTES,
        help="同一文件夹内最早和最晚照片的最大间隔分钟数（默认 10）",
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="随机种子；指定后，同一批文件每次可得到相同结果",
    )
    parser.add_argument(
        "--keep-file-times",
        action="store_true",
        help="只改照片 EXIF，不同步文件的创建/访问/修改时间",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="直接修改原图（默认关闭；使用前请先备份）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="允许覆盖输出目录中同名的已有文件",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅显示计划，不创建或修改任何文件",
    )
    return parser


def parse_date(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("无效日期 %r，应为 YYYY-MM-DD" % value)


def parse_time(value):
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            pass
    raise ValueError("无效时间 %r，应为 HH:MM 或 HH:MM:SS" % value)


def natural_key(path):
    return [
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", path.name)
    ]


def discover_groups(input_root):
    """Return {direct parent: sorted image paths} for supported photos."""
    groups = {}
    for root, dirs, files in os.walk(str(input_root)):
        dirs.sort(key=str.casefold)
        parent = Path(root)
        images = [
            parent / name
            for name in files
            if Path(name).suffix.lower() in IMAGE_EXTENSIONS
        ]
        if images:
            groups[parent] = sorted(images, key=natural_key)
    return dict(sorted(groups.items(), key=lambda item: str(item[0]).casefold()))


def seconds_since_midnight(value):
    return value.hour * 3600 + value.minute * 60 + value.second


def datetime_from_day_and_second(day, second):
    return datetime.combine(day, time()) + timedelta(seconds=second)


def choose_group_datetimes(
    photo_count,
    rng,
    start_date,
    end_date,
    start_time,
    end_time,
    max_group_span_minutes,
):
    """Choose one date and nearby, ascending times for a folder group."""
    if photo_count <= 0:
        return []
    day_count = (end_date - start_date).days
    chosen_date = start_date + timedelta(days=rng.randint(0, day_count))

    first_second = seconds_since_midnight(start_time)
    last_second = seconds_since_midnight(end_time)
    available_seconds = last_second - first_second
    span_seconds = min(max_group_span_minutes * 60, available_seconds)

    if photo_count == 1:
        offsets = [0]
    elif photo_count <= span_seconds + 1:
        offsets = [0] + sorted(
            rng.sample(range(1, span_seconds + 1), photo_count - 1)
        )
    else:
        offsets = [
            round(index * span_seconds / float(photo_count - 1))
            for index in range(photo_count)
        ]

    latest_offset = offsets[-1]
    base_second = rng.randint(first_second, last_second - latest_offset)
    return [
        datetime_from_day_and_second(chosen_date, base_second + offset)
        for offset in offsets
    ]


def build_assignments(
    groups,
    seed,
    start_date,
    end_date,
    start_time,
    end_time,
    max_group_span_minutes,
):
    rng = random.Random(seed)
    assignments = {}
    for folder, photos in groups.items():
        values = choose_group_datetimes(
            len(photos),
            rng,
            start_date,
            end_date,
            start_time,
            end_time,
            max_group_span_minutes,
        )
        assignments[folder] = list(zip(photos, values))
    return assignments


def default_output_dir(input_root):
    folder_name = input_root.name or "照片"
    return input_root.parent / (folder_name + "_日期已修改")


def is_relative_to(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_paths(input_root, output_root, in_place):
    if not input_root.is_dir():
        raise ValueError("输入目录不存在或不是文件夹：%s" % input_root)
    if in_place:
        return
    if output_root == input_root:
        raise ValueError("输出目录不能与输入目录相同；如需修改原图，请使用 --in-place")
    if is_relative_to(output_root, input_root):
        raise ValueError("输出目录不能放在输入目录内部，否则递归处理时可能重复读取")


def set_file_times(path, value):
    """Set access/modified time and, on Windows, creation time as well."""
    timestamp = value.timestamp()
    os.utime(str(path), (timestamp, timestamp))
    if os.name != "nt":
        return

    import ctypes
    from ctypes import wintypes

    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    set_file_time = ctypes.windll.kernel32.SetFileTime
    set_file_time.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    set_file_time.restype = wintypes.BOOL

    file_write_attributes = 0x0100
    file_share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    handle = create_file(
        str(path),
        file_write_attributes,
        file_share_all,
        None,
        open_existing,
        0,
        None,
    )
    invalid_handle = wintypes.HANDLE(-1).value
    if handle == invalid_handle:
        raise OSError(ctypes.get_last_error(), "无法打开文件以设置创建时间", str(path))
    try:
        intervals = int(timestamp * 10_000_000) + 116_444_736_000_000_000
        file_time = wintypes.FILETIME(intervals & 0xFFFFFFFF, intervals >> 32)
        if not set_file_time(
            handle,
            ctypes.byref(file_time),
            ctypes.byref(file_time),
            ctypes.byref(file_time),
        ):
            raise OSError(ctypes.get_last_error(), "无法设置 Windows 文件时间", str(path))
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def write_photo(source, target, assigned_datetime, in_place):
    suffix = source.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        return Path(
            write_datetime_exif(
                str(source),
                in_place=in_place,
                dst_path=None if in_place else str(target),
                datetime_original=assigned_datetime,
            )
        )
    if suffix == ".png":
        return Path(
            write_png_exif(
                str(source),
                in_place=in_place,
                dst_path=None if in_place else str(target),
                datetime_original=assigned_datetime,
            )
        )
    raise ValueError("不支持的图片格式：%s" % source)


def write_manifest(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["原图", "输出图", "所属文件夹", "新拍摄时间", "结果", "错误"],
        )
        writer.writeheader()
        writer.writerows(rows)


def run(args):
    input_root = Path(args.input_dir).expanduser().resolve()
    output_root = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else default_output_dir(input_root)
    )

    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)
    start_time = parse_time(args.start_time)
    end_time = parse_time(args.end_time)
    if start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期")
    if seconds_since_midnight(start_time) > seconds_since_midnight(end_time):
        raise ValueError("开始时间不能晚于结束时间")
    if args.max_group_span_minutes < 0:
        raise ValueError("--max-group-span-minutes 不能小于 0")
    if args.in_place and args.output_dir:
        raise ValueError("--in-place 与 --output-dir 不能同时使用")

    validate_paths(input_root, output_root, args.in_place)
    groups = discover_groups(input_root)
    if not groups:
        print("未找到 JPG、JPEG 或 PNG 图片：%s" % input_root)
        return 0

    assignments = build_assignments(
        groups,
        args.seed,
        start_date,
        end_date,
        start_time,
        end_time,
        args.max_group_span_minutes,
    )
    photo_total = sum(len(items) for items in assignments.values())
    print(
        "找到 %d 个图片文件夹，共 %d 张照片。%s"
        % (
            len(assignments),
            photo_total,
            "仅演练，不会写文件。" if args.dry_run else "开始处理……",
        )
    )

    if (
        not args.in_place
        and output_root.exists()
        and not args.overwrite
        and not args.dry_run
    ):
        raise ValueError(
            "输出目录已存在：%s\n如确认覆盖其中的同名文件，请增加 --overwrite"
            % output_root
        )

    rows = []
    success_count = 0
    error_count = 0
    current = 0
    for folder, items in assignments.items():
        relative_folder = folder.relative_to(input_root)
        folder_date = items[0][1].strftime("%Y-%m-%d")
        print(
            "[文件夹] %s：%d 张，日期 %s"
            % (
                relative_folder if str(relative_folder) != "." else ".",
                len(items),
                folder_date,
            )
        )
        for source, assigned_datetime in items:
            current += 1
            relative_path = source.relative_to(input_root)
            target = source if args.in_place else output_root / relative_path
            display_time = assigned_datetime.strftime("%Y-%m-%d %H:%M:%S")
            row = {
                "原图": str(source),
                "输出图": str(target),
                "所属文件夹": str(relative_folder),
                "新拍摄时间": display_time,
                "结果": "",
                "错误": "",
            }
            if args.dry_run:
                row["结果"] = "计划"
                success_count += 1
                print(
                    "  [%d/%d] %s -> %s"
                    % (current, photo_total, relative_path, display_time)
                )
                rows.append(row)
                continue

            try:
                if target.exists() and not args.in_place and not args.overwrite:
                    raise FileExistsError("目标文件已存在：%s" % target)
                written_path = write_photo(
                    source,
                    target,
                    assigned_datetime,
                    args.in_place,
                )
                if not args.keep_file_times:
                    set_file_times(written_path, assigned_datetime)
                row["结果"] = "成功"
                success_count += 1
                print(
                    "  [%d/%d] 成功：%s -> %s"
                    % (current, photo_total, relative_path, display_time)
                )
            except Exception as exc:
                row["结果"] = "失败"
                row["错误"] = str(exc)
                error_count += 1
                print(
                    "  [%d/%d] 失败：%s（%s）"
                    % (current, photo_total, relative_path, exc),
                    file=sys.stderr,
                )
            rows.append(row)

    if not args.dry_run:
        manifest_root = input_root if args.in_place else output_root
        manifest_path = manifest_root / MANIFEST_NAME
        write_manifest(manifest_path, rows)
        print("处理记录：%s" % manifest_path)
    print("完成：成功 %d 张，失败 %d 张。" % (success_count, error_count))
    if not args.in_place and not args.dry_run:
        print("输出目录：%s" % output_root)
    return 1 if error_count else 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
