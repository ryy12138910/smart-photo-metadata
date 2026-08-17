#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Helpers for parsing GPS text and writing JPEG EXIF GPS tags."""

from __future__ import print_function

import math
import os
import re
import shutil

from PIL import Image

try:
    import piexif
except ImportError:
    piexif = None


IMAGE_EXTENSIONS = (".jpg", ".jpeg")


def require_piexif():
    if piexif is None:
        raise RuntimeError(
            "Missing dependency: piexif. Install it with: pip install piexif"
        )


def is_jpeg(path):
    return os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS


def is_png(path):
    return os.path.splitext(path)[1].lower() == ".png"


def iter_jpegs(folder):
    for root, _, files in os.walk(folder):
        for name in files:
            path = os.path.join(root, name)
            if is_jpeg(path):
                yield path


def normalize_text(text):
    if text is None:
        return ""
    text = str(text)
    replacements = {
        "°": " deg ",
        "º": " deg ",
        "˚": " deg ",
        "′": "'",
        "’": "'",
        "‘": "'",
        "″": '"',
        "“": '"',
        "”": '"',
        "，": ",",
        "：": ":",
        "；": ";",
        "　": " ",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return re.sub(r"\s+", " ", text).strip()


def _parse_dms(value):
    """Parse '31 deg 22' 0.12" N' or decimal-like text to signed degrees."""
    text = normalize_text(value)
    if not text:
        return None

    sign = -1 if re.search(r"\b[SW]\b|[南西]|South|West", text, re.I) else 1
    nums = re.findall(r"[-+]?\d+(?:\.\d+)?", text)
    if not nums:
        return None

    parts = [float(n) for n in nums[:3]]
    if len(parts) >= 3:
        deg, minutes, seconds = parts[:3]
        result = abs(deg) + minutes / 60.0 + seconds / 3600.0
    elif len(parts) == 2 and re.search(r"deg|度|'|分", text, re.I):
        deg, minutes = parts[:2]
        result = abs(deg) + minutes / 60.0
    else:
        result = abs(parts[0])
        if parts[0] < 0:
            sign = -1

    return sign * result


def parse_coordinate_pair(latitude_text, longitude_text):
    lat = _parse_dms(latitude_text)
    lon = _parse_dms(longitude_text)
    if lat is None or lon is None:
        raise ValueError("Could not parse latitude/longitude: %r, %r" % (latitude_text, longitude_text))
    validate_lat_lon(lat, lon)
    return lat, lon


def parse_gps_from_text(text):
    """Extract a latitude/longitude pair from OCR text.

    Supports common forms:
      纬度 31.3667 经度 120.7167
      Lat: 31 deg 22' 0.12" N Lon: 120 deg 43' 0.12" E
      N31.3667 E120.7167
      31.3667, 120.7167
    """
    clean = normalize_text(text)

    label_patterns = [
        (
            r"(?:纬度|GPSLatitude|Latitude|Lat)\s*[:=]?\s*([NS南北]?\s*[-+]?\d+(?:\.\d+)?(?:\s*(?:deg|度|d)\s*\d+(?:\.\d+)?(?:\s*(?:'|分|m)\s*\d+(?:\.\d+)?)?)?\s*(?:\"|秒|s)?\s*[NS南北]?)",
            r"(?:经度|GPSLongitude|Longitude|Lon|Lng|Long)\s*[:=]?\s*([EW东西]?\s*[-+]?\d+(?:\.\d+)?(?:\s*(?:deg|度|d)\s*\d+(?:\.\d+)?(?:\s*(?:'|分|m)\s*\d+(?:\.\d+)?)?)?\s*(?:\"|秒|s)?\s*[EW东西]?)",
        ),
        (
            r"(?:经度|GPSLongitude|Longitude|Lon|Lng|Long)\s*[:=]?\s*([EW东西]?\s*[-+]?\d+(?:\.\d+)?(?:\s*(?:deg|度|d)\s*\d+(?:\.\d+)?(?:\s*(?:'|分|m)\s*\d+(?:\.\d+)?)?)?\s*(?:\"|秒|s)?\s*[EW东西]?)",
            r"(?:纬度|GPSLatitude|Latitude|Lat)\s*[:=]?\s*([NS南北]?\s*[-+]?\d+(?:\.\d+)?(?:\s*(?:deg|度|d)\s*\d+(?:\.\d+)?(?:\s*(?:'|分|m)\s*\d+(?:\.\d+)?)?)?\s*(?:\"|秒|s)?\s*[NS南北]?)",
        ),
    ]

    for first_pattern, second_pattern in label_patterns:
        first = re.search(first_pattern, clean, re.I)
        second = re.search(second_pattern, clean, re.I)
        if first and second:
            first_text = first.group(1)
            second_text = second.group(1)
            if "经" in first_pattern or "Longitude" in first_pattern or "Lon" in first_pattern:
                lat, lon = parse_coordinate_pair(second_text, first_text)
            else:
                lat, lon = parse_coordinate_pair(first_text, second_text)
            return lat, lon

    hemi_pair = re.search(
        r"([NS南北])\s*([-+]?\d+(?:\.\d+)?)\D{0,30}([EW东西])\s*([-+]?\d+(?:\.\d+)?)",
        clean,
        re.I,
    )
    if hemi_pair:
        lat = _parse_dms(hemi_pair.group(1) + hemi_pair.group(2))
        lon = _parse_dms(hemi_pair.group(3) + hemi_pair.group(4))
        validate_lat_lon(lat, lon)
        return lat, lon

    pair = re.search(r"([-+]?\d{1,2}\.\d{4,})\s*[,; ]+\s*([-+]?\d{2,3}\.\d{4,})", clean)
    if pair:
        lat = float(pair.group(1))
        lon = float(pair.group(2))
        validate_lat_lon(lat, lon)
        return lat, lon

    dms_chunks = re.findall(
        r"(\d{1,3})\s*(?:deg|度|d|。|\.|°|º|˚)?\s*(\d{1,2})\s*(?:'|分|m|′|’|‘)?",
        clean,
        re.I,
    )
    for i in range(0, len(dms_chunks) - 1):
        lat_deg, lat_min = [int(x) for x in dms_chunks[i]]
        lon_deg, lon_min = [int(x) for x in dms_chunks[i + 1]]
        if 0 <= lat_deg <= 90 and 0 <= lat_min < 60 and 70 <= lon_deg <= 180 and 0 <= lon_min < 60:
            lat = lat_deg + lat_min / 60.0
            lon = lon_deg + lon_min / 60.0
            validate_lat_lon(lat, lon)
            return lat, lon

    candidates = [float(n) for n in re.findall(r"[-+]?\d+(?:\.\d+)?", clean)]
    for i in range(0, len(candidates) - 1):
        lat = candidates[i]
        lon = candidates[i + 1]
        if -90 <= lat <= 90 and -180 <= lon <= 180 and abs(lon) >= 70:
            return lat, lon

    raise ValueError("No latitude/longitude pair found in text.")


def validate_lat_lon(lat, lon):
    if lat is None or lon is None:
        raise ValueError("Latitude/longitude cannot be empty.")
    if not (-90.0 <= float(lat) <= 90.0):
        raise ValueError("Latitude out of range: %r" % lat)
    if not (-180.0 <= float(lon) <= 180.0):
        raise ValueError("Longitude out of range: %r" % lon)


def _decimal_to_dms_rational(decimal_degrees):
    decimal_degrees = abs(float(decimal_degrees))
    degrees = int(decimal_degrees)
    minutes_float = (decimal_degrees - degrees) * 60.0
    minutes = int(minutes_float)
    seconds = int(round((minutes_float - minutes) * 60.0))
    if seconds >= 60:
        seconds = 0
        minutes += 1
    if minutes >= 60:
        minutes = 0
        degrees += 1
    return (
        (degrees, 1),
        (minutes, 1),
        (seconds, 1),
    )


def _empty_exif_dict():
    return {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}


def _safe_exif_load(image_path):
    require_piexif()
    try:
        return piexif.load(image_path)
    except Exception:
        pass

    try:
        with Image.open(image_path) as img:
            exif_bytes = img.info.get("exif")
        if exif_bytes:
            return piexif.load(exif_bytes)
    except Exception:
        pass

    return _empty_exif_dict()


def load_exif_dict(image_path):
    return _safe_exif_load(image_path)


def _format_exif_datetime(value):
    if not value:
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%Y:%m:%d %H:%M:%S").encode("ascii")

    text = str(value).strip()
    if not text:
        return None

    match = re.search(
        r"(\d{4})[-:/\.](\d{1,2})[-:/\.](\d{1,2})(?:\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?",
        text,
    )
    if not match:
        raise ValueError("Invalid EXIF datetime: %r" % value)

    year, month, day = [int(v) for v in match.group(1, 2, 3)]
    hour = int(match.group(4) or 0)
    minute = int(match.group(5) or 0)
    second = int(match.group(6) or 0)
    return ("%04d:%02d:%02d %02d:%02d:%02d" % (year, month, day, hour, minute, second)).encode("ascii")


def _target_path(src_path, dst_path=None, in_place=False):
    if in_place:
        target_path = src_path
    elif dst_path:
        target_path = dst_path
    else:
        raise ValueError("Provide dst_path or set in_place=True.")

    target_dir = os.path.dirname(os.path.abspath(target_path))
    if target_dir and not os.path.isdir(target_dir):
        os.makedirs(target_dir)

    if os.path.abspath(src_path) != os.path.abspath(target_path):
        shutil.copy2(src_path, target_path)
    return target_path


def _set_datetime_tags(exif_dict, datetime_original):
    datetime_value = _format_exif_datetime(datetime_original)
    if not datetime_value:
        return False

    exif_ifd = exif_dict.get("Exif") or {}
    zeroth_ifd = exif_dict.get("0th") or {}
    exif_ifd[piexif.ExifIFD.DateTimeOriginal] = datetime_value
    exif_ifd[piexif.ExifIFD.DateTimeDigitized] = datetime_value
    zeroth_ifd[piexif.ImageIFD.DateTime] = datetime_value
    exif_dict["Exif"] = exif_ifd
    exif_dict["0th"] = zeroth_ifd
    return True


def _set_gps_tags(exif_dict, lat, lon):
    validate_lat_lon(lat, lon)
    gps_ifd = exif_dict.get("GPS") or {}
    gps_ifd[piexif.GPSIFD.GPSLatitudeRef] = b"N" if lat >= 0 else b"S"
    gps_ifd[piexif.GPSIFD.GPSLatitude] = _decimal_to_dms_rational(lat)
    gps_ifd[piexif.GPSIFD.GPSLongitudeRef] = b"E" if lon >= 0 else b"W"
    gps_ifd[piexif.GPSIFD.GPSLongitude] = _decimal_to_dms_rational(lon)
    gps_ifd[piexif.GPSIFD.GPSMapDatum] = b"WGS-84"
    gps_ifd[piexif.GPSIFD.GPSVersionID] = (2, 3, 0, 0)
    exif_dict["GPS"] = gps_ifd


def write_datetime_exif(src_path, dst_path=None, in_place=False, datetime_original=None):
    """Write only shooting datetime to JPEG EXIF, leaving GPS unchanged."""
    require_piexif()
    if not is_jpeg(src_path):
        raise ValueError("Only .jpg/.jpeg files are supported: %s" % src_path)

    target_path = _target_path(src_path, dst_path=dst_path, in_place=in_place)
    exif_dict = _safe_exif_load(target_path)
    if not _set_datetime_tags(exif_dict, datetime_original):
        raise ValueError("datetime_original is empty")
    piexif.insert(piexif.dump(exif_dict), target_path)
    return target_path


def _pnginfo_from_image(img):
    from PIL import PngImagePlugin

    pnginfo = PngImagePlugin.PngInfo()
    for key, value in img.info.items():
        if key in ("exif", "icc_profile", "transparency", "dpi"):
            continue
        if isinstance(value, str):
            pnginfo.add_text(key, value)
    return pnginfo


def _save_png_with_exif(src_path, target_path, exif_bytes):
    with Image.open(src_path) as img:
        pnginfo = _pnginfo_from_image(img)
        save_kwargs = {"pnginfo": pnginfo, "exif": exif_bytes}
        if "icc_profile" in img.info:
            save_kwargs["icc_profile"] = img.info.get("icc_profile")
        if "dpi" in img.info:
            save_kwargs["dpi"] = img.info.get("dpi")
        if "transparency" in img.info:
            save_kwargs["transparency"] = img.info.get("transparency")

        if os.path.abspath(src_path) == os.path.abspath(target_path):
            temp_path = target_path + ".tmp.png"
            img.save(temp_path, format="PNG", **save_kwargs)
            os.replace(temp_path, target_path)
        else:
            target_dir = os.path.dirname(os.path.abspath(target_path))
            if target_dir and not os.path.isdir(target_dir):
                os.makedirs(target_dir)
            img.save(target_path, format="PNG", **save_kwargs)


def write_png_exif(src_path, lat=None, lon=None, dst_path=None, in_place=False, datetime_original=None):
    """Write GPS and/or shooting datetime into a PNG eXIf chunk."""
    require_piexif()
    if not is_png(src_path):
        raise ValueError("Only .png files are supported: %s" % src_path)
    if lat is None and lon is None and not datetime_original:
        raise ValueError("No PNG EXIF values to write")
    if (lat is None) != (lon is None):
        raise ValueError("Both latitude and longitude are required to write PNG GPS")

    target_path = _target_path(src_path, dst_path=dst_path, in_place=in_place)
    exif_dict = _safe_exif_load(target_path)
    if lat is not None and lon is not None:
        _set_gps_tags(exif_dict, lat, lon)
    _set_datetime_tags(exif_dict, datetime_original)
    _save_png_with_exif(target_path, target_path, piexif.dump(exif_dict))
    return target_path


def write_gps_exif(src_path, lat, lon, dst_path=None, in_place=False, datetime_original=None):
    """Write GPS coordinates and optional shooting datetime to JPEG EXIF."""
    require_piexif()
    validate_lat_lon(lat, lon)

    if not is_jpeg(src_path):
        raise ValueError("Only .jpg/.jpeg files are supported: %s" % src_path)

    target_path = _target_path(src_path, dst_path=dst_path, in_place=in_place)

    exif_dict = _safe_exif_load(target_path)
    _set_gps_tags(exif_dict, lat, lon)
    _set_datetime_tags(exif_dict, datetime_original)

    piexif.insert(piexif.dump(exif_dict), target_path)
    return target_path


def copy_output_path(src_path, input_root, output_root):
    rel_path = os.path.relpath(src_path, input_root)
    return os.path.join(output_root, rel_path)


def gps_to_decimal_from_exif_tuple(values, ref):
    deg, minutes, seconds = values
    def div(pair):
        return float(pair[0]) / float(pair[1])
    result = div(deg) + div(minutes) / 60.0 + div(seconds) / 3600.0
    if ref in (b"S", "S", b"W", "W"):
        result *= -1
    return result


def read_gps_exif(image_path):
    require_piexif()
    exif_dict = _safe_exif_load(image_path)
    gps = exif_dict.get("GPS") or {}
    lat_val = gps.get(piexif.GPSIFD.GPSLatitude)
    lat_ref = gps.get(piexif.GPSIFD.GPSLatitudeRef)
    lon_val = gps.get(piexif.GPSIFD.GPSLongitude)
    lon_ref = gps.get(piexif.GPSIFD.GPSLongitudeRef)
    if not all([lat_val, lat_ref, lon_val, lon_ref]):
        return None
    return (
        gps_to_decimal_from_exif_tuple(lat_val, lat_ref),
        gps_to_decimal_from_exif_tuple(lon_val, lon_ref),
    )
