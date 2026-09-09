# Install local Swedish OCR. No documents are processed or uploaded by setup.
param([switch]$SkipPaddle, [string]$PythonExecutable)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\setup_ocr_helpers.ps1"
$ocrPython = $PythonExecutable
if (-not $ocrPython) { $ocrPython = (Get-Command python -CommandType Application -ErrorAction SilentlyContinue).Source }
if (-not $ocrPython) {
    $ocrPython = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $ocrPython -or -not (Test-Path -LiteralPath $ocrPython -PathType Leaf)) { throw 'Install Python with setup.bat before running OCR setup.' }
& $ocrPython -m pip install --disable-pip-version-check -r "$PSScriptRoot\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw 'Document-reading packages could not be installed.' }
$ocrExecutable = Get-OrInstallSmartDocTesseract
$ocrLanguageDir = Join-Path $PSScriptRoot 'data\ocr\tessdata'
New-Item -ItemType Directory -Force -Path $ocrLanguageDir | Out-Null
foreach ($ocrLanguage in @('swe','eng','osd')) {
    $ocrDestination = Join-Path $ocrLanguageDir "$ocrLanguage.traineddata"
    if (-not (Test-Path -LiteralPath $ocrDestination) -or (Get-Item -LiteralPath $ocrDestination).Length -eq 0) {
        Write-Host "Downloading $ocrLanguage OCR data..."
        $ocrTemporary = "$ocrDestination.download"
        Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/$ocrLanguage.traineddata" -OutFile $ocrTemporary -TimeoutSec 180
        Move-Item -LiteralPath $ocrTemporary -Destination $ocrDestination -Force
    }
}
Assert-SmartDocOcrLanguages -Executable $ocrExecutable -LanguageDirectory $ocrLanguageDir
& $ocrPython -B "$PSScriptRoot\backend\verify_ocr_setup.py" --tesseract $ocrExecutable --tessdata $ocrLanguageDir
if ($LASTEXITCODE -ne 0) { throw 'Swedish text reading could not be verified. OCR setup is incomplete; check the error above.' }
if (-not $SkipPaddle) {
    & $ocrPython -m pip install --disable-pip-version-check -r "$PSScriptRoot\requirements-ocr.txt"
    if ($LASTEXITCODE -ne 0) { throw 'PaddleOCR packages could not be installed. Tesseract can still be used.' }
    & $ocrPython -B "$PSScriptRoot\backend\setup_ocr_models.py"
    if ($LASTEXITCODE -ne 0) { throw 'PaddleOCR model setup failed. Tesseract can still be used.' }
}
Write-Host 'OCR setup complete. Restart SmartDoc to activate it.'
