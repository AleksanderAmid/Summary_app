# Install local Swedish OCR. No documents are processed or uploaded by setup.
param([switch]$SkipPaddle)
$ErrorActionPreference = 'Stop'
$ocrPython = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $ocrPython) {
    $ocrPython = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $ocrPython) { throw 'Install Python with setup.bat before running OCR setup.' }
& $ocrPython -m pip install --disable-pip-version-check -r "$PSScriptRoot\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw 'Document-reading packages could not be installed.' }
$ocrExecutable = (Get-Command tesseract -ErrorAction SilentlyContinue).Source
if (-not $ocrExecutable) {
    $ocrCandidates = @("$env:ProgramFiles\Tesseract-OCR\tesseract.exe", "$env:LOCALAPPDATA\Programs\Tesseract-OCR\tesseract.exe")
    $ocrExecutable = $ocrCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $ocrExecutable -and (Get-Command winget -ErrorAction SilentlyContinue)) {
    & winget install --exact --id UB-Mannheim.TesseractOCR --silent --accept-package-agreements --accept-source-agreements
    $ocrExecutable = @("$env:ProgramFiles\Tesseract-OCR\tesseract.exe", "$env:LOCALAPPDATA\Programs\Tesseract-OCR\tesseract.exe") | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if ($ocrExecutable) {
    $ocrLanguageDir = Join-Path $PSScriptRoot 'data\ocr\tessdata'
    New-Item -ItemType Directory -Force -Path $ocrLanguageDir | Out-Null
    foreach ($ocrLanguage in @('swe','eng','osd')) {
        $ocrDestination = Join-Path $ocrLanguageDir "$ocrLanguage.traineddata"
        if (-not (Test-Path -LiteralPath $ocrDestination)) {
            Write-Host "Downloading $ocrLanguage OCR data..."
            $ocrTemporary = "$ocrDestination.download"
            Invoke-WebRequest -Uri "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/$ocrLanguage.traineddata" -OutFile $ocrTemporary
            Move-Item -LiteralPath $ocrTemporary -Destination $ocrDestination -Force
        }
    }
    & $ocrExecutable --tessdata-dir $ocrLanguageDir --list-langs
    if ($LASTEXITCODE -ne 0) { throw 'Tesseract language data could not be verified.' }
} else {
    Write-Warning 'Tesseract installation was unavailable. Install it from the official Windows installation link at https://tesseract-ocr.github.io/tessdoc/Installation.html and re-run this script.'
}
if (-not $SkipPaddle) {
    & $ocrPython -m pip install --disable-pip-version-check -r "$PSScriptRoot\requirements-ocr.txt"
    if ($LASTEXITCODE -ne 0) { throw 'PaddleOCR packages could not be installed. Tesseract can still be used.' }
    & $ocrPython -B "$PSScriptRoot\backend\setup_ocr_models.py"
    if ($LASTEXITCODE -ne 0) { throw 'PaddleOCR model setup failed. Tesseract can still be used.' }
}
Write-Host 'OCR setup complete. Restart SmartDoc to activate it.'