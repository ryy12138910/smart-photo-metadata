#!/usr/bin/env python
# -*- coding: utf-8 -*-

from datetime import date, time
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image
import piexif

TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

import randomize_photo_datetime as photo_dates


class RandomizePhotoDatetimeTests(unittest.TestCase):
    def test_same_folder_gets_one_date_and_nearby_times(self):
        import random

        values = photo_dates.choose_group_datetimes(
            photo_count=4,
            rng=random.Random(123),
            start_date=date(2026, 6, 1),
            end_date=date(2026, 7, 25),
            start_time=time(8, 30),
            end_time=time(17, 0),
            max_group_span_minutes=10,
        )

        self.assertEqual(len({value.date() for value in values}), 1)
        self.assertEqual(values, sorted(values))
        self.assertLessEqual((values[-1] - values[0]).total_seconds(), 10 * 60)
        self.assertGreaterEqual(values[0].time(), time(8, 30))
        self.assertLessEqual(values[-1].time(), time(17, 0))
        self.assertGreaterEqual(values[0].date(), date(2026, 6, 1))
        self.assertLessEqual(values[0].date(), date(2026, 7, 25))

    def test_seed_makes_assignments_repeatable(self):
        import random

        kwargs = dict(
            photo_count=6,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 7, 25),
            start_time=time(8, 30),
            end_time=time(17, 0),
            max_group_span_minutes=10,
        )
        first = photo_dates.choose_group_datetimes(rng=random.Random(99), **kwargs)
        second = photo_dates.choose_group_datetimes(rng=random.Random(99), **kwargs)
        self.assertEqual(first, second)

    def test_jpeg_and_png_are_written_with_expected_exif(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            group = source / "42711" / "赵胜路1号2幢"
            group.mkdir(parents=True)
            Image.new("RGB", (24, 18), "red").save(group / "1.jpg", quality=90)
            Image.new("RGBA", (24, 18), "blue").save(group / "2.png")
            output = root / "output"

            args = photo_dates.build_parser().parse_args(
                [
                    str(source),
                    "--output-dir",
                    str(output),
                    "--seed",
                    "7",
                    "--keep-file-times",
                ]
            )
            result = photo_dates.run(args)

            self.assertEqual(result, 0)
            jpeg_output = output / "42711" / "赵胜路1号2幢" / "1.jpg"
            png_output = output / "42711" / "赵胜路1号2幢" / "2.png"
            self.assertTrue(jpeg_output.is_file())
            self.assertTrue(png_output.is_file())
            jpeg_exif = piexif.load(str(jpeg_output))
            with Image.open(png_output) as png_image:
                png_exif = piexif.load(png_image.info["exif"])
            jpeg_value = jpeg_exif["Exif"][piexif.ExifIFD.DateTimeOriginal]
            png_value = png_exif["Exif"][piexif.ExifIFD.DateTimeOriginal]
            self.assertEqual(jpeg_value[:10], png_value[:10])
            self.assertGreaterEqual(jpeg_value[11:], b"08:30:00")
            self.assertLessEqual(jpeg_value[11:], b"17:00:00")
            self.assertTrue((output / photo_dates.MANIFEST_NAME).is_file())

    def test_discovery_groups_by_direct_parent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "a").mkdir()
            (root / "b" / "inner").mkdir(parents=True)
            Image.new("RGB", (4, 4)).save(root / "a" / "1.jpg")
            Image.new("RGB", (4, 4)).save(root / "a" / "2.png")
            Image.new("RGB", (4, 4)).save(root / "b" / "inner" / "3.jpeg")

            groups = photo_dates.discover_groups(root)

            self.assertEqual(set(groups), {root / "a", root / "b" / "inner"})
            self.assertEqual(len(groups[root / "a"]), 2)


if __name__ == "__main__":
    unittest.main()
