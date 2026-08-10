# 批量随机修改照片日期

本工具递归处理 JPG、JPEG 和 PNG，并按图片的直接父文件夹分组：

- 同一最末级照片文件夹使用同一天。
- 组内时间按文件名顺序小幅递增，默认最大跨度为 10 分钟。
- 日期范围默认为 2026-06-01 至 2026-07-25（含首尾）。
- 时间范围默认为 08:30 至 17:00。
- 覆盖三个常用 EXIF 拍摄时间字段，并同步 Windows 文件时间。
- 默认创建新输出目录，不修改原图。

## 使用方法

最简单的方法：把照片根文件夹拖到 `修改照片日期.bat` 上。

也可以从项目根目录运行：

```powershell
.\.venv\Scripts\python.exe .\utils\photo_datetime_randomizer\randomize_photo_datetime.py "E:\照片下载结果_缺失补充"
```

默认结果保存到原文件夹旁边的“原文件夹名_日期已修改”，并生成
`照片日期修改记录.csv`。

先预览，不写入任何文件：

```powershell
.\.venv\Scripts\python.exe .\utils\photo_datetime_randomizer\randomize_photo_datetime.py "E:\照片下载结果_缺失补充" --dry-run --seed 2026
```

固定输出目录：

```powershell
.\.venv\Scripts\python.exe .\utils\photo_datetime_randomizer\randomize_photo_datetime.py "E:\照片" --output-dir "E:\照片_新日期" --seed 2026
```

直接修改原图：

```powershell
.\.venv\Scripts\python.exe .\utils\photo_datetime_randomizer\randomize_photo_datetime.py "E:\照片" --in-place --seed 2026
```

`--in-place` 会直接覆盖原图元数据，使用前务必备份。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest -v .\utils\photo_datetime_randomizer\test_randomize_photo_datetime.py
```
