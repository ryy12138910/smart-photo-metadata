# 图片经纬度/拍摄日期智能补全

通过识别照片水印，智能提取经纬度与拍摄日期，并自动补全照片的地理位置及拍摄时间元数据。

## 批量随机覆盖照片拍摄日期

`utils/photo_datetime_randomizer/randomize_photo_datetime.py` 会递归处理输入目录中的 JPG、JPEG 和 PNG。
照片按“直接父文件夹”分组：同一最末级照片文件夹使用同一天，组内时间按文件名顺序小幅递增。
默认日期范围为 2026-06-01 至 2026-07-25（含首尾），时间范围为 08:30 至 17:00，
同组照片的最大时间跨度为 10 分钟。

最简单的用法是把照片文件夹拖到 `utils/photo_datetime_randomizer/修改照片日期.bat`
上。也可以使用命令行：

```powershell
.\.venv\Scripts\python.exe .\utils\photo_datetime_randomizer\randomize_photo_datetime.py "E:\照片下载结果_缺失补充"
```

默认不会修改原图，而是在输入目录旁新建“原目录名_日期已修改”，并生成
`照片日期修改记录.csv`。程序覆盖照片的三个常用 EXIF 日期字段，同时同步输出文件的
创建、访问和修改时间；GPS 等其他 EXIF 保持不变。

先预览分配结果但不写文件：

```powershell
.\.venv\Scripts\python.exe .\utils\photo_datetime_randomizer\randomize_photo_datetime.py "E:\照片下载结果_缺失补充" --dry-run --seed 2026
```

需要固定输出位置或直接修改原图时：

```powershell
.\.venv\Scripts\python.exe .\utils\photo_datetime_randomizer\randomize_photo_datetime.py "E:\照片" --output-dir "E:\照片_新日期" --seed 2026
.\.venv\Scripts\python.exe .\utils\photo_datetime_randomizer\randomize_photo_datetime.py "E:\照片" --in-place --seed 2026
```

`--in-place` 会直接改原图，使用前务必自行备份。

这套流程保持“Excel 可人工复核”的原有做法，但把处理顺序调整为逐字段融合：

1. 先读取原图 EXIF，已有字段永不靠模型猜测。
2. 对缺字段图片调用本机 Umi-OCR 接口（程序可自动启动 Umi）。
3. 规则解析 OCR，并修复本项目里常见的“东经→东纪、120→720”错误。
4. 默认只将缺字段、超范围、解析失败或发生纠错的图片交给百炼 `qwen3.5-ocr` 并发复核。
5. 仅将仍缺字段、低置信或与 Umi 结果冲突的图片交给 `qwen3.6-flash` 做最终视觉复核。
6. 按 `EXIF > 视觉模型 > OCR` 逐字段融合，输出带来源、置信度和问题说明的审核表。
7. 高置信且无冲突的行标为“自动通过”；冲突、低置信或仍缺字段的行标为“待核”。
8. 写出时复制原目录结构，只给“通过/自动通过”行写入 EXIF。

当前审核流程分为四个阶段：先完成全部图片的 EXIF/Umi-OCR 并保存断点，再把需要复核的问题图片
并发提交给百炼 Qwen3.5-OCR；仅把仍然不确定的图片提交给 Qwen3.6-Flash，最后统一融合字段并
生成审核表。Umi-OCR 默认每批 32 张，以文件
路径提交到同一个 Umi `MissionOCR` 任务队列并逐图返回结果，不再逐张读取原图、转换 Base64
和建立 HTTP 请求。百炼阶段采用单图请求并行化，默认 Qwen3.5-OCR 并发 10、Qwen3.6-Flash
并发 5；429 和临时 5xx 错误会自动退避重试。

模型只允许转写图片上明确可见的水印，不允许根据地名或建筑物猜经纬度。

## 环境

### 准备 Umi-OCR

为避免仓库体积过大，Umi-OCR 的 Windows 运行包不纳入版本控制。请从
[Umi-OCR Releases](https://github.com/hiroi-sora/Umi-OCR/releases) 下载 Paddle v2.1.5，
解压后确认可执行文件位于：

```text
utils/Umi-OCR_Paddle_v2.1.5/Umi-OCR.exe
```

### 安装 Python 依赖

```powershell
py -m pip install -r requirements.txt
```

默认模式需要阿里云百炼 API Key。PowerShell 中执行（将值替换为实际 Key）：

```powershell
[Environment]::SetEnvironmentVariable(
  "DASHSCOPE_API_KEY",
  "YOUR_DASHSCOPE_API_KEY",
  [EnvironmentVariableTarget]::User
)
```

验证是否设置成功：

```powershell
[bool][Environment]::GetEnvironmentVariable(
  "DASHSCOPE_API_KEY",
  [EnvironmentVariableTarget]::User
)
```

显示 `True` 后，关闭已经打开的图形界面，再双击 `start_gui.bat`，让新进程读取环境变量。
API Key 不写入配置、缓存或日志。也可用 `--llm-provider ollama-cloud`、`ollama` 或
`openai` 切换回原有模型接口。

## 图形界面（推荐日常使用）

在项目文件夹中双击 `启动图形界面.bat`，即可打开本地桌面页面，不需要手工输入命令。
界面分为两个页签：

1. `生成审核 XLSX`：选择照片根目录和审核表保存位置，设置模型策略、缩略图模式及
   小范围测试条件，然后点击“开始生成审核表”。
2. `生成新图片`：选择原始照片目录、审核后的 XLSX 和输出目录。第一次建议勾选
   “仅演练”，检查 `write_result_report.xlsx` 后再取消勾选正式生成图片。

页面底部会显示实时日志和图片处理进度。任务中途停止时，审核表旁边的 `.cache.json`
和 `.cache.json.journal` 会保留逐图断点。再次选择完全相同的照片根目录及审核表路径后，
界面会显示“已恢复上次断点”，已完成图片不会再次读取 EXIF、调用 OCR 或调用本地模型。
点击“停止当前任务”或直接关闭窗口时，程序会先尝试保存当前图片断点，再结束后台进程。

大批量任务默认使用“不嵌入图片，仅保留链接（最快）”，避免为每张照片生成 Excel 缩略图；
如果审核时确实需要直接看图，可手动改为“压缩缩略图”。缓存采用逐图追加写入，不再每处理
一张照片就重写整个缓存文件。

也可以在已激活的虚拟环境中直接启动：

```powershell
.\.venv\Scripts\python.exe photo_metadata_gui.py
```

## 第一步：生成智能审核表

```powershell
py smart_photo_metadata.py review `
  --image-root "D:\code\lon\data\跨塘社区城建资料\跨塘社区建筑照片" `
  --review-xlsx "D:\code\lon\output\metadata_review.xlsx"
```

程序支持中断续跑，OCR、模型和已合并的审核行保存在审核表旁边的缓存及追加式断点日志中。
先小范围验证时可加 `--max-images 20` 或 `--path-contains "10幢"`。
程序使用 Umi 的 `127.0.0.1:1224` 本地接口，识别边长设置为 960，以提高批量识别速度。
实现参考 [Umi-OCR 官方命令行手册](https://github.com/hiroi-sora/Umi-OCR/blob/main/docs/README_CLI.md)：
使用路径列表进入 Umi 内部批量任务。默认 `--ocr-batch-size 32`，每个批次完成后将逐图结果
写入断点。每次任务只检查并启动一次 Umi 服务；服务不可用时整项任务直接报错，不再对每张图片
重复等待。
OCR 阶段全部完成后才进入批量模型复核阶段；关闭任务后重新使用相同路径，可以直接复用已完成的
OCR 断点和已完成的模型批次。
默认使用 `qwen3.5-ocr` 和 `qwen3.6-flash`，关闭思考模式并要求精简 JSON。
`--llm-review-mode suspicious` 只让模型处理异常 OCR。如需让模型复核所有
EXIF 不完整的图片，可显式增加 `--llm-review-mode all`。

百炼相关参数：

- `--dashscope-ocr-concurrency 10`：第一阶段专用 OCR 并发数。
- `--dashscope-review-concurrency 5`：最终视觉复核并发数。
- `--api-max-retries 4`：429/5xx 自动退避重试次数。
- `--refresh-model`：忽略已有成功模型断点并重新请求。

日志会分别显示两个阶段的调用数、累计 Token、耗时和预估费用。成功的逐图结果立即写入
断点；关闭程序后再次使用相同的照片目录与审核表路径，只重试未完成或失败的接口请求。

如暂时不使用模型：

```powershell
py smart_photo_metadata.py review ... --llm-provider none
```

## 第二步：只检查“待核”行

审核表中的关键列：

- `审核结果`：自动通过、待核、通过、跳过等。
- `字段来源`：每个字段来自 EXIF、Umi-OCR 还是视觉模型。
- `字段置信度`：经度、纬度、时间分别评分。
- `解析提示`：缺字段、范围异常和来源冲突。
- `模型说明`：模型看见的原始证据，方便快速核对。

筛选 `审核结果=待核`，修改经纬度/时间后将该行改成 `通过`。
若希望所有行都由人确认，生成审核表时加 `--no-auto-approve`。

## 第三步：写入新图片

建议先做不写文件的演练：

```powershell
py smart_photo_metadata.py write `
  --image-root "D:\code\lon\data\跨塘社区城建资料\跨塘社区建筑照片" `
  --review-xlsx "D:\code\lon\output\metadata_review.xlsx" `
  --output-dir "D:\code\lon\output\photos_with_exif" `
  --dry-run
```

确认结果报告后去掉 `--dry-run`。默认复制到新目录并保留原目录结构；除非已有可靠备份，不建议使用 `--in-place`。
写出时默认只补缺失的 EXIF 字段，已有 GPS 或拍摄时间不会被覆盖。如果确实需要用审核表覆盖已有字段，必须显式增加 `--overwrite-existing-exif`。

## 设计上的安全边界

- 完整 EXIF 直接保留并跳过，不被 OCR/模型覆盖。
- 部分 EXIF 字段也保持最高优先级；默认写图时只补缺失字段。
- 超出项目范围的 OCR 经纬度会逐字段丢弃，不再进入融合。
- 合法且位于项目范围内的模型坐标会写入审核表；低分或缺少证据时保留候选值但标为待核。
- OCR 与模型冲突时使用模型结果，并在解析提示中记录覆盖过程。
- Umi-OCR 只识别到日期而没有时分时，会继续进入 Qwen OCR；Qwen OCR 仍只返回日期时，
  再由 Qwen Flash 查看原图确认是否存在时分。
- 图片明确显示时分时保留完整日期时间；图片只显示日期时，审核表只保留日期，不补
  `00:00:00`；所有视觉来源都没有日期时保持为空，也不使用文件名猜测。
- 标准 EXIF `DateTimeOriginal` 需要完整日期和时分，因此日期-only 结果会保留在审核表中，
  但写出图片时不会伪造午夜时间写入 EXIF。
- 百炼模式会将筛选出的异常图片上传到阿里云模型服务；完整 EXIF 或 Umi 已可靠识别的图片
  不会上传。使用本地 Ollama 模式时图片仍只发送到本机模型服务。

## 同目录坐标一致性与审核表体积

默认把图片的直接父目录视为同一拍摄对象。当目录中至少有 3 张图片具备完整坐标时，
程序会计算组内中位坐标；某张由 OCR 提供的坐标偏离超过 500 米，即使格式正确且仍在
项目范围内，也会调用本地模型重新查看原图。模型不可用、置信度过低或复核结果仍然离群
时，该行会标为“待核”。可通过以下参数调整：

- `--group-coordinate-threshold-meters 500`：离群距离阈值。
- `--group-min-images 3`：启用组内检查所需的最少图片数。
- `--no-group-consistency`：关闭该检查。

审核表默认使用 `--excel-image-mode thumbnail`，嵌入的是最大 520×320、质量 70 的
JPEG 缩略图，不再嵌入原图字节；“照片链接”和“原图路径”仍指向原始文件，写 EXIF
时也仍使用原图。超大批次可完全不嵌图：

```powershell
py smart_photo_metadata.py review ... --excel-image-mode none
```

如确实需要恢复旧行为，可使用 `--excel-image-mode original`。缩略图大小还可通过
`--photo-display-max-width`、`--photo-display-max-height` 和
`--thumbnail-quality` 调整。
