<#
.SYNOPSIS
    One-command start for Nova on Windows.

.DESCRIPTION
    Creates the virtual environment if needed, installs dependencies, checks
    that Ollama is running with the configured model, and starts the app.

.EXAMPLE
    .\scripts\run.ps1
    .\scripts\run.ps1 -Port 9000
#>
[CmdletBinding()]
param(
    [int]$Port,
    [switch]$NoBrowser,
    [switch]$SkipChecks
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$model = if ($env:NOVA_OLLAMA_MODEL) { $env:NOVA_OLLAMA_MODEL } else { 'qwen3.5:4b' }

function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Write-Warn($message) { Write-Host "!!  $message" -ForegroundColor Yellow }

# ---------------------------------------------------------------- environment
if (-not (Test-Path $venvPython)) {
    Write-Step 'Creating the virtual environment'
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv venv --python 3.12
        uv pip install -e ".[dev]"
    } else {
        python -m venv .venv
        & $venvPython -m pip install --upgrade pip
        & $venvPython -m pip install -e ".[dev]"
    }
} else {
    Write-Step 'Virtual environment found'
}

# -------------------------------------------------------------------- checks
if (-not $SkipChecks) {
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        Write-Warn 'Ollama was not found on PATH. Install it from https://ollama.com'
    } else {
        $tags = $null
        try {
            $tags = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 3
        } catch {
            Write-Warn 'Ollama is not responding. Start it in another terminal with: ollama serve'
        }

        if ($tags) {
            $installed = @($tags.models | ForEach-Object { $_.name })
            if ($installed -notcontains $model) {
                Write-Warn "The model '$model' is not installed. Pulling it now (this is a one-off)."
                ollama pull $model
            } else {
                Write-Step "Model '$model' is ready"
            }
        }
    }

    $voices = Get-ChildItem -Path (Join-Path $projectRoot 'models\piper') -Filter '*.onnx' `
        -ErrorAction SilentlyContinue
    if (-not $voices) {
        Write-Step 'Downloading a Piper voice (about 60 MB, one-off)'
        & $venvPython scripts\download_voice.py
    }
}

# --------------------------------------------------------------------- start
if ($Port) { $env:NOVA_PORT = $Port }
if ($NoBrowser) { $env:NOVA_OPEN_BROWSER = 'false' }

Write-Step 'Starting Nova'
& $venvPython -m nova
