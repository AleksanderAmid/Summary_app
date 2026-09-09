# Shared setup functions. Loading this file never installs anything.
function Find-SmartDocTesseract {
    $command = Get-Command tesseract -CommandType Application -ErrorAction SilentlyContinue
    $candidates = @($env:SMARTDOC_TESSERACT, $command.Source,
        "$env:ProgramFiles\Tesseract-OCR\tesseract.exe",
        "$env:LOCALAPPDATA\Programs\Tesseract-OCR\tesseract.exe")
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return $candidate
        }
    }
    return $null
}

function Install-SmartDocTesseractWithWinget {
    $command = Get-Command winget -CommandType Application -ErrorAction SilentlyContinue
    if ($command) {
        & $command.Source install --exact --id UB-Mannheim.TesseractOCR --silent --accept-package-agreements --accept-source-agreements | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-Warning 'Windows package installation did not complete. Trying the direct installer.'
        }
    }
}

function Install-SmartDocTesseractDirect {
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw 'Automatic Tesseract setup requires 64-bit Windows.'
    }
    # Published by the Tesseract project and linked from its Windows install guide.
    # SHA256 is the digest published on the corresponding GitHub release asset.
    $url = 'https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/tesseract-ocr-w64-setup-5.5.3.20260724.exe'
    $sha256 = 'bee9e3434bd94fd65387d9be28cd467a41f61b1275383b55b0f59a1331270ae4'
    $installer = Join-Path ([IO.Path]::GetTempPath()) ('smartdoc-tesseract-' + [guid]::NewGuid().ToString('N') + '.exe')
    $directory = Join-Path $env:LOCALAPPDATA 'Programs\Tesseract-OCR'
    try {
        Write-Host 'Downloading the Tesseract Windows installer...'
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $installer -TimeoutSec 180
        if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash -ne $sha256) {
            throw 'Tesseract download verification failed. The installer was not run; retry setup.'
        }
        # NSIS requires /D= last and unquoted, including directories containing spaces.
        $process = Start-Process -FilePath $installer -ArgumentList @('/S', '/CURRENTUSER', "/D=$directory") -WindowStyle Hidden -Wait -PassThru
        if ($process.ExitCode -notin @(0, 3010)) {
            throw "Tesseract installation failed (exit code $($process.ExitCode)). Re-run setup and allow the Windows installer to finish."
        }
    } finally {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
}

function Get-OrInstallSmartDocTesseract {
    $executable = Find-SmartDocTesseract
    if ($executable) { return $executable }
    try {
        Install-SmartDocTesseractWithWinget
    } catch {
        Write-Warning 'Windows package installation was unavailable. Trying the direct installer.'
    }
    $executable = Find-SmartDocTesseract
    if (-not $executable) {
        Install-SmartDocTesseractDirect
        $executable = Find-SmartDocTesseract
    }
    if (-not $executable) {
        throw 'Tesseract is still unavailable. OCR setup is incomplete; install Tesseract and re-run setup.'
    }
    return $executable
}

function Assert-SmartDocOcrLanguages {
    param([string]$Executable, [string]$LanguageDirectory)
    $languages = @(& $Executable --tessdata-dir $LanguageDirectory --list-langs)
    if ($LASTEXITCODE -ne 0) { throw 'Tesseract language data could not be loaded. OCR setup is incomplete.' }
    foreach ($language in @('swe', 'eng', 'osd')) {
        if ($languages -notcontains $language) {
            throw "Tesseract language data '$language' is missing. OCR setup is incomplete."
        }
    }
}
