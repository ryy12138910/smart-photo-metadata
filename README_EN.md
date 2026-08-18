# Photo Watermark Metadata Tool

[中文说明](README.md)

This tool organizes coordinates and capture times from photo watermarks in engineering, inspection, surveying, and field-work photo collections. It extracts the visible information into an Excel workbook for human review, then writes approved results to new photo files.

## Background

Field photos often show location and time on the image while the same information is missing from the file metadata. Reviewing and copying these values one photo at a time is slow and error-prone.

The tool provides a repeatable workflow:

1. Extract watermark information from a photo collection.
2. Review and correct the results in Excel.
3. Write only approved results to new photos.
4. Preserve the source photos and folder structure for traceability.

## Recognition Modes

- **OCR Only (Local):** photos remain on the computer and can be processed offline.
- **OCR + LLM API:** a vision-capable model reviews incomplete or suspicious results. Any provider and model can be used when it offers a compatible image chat endpoint.

## Quick Start

### Portable Windows Package

Download `PhotoMetadataTool-Windows-x64.zip` from [GitHub Releases](https://github.com/ryy12138910/smart-photo-metadata/releases/latest). Do not use “Code → Download ZIP” on the repository page; that archive contains source code.

Extract the package to a normal folder and double-click `PhotoMetadataTool.exe` or `启动程序.bat`. Python, offline OCR, and the required libraries are already included. No Python installation, pip command, environment variable, or application path configuration is needed.

Use the language selector in the upper-right corner to switch between Chinese and English. Review workbooks created from the English interface use English headers and review choices.

### Source Code

Source mode is intended for developers who need to modify the program. It requires Python and access to dependency download services. If dependency installation fails, use the portable Windows package instead.

## Workflow

### 1. Extract and Review

1. Choose the photo root folder.
2. Choose where to save the review workbook.
3. Select OCR Only or OCR + LLM API.
4. When using an API, select the provider and enter the model name and API key.
5. Select **Create Review Workbook**.

The selected model must support image input. The API key is supplied only to the current task and is not written to the workbook, cache, or log.

Open the completed workbook and inspect rows marked **Needs Review**. Correct coordinates or capture times, change confirmed rows to **Approved**, and save the workbook.

#### Processing Options

| Option | Description |
| --- | --- |
| Recognition Mode | OCR Only runs entirely on the computer. OCR + LLM API sends photos that need review to the configured API. |
| API Provider | OpenAI and Alibaba Cloud Model Studio (Qwen) configure their endpoints automatically. Choose Custom Compatible Service for another provider. |
| API Endpoint | No input is needed for OpenAI or Qwen. For a custom compatible service, enter the complete image-chat endpoint supplied by that provider. |
| Model Name | The provider's model identifier. The selected model must support image input. |
| API Key | The credential used for the current model task. The application does not save it. |
| API Review Scope | Review Exceptions Only reduces API calls. Review All Photos with Missing Metadata is more comprehensive but may increase usage and cost. |

The Alibaba Cloud Model Studio (Qwen) preset uses the public China (Beijing) compatible endpoint. Select Custom Compatible Service for another region or a workspace-specific endpoint.
| Workbook Images | Keep links only, embed compressed thumbnails, or embed original photos. Embedding originals can make the workbook very large. |
| Path Filter | Process only photos whose full path contains this text. Leave it blank to process every supported photo under the selected root folder. |
| Photo Limit | Maximum number of photos for this run. Enter `0` for no limit. This is useful for a small pilot run. |
| Folder Outlier Threshold (m) | Flags an OCR coordinate when it differs from the typical location of other photos in the same direct folder by more than this distance. The default is `500` metres. |

### 2. Write New Photos

1. Open the **Write New Photos** tab.
2. Choose the original photo folder, reviewed workbook, and output folder.
3. Keep **Dry run only** enabled for the first run and inspect the result report.
4. When the report is correct, disable dry run and run again to create new photos.

Source photos are not modified. By default, the tool only fills missing metadata. Replacing existing EXIF fields should be enabled only when necessary.

#### Write Options

| Option | Description |
| --- | --- |
| Dry run only | Validate the reviewed workbook and create a report without creating new photos. Keep this enabled for the first run. |
| Replace existing EXIF | Allow reviewed GPS or capture-time values to replace existing metadata. Files are still written to a new output folder. |

If the reviewed capture time contains a date only, the application writes `00:00:00` as its clock time and records a warning in `write_result_report.xlsx`. Existing capture time is still preserved unless EXIF replacement is enabled.

## Outputs

| Output | Purpose |
| --- | --- |
| `metadata_review.xlsx` | Review, correct, and approve extracted values |
| `photos_with_exif/` | New photos containing the approved metadata |
| `write_result_report.xlsx` | Per-photo processing status and failure reasons |

If a task is interrupted, run it again with the same photo folder and workbook path to resume from its checkpoint.

## Recommendations

- Test the full workflow with a small photo set before a production batch.
- Never choose the source photo folder as the output folder.
- Use local OCR for sensitive photos, or confirm that the chosen API meets your data-handling requirements.
- OCR and model output can be wrong; complete the necessary human review before archival use.

## Troubleshooting

### “Umi-OCR local API did not become ready”

Download the Windows portable package from GitHub Releases and extract the complete ZIP before starting the application. Do not run the EXE from inside the ZIP preview and do not copy only the two EXE files. The `runtime` folder must remain beside `PhotoMetadataTool.exe`.

Version `v1.0.0` had a Umi-OCR startup compatibility issue. Upgrade to `v1.0.4` or later. If a newer version still fails, check whether security software blocked `Umi-OCR.exe` under `runtime`, or whether another application is using local port `1224`.

## Supported Systems and Files

- Extract and review: JPG, JPEG, PNG, BMP, and WebP.
- Write photo metadata: JPG, JPEG, and PNG.
- Primary target: 64-bit Windows 10 and Windows 11.

## License

This project is available under the [MIT License](LICENSE).
