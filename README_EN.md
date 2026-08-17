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
4. When using an API, enter the complete endpoint, model name, and API key.
5. Select **Create Review Workbook**.

The selected model must support image input. The API key is supplied only to the current task and is not written to the workbook, cache, or log.

Open the completed workbook and inspect rows marked **Needs Review**. Correct coordinates or capture times, change confirmed rows to **Approved**, and save the workbook.

### 2. Write New Photos

1. Open the **Write New Photos** tab.
2. Choose the original photo folder, reviewed workbook, and output folder.
3. Keep **Dry run only** enabled for the first run and inspect the result report.
4. When the report is correct, disable dry run and run again to create new photos.

Source photos are not modified. By default, the tool only fills missing metadata. Replacing existing EXIF fields should be enabled only when necessary.

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

## Supported Systems and Files

- Extract and review: JPG, JPEG, PNG, BMP, and WebP.
- Write photo metadata: JPG, JPEG, and PNG.
- Primary target: 64-bit Windows 10 and Windows 11.

## License

This project is available under the [MIT License](LICENSE).
