# SmartDoc Medical Summary - one-time setup for a new device.
# Checks for and installs everything the app needs:
#   1. Python 3.10+        (winget, or python.org installer, per-user)
#   2. Ollama              (winget, or ollama.com installer, per-user)
#   3. gemma4:12b model (~8 GB download via ollama pull)
#   4. Document and encryption packages (pip)
# Safe to re-run: every step is skipped when already satisfied.
# Run via setup.bat, or:  powershell -ExecutionPolicy Bypass -File setup.ps1

$ErrorActionPreference = "Stop"
$MODEL_TAG = "gemma4:12b"

function Write-Step($msg)  { Write-Host "`n=== $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "  OK  $msg" -ForegroundColor Green }
function Write-Info($msg)  { Write-Host "      $msg" }
function Write-Fail($msg)  { Write-Host "  !!  $msg" -ForegroundColor Red }

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user    = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Test-Winget {
    try { $null = Get-Command winget -ErrorAction Stop; return $true }
    catch { return $false }
}

# ---------------------------------------------------------------- Python
function Get-PythonCommand {
    # Returns a working Python >= 3.10 invocation, or $null.
    $candidates = @(
        @{ exe = "py";     args = @("-3", "--version") },
        @{ exe = "python"; args = @("--version") }
    )
    foreach ($c in $candidates) {
        try {
            $out = & $c.exe @($c.args) 2>$null
            if ($out -match "Python 3\.(\d+)") {
                if ([int]$Matches[1] -ge 10) {
                    if ($c.exe -eq "py") { return "py -3" } else { return "python" }
                }
            }
        } catch { }
    }
    # Direct per-user python.org install locations (PATH may be stale).
    $found = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" `
             -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
    if ($found) { return "`"$($found.FullName)`"" }
    return $null
}

Write-Step "Step 1/5 - Python 3.10+"
$py = Get-PythonCommand
if ($py) {
    Write-Ok "Python found: $py"
} else {
    Write-Info "Python not found - installing..."
    if (Test-Winget) {
        winget install -e --id Python.Python.3.12 --silent `
            --accept-package-agreements --accept-source-agreements
    } else {
        $url = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
        $dst = Join-Path $env:TEMP "python-setup.exe"
        Write-Info "Downloading $url"
        Invoke-WebRequest -Uri $url -OutFile $dst
        Write-Info "Running silent per-user install (PrependPath=1)..."
        Start-Process $dst -WindowStyle Hidden -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0" -Wait
        Remove-Item $dst -ErrorAction SilentlyContinue
    }
    Refresh-Path
    $py = Get-PythonCommand
    if ($py) { Write-Ok "Python installed: $py" }
    else { Write-Fail "Python installation failed - install manually from python.org"; exit 1 }
}

# ---------------------------------------------------------------- Ollama
Write-Step "Step 2/5 - Ollama"
function Test-Ollama {
    try { $null = & ollama --version 2>$null; return $true } catch { }
    return Test-Path "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
}
if (Test-Ollama) {
    Write-Ok "Ollama found"
} else {
    Write-Info "Ollama not found - installing..."
    if (Test-Winget) {
        winget install -e --id Ollama.Ollama --silent `
            --accept-package-agreements --accept-source-agreements
    } else {
        $url = "https://ollama.com/download/OllamaSetup.exe"
        $dst = Join-Path $env:TEMP "OllamaSetup.exe"
        Write-Info "Downloading $url (~700 MB)"
        Invoke-WebRequest -Uri $url -OutFile $dst
        Write-Info "Running silent install..."
        Start-Process $dst -WindowStyle Hidden -ArgumentList "/VERYSILENT /NORESTART" -Wait
        Remove-Item $dst -ErrorAction SilentlyContinue
    }
    Refresh-Path
    if (Test-Ollama) { Write-Ok "Ollama installed" }
    else { Write-Fail "Ollama installation failed - install manually from ollama.com"; exit 1 }
}
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    # PATH not refreshed in this session - use the default install location.
    Set-Alias -Name ollama -Value "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" -Scope Script
}

# Configure the shared model server before starting it on a new installation.
& "$PSScriptRoot\configure_parallel.ps1"

# Make sure the Ollama server is running before talking to it.
function Test-OllamaApi {
    try {
        $null = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 3
        return $true
    } catch { return $false }
}
if (-not (Test-OllamaApi)) {
    Write-Info "Starting the Ollama server..."
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    $tries = 0
    while (-not (Test-OllamaApi) -and $tries -lt 15) { Start-Sleep -Seconds 1; $tries++ }
}
if (Test-OllamaApi) { Write-Ok "Ollama server is running" }
else { Write-Fail "Could not start the Ollama server - start the Ollama app manually and re-run"; exit 1 }

# Explicit no-truncation controls are verified against Ollama 0.32.1+.
$summaryRuntimeVersion = (Invoke-RestMethod -Uri "http://localhost:11434/api/version" -TimeoutSec 5).version
if ($summaryRuntimeVersion -notmatch '^(\d+\.\d+\.\d+)' -or [version]$Matches[1] -lt [version]'0.32.1') {
    Write-Fail "Ollama 0.32.1 or newer is required. Update Ollama, restart it, and run setup again."
    exit 1
}
Write-Ok "Ollama $summaryRuntimeVersion supports protected long-record summaries"

# ---------------------------------------------------------------- Model
Write-Step "Step 3/5 - Summarization model ($MODEL_TAG)"
$tags = (Invoke-RestMethod -Uri "http://localhost:11434/api/tags").models | ForEach-Object { $_.name }
if ($tags -contains $MODEL_TAG) {
    Write-Ok "Model already pulled"
} else {
    Write-Info "Pulling $MODEL_TAG (~8 GB - this can take a while)..."
    & ollama pull $MODEL_TAG
    if ($LASTEXITCODE -ne 0) { Write-Fail "Model pull failed - check your internet connection and re-run"; exit 1 }
    Write-Ok "Model pulled"
}

# ---------------------------------------------------------------- Python packages
Write-Step "Step 4/5 - Document and encryption packages"
# Native commands run via cmd /c so stderr redirection happens outside
# PowerShell (5.1 turns redirected native stderr into terminating errors).
& cmd /c "$py -c `"import fitz, cryptography, docx, reportlab, PIL, numpy, cv2`" >nul 2>&1"
if ($LASTEXITCODE -eq 0) {
    Write-Ok "Document and encryption packages already installed"
} else {
    Write-Info "Installing document and encryption packages..."
    & cmd /c "$py -m pip install --quiet -r `"$PSScriptRoot\requirements.txt`" 2>&1"
    if ($LASTEXITCODE -eq 0) { Write-Ok "Document and encryption packages installed" }
    else { Write-Fail "Required package installation failed. Check the error above and re-run setup."; exit 1 }
}

Write-Host ""
Write-Step "Step 5/5 - Swedish OCR"
& "$PSScriptRoot\setup_ocr.ps1"

Write-Host "Setup complete. Start the app with SmartDoc.vbs" -ForegroundColor Green
Write-Host "(the UI opens at http://localhost:8765)"
