param(
    [string]$SourceFolder,
    [string]$DestinationFolder
)

$ErrorActionPreference = 'Stop'

function Read-U16([byte[]]$Data, [int]$Offset, [bool]$LittleEndian) {
    if ($LittleEndian) { return [int]$Data[$Offset] -bor ([int]$Data[$Offset + 1] -shl 8) }
    return ([int]$Data[$Offset] -shl 8) -bor [int]$Data[$Offset + 1]
}

function Read-U32([byte[]]$Data, [int]$Offset, [bool]$LittleEndian) {
    if ($LittleEndian) {
        return [uint32]([uint32]$Data[$Offset] -bor ([uint32]$Data[$Offset + 1] -shl 8) -bor
            ([uint32]$Data[$Offset + 2] -shl 16) -bor ([uint32]$Data[$Offset + 3] -shl 24))
    }
    return [uint32](([uint32]$Data[$Offset] -shl 24) -bor ([uint32]$Data[$Offset + 1] -shl 16) -bor
        ([uint32]$Data[$Offset + 2] -shl 8) -bor [uint32]$Data[$Offset + 3])
}

function Find-ExifTiffStart([byte[]]$Data) {
    if ($Data.Length -lt 4 -or $Data[0] -ne 0xFF -or $Data[1] -ne 0xD8) { throw 'Not a valid JPEG file' }
    $p = 2
    while ($p + 4 -le $Data.Length) {
        if ($Data[$p] -ne 0xFF) { throw 'Invalid JPEG segment structure' }
        while ($p -lt $Data.Length -and $Data[$p] -eq 0xFF) { $p++ }
        $marker = $Data[$p]; $p++
        if ($marker -eq 0xDA -or $marker -eq 0xD9) { break }
        if ($marker -eq 0x01 -or ($marker -ge 0xD0 -and $marker -le 0xD7)) { continue }
        if ($p + 2 -gt $Data.Length) { break }
        $length = ([int]$Data[$p] -shl 8) -bor [int]$Data[$p + 1]
        if ($length -lt 2 -or $p + $length -gt $Data.Length) { throw 'Invalid JPEG segment length' }
        $payload = $p + 2
        if ($marker -eq 0xE1 -and $length -ge 8 -and
            $Data[$payload] -eq 0x45 -and $Data[$payload + 1] -eq 0x78 -and
            $Data[$payload + 2] -eq 0x69 -and $Data[$payload + 3] -eq 0x66 -and
            $Data[$payload + 4] -eq 0 -and $Data[$payload + 5] -eq 0) {
            return $payload + 6
        }
        $p += $length
    }
    throw 'EXIF data not found'
}

function Get-IfdEntries([byte[]]$Data, [int]$TiffStart, [uint32]$IfdOffset, [bool]$LittleEndian) {
    $start = $TiffStart + [int]$IfdOffset
    if ($start + 2 -gt $Data.Length) { throw 'IFD offset is out of range' }
    $count = Read-U16 $Data $start $LittleEndian
    $entries = @{}
    for ($i = 0; $i -lt $count; $i++) {
        $entry = $start + 2 + 12 * $i
        if ($entry + 12 -gt $Data.Length) { throw 'IFD entry is out of range' }
        $tag = Read-U16 $Data $entry $LittleEndian
        $type = Read-U16 $Data ($entry + 2) $LittleEndian
        $size = Read-U32 $Data ($entry + 4) $LittleEndian
        $valueOffset = Read-U32 $Data ($entry + 8) $LittleEndian
        $entries[$tag] = [pscustomobject]@{ Type=$type; Size=$size; ValueOffset=$valueOffset; EntryOffset=$entry }
    }
    return $entries
}

function Get-AsciiTag([byte[]]$Data, [int]$TiffStart, $Entry) {
    if ($null -eq $Entry -or $Entry.Type -ne 2 -or $Entry.Size -lt 2) { return $null }
    $offset = if ($Entry.Size -le 4) { $Entry.EntryOffset + 8 } else { $TiffStart + [int]$Entry.ValueOffset }
    if ($offset + [int]$Entry.Size -gt $Data.Length) { throw 'EXIF string offset is out of range' }
    $length = [int]$Entry.Size
    if ($Data[$offset + $length - 1] -eq 0) { $length-- }
    return [pscustomobject]@{ Text=[Text.Encoding]::ASCII.GetString($Data, $offset, $length); Offset=$offset; Size=[int]$Entry.Size }
}

function Set-AsciiTag([byte[]]$Data, $TagInfo, [string]$Value) {
    if ($null -eq $TagInfo) { return $false }
    $bytes = [Text.Encoding]::ASCII.GetBytes($Value)
    if ($bytes.Length + 1 -ne $TagInfo.Size) { throw "Date field length mismatch: $Value" }
    [Array]::Copy($bytes, 0, $Data, $TagInfo.Offset, $bytes.Length)
    $Data[$TagInfo.Offset + $bytes.Length] = 0
    return $true
}

function Update-JpegDates([string]$InputPath, [string]$OutputPath) {
    [byte[]]$data = [IO.File]::ReadAllBytes($InputPath)
    $tiff = Find-ExifTiffStart $data
    $little = $data[$tiff] -eq 0x49 -and $data[$tiff + 1] -eq 0x49
    $big = $data[$tiff] -eq 0x4D -and $data[$tiff + 1] -eq 0x4D
    if (-not $little -and -not $big) { throw 'Invalid EXIF byte order' }
    if ((Read-U16 $data ($tiff + 2) $little) -ne 42) { throw 'Invalid TIFF marker' }
    $ifd0Offset = Read-U32 $data ($tiff + 4) $little
    $ifd0 = Get-IfdEntries $data $tiff $ifd0Offset $little
    if (-not $ifd0.ContainsKey(0x8769)) { throw 'ExifIFD not found' }
    $exifOffset = $ifd0[0x8769].ValueOffset
    $exifIfd = Get-IfdEntries $data $tiff $exifOffset $little
    $dateTime = if ($ifd0.ContainsKey(0x0132)) { Get-AsciiTag $data $tiff $ifd0[0x0132] } else { $null }
    $original = if ($exifIfd.ContainsKey(0x9003)) { Get-AsciiTag $data $tiff $exifIfd[0x9003] } else { $null }
    $digitized = if ($exifIfd.ContainsKey(0x9004)) { Get-AsciiTag $data $tiff $exifIfd[0x9004] } else { $null }
    if ($null -eq $original -or $original.Text -notmatch '^\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}$') {
        throw 'Valid DateTimeOriginal not found'
    }
    $beforeDateTime = if ($dateTime) { $dateTime.Text } else { '' }
    $beforeDigitized = if ($digitized) { $digitized.Text } else { '' }
    $changedA = Set-AsciiTag $data $dateTime $original.Text
    $changedB = Set-AsciiTag $data $digitized $original.Text
    if (-not $changedA -or -not $changedB) { throw 'DateTime or DateTimeDigitized field not found; file skipped' }
    $parent = Split-Path -Parent $OutputPath
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    [IO.File]::WriteAllBytes($OutputPath, $data)
    return [pscustomobject]@{
        BeforeDateTime = $beforeDateTime
        DateTimeOriginal = $original.Text
        BeforeDateTimeDigitized = $beforeDigitized
    }
}

$UsedDialog = -not $SourceFolder
if ($UsedDialog) {
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = 'Select the folder containing JPEG photos. Original files will not be changed.'
    $dialog.ShowNewFolderButton = $false
    if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { exit }
    $SourceFolder = $dialog.SelectedPath
}

$SourceFolder = [IO.Path]::GetFullPath($SourceFolder)
if (-not [IO.Directory]::Exists($SourceFolder)) { throw "Source folder does not exist: $SourceFolder" }
if (-not $DestinationFolder) {
    $parent = Split-Path -Parent $SourceFolder
    $name = Split-Path -Leaf $SourceFolder
    $DestinationFolder = Join-Path $parent ($name + '_dates-unified')
}
$DestinationFolder = [IO.Path]::GetFullPath($DestinationFolder)
if ($DestinationFolder.StartsWith($SourceFolder + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The destination folder cannot be inside the source folder'
}

$files = Get-ChildItem -LiteralPath $SourceFolder -Recurse -File | Where-Object { $_.Extension -match '^\.(jpg|jpeg)$' }
$report = New-Object System.Collections.Generic.List[object]
foreach ($file in $files) {
    $relative = $file.FullName.Substring($SourceFolder.Length).TrimStart('\')
    $output = Join-Path $DestinationFolder $relative
    try {
        $result = Update-JpegDates $file.FullName $output
        $report.Add([pscustomobject]@{
            File = $relative; Status = 'Success'; DateTimeOriginal = $result.DateTimeOriginal
            BeforeDateTime = $result.BeforeDateTime; BeforeDateTimeDigitized = $result.BeforeDateTimeDigitized; Note = ''
        })
    } catch {
        $report.Add([pscustomobject]@{
            File = $relative; Status = 'Skipped'; DateTimeOriginal = ''; BeforeDateTime = ''
            BeforeDateTimeDigitized = ''; Note = $_.Exception.Message
        })
    }
}

[IO.Directory]::CreateDirectory($DestinationFolder) | Out-Null
$reportPath = Join-Path $DestinationFolder 'processing-report.csv'
$report | Export-Csv -LiteralPath $reportPath -NoTypeInformation -Encoding UTF8
$success = @($report | Where-Object Status -eq 'Success').Count
$skipped = $report.Count - $success
$message = "Finished.`r`nSuccess: $success`r`nSkipped: $skipped`r`nOutput: $DestinationFolder`r`n`r`nOriginal files were not changed."
if ($UsedDialog -and [Environment]::UserInteractive) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($message, 'Photo EXIF Date Unifier') | Out-Null
}
Write-Output $message
