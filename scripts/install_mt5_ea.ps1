<#>
.SYNOPSIS
    Fast MT5 EA Installation - Simple linear script
#>

param(
    [int]$MagicNumber = 123456
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  ATI MT5 EA Installation Script v2" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ===================== FIND MT5 DATA FOLDER =====================
$foundPaths = @()

# Method 1: Registry
Write-Host "Checking Registry..." -ForegroundColor Yellow
$regPaths = @("HKCU:\Software\MetaQuotes\Terminal", "HKLM:\SOFTWARE\MetaQuotes\Terminal", "HKLM:\SOFTWARE\WOW6432Node\MetaQuotes\Terminal")
foreach ($regPath in $regPaths) {
    $keys = Get-ChildItem $regPath -ErrorAction SilentlyContinue
    foreach ($key in $keys) {
        $dataPath = Get-ItemProperty $key.PSPath -Name "DataPath" -ErrorAction SilentlyContinue
        if ($dataPath -and $dataPath.DataPath) {
            $path = $dataPath.DataPath
            if (Test-Path (Join-Path $path "MQL5\Files")) {
                Write-Host "  Registry: $path" -ForegroundColor Green
                $foundPaths += $path
            }
        }
    }
}

# Method 2: AppData
Write-Host "Checking AppData..." -ForegroundColor Yellow
$appDataPaths = @("$env:APPDATA\MetaQuotes\Terminal", "C:\Users\$env:USERNAME\AppData\Roaming\MetaQuotes\Terminal")
foreach ($base in $appDataPaths) {
    if (Test-Path $base) {
        $dirs = Get-ChildItem $base -Directory -ErrorAction SilentlyContinue
        foreach ($dir in $dirs) {
            $filesPath = Join-Path $dir.FullName "MQL5\Files"
            if (Test-Path $filesPath) {
                Write-Host "  AppData: $($dir.FullName)" -ForegroundColor Green
                $foundPaths += $dir.FullName
            }
        }
    }
}

# Method 3: Program Files
Write-Host "Checking Program Files..." -ForegroundColor Yellow
$pfPaths = @("C:\Program Files\MetaTrader 5", "C:\Program Files (x86)\MetaTrader 5")
foreach ($pf in $pfPaths) {
    $filesPath = Join-Path $pf "MQL5\Files"
    if (Test-Path $filesPath) {
        Write-Host "  Program Files: $pf" -ForegroundColor Green
        $foundPaths += $pf
    }
}

# Method 4: Public folders
Write-Host "Checking Public folders..." -ForegroundColor Yellow
$publicPaths = @("C:\Users\Public\AppData\Roaming\MetaQuotes\Terminal", "C:\ProgramData\MetaQuotes\Terminal")
foreach ($base in $publicPaths) {
    if (Test-Path $base) {
        $dirs = Get-ChildItem $base -Directory -ErrorAction SilentlyContinue
        foreach ($dir in $dirs) {
            $filesPath = Join-Path $dir.FullName "MQL5\Files"
            if (Test-Path $filesPath) {
                Write-Host "  Public: $($dir.FullName)" -ForegroundColor Green
                $foundPaths += $dir.FullName
            }
        }
    }
}

$found = @($foundPaths | Select-Object -Unique)

# ===================== MAIN =====================

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  ATI MT5 EA Installation Script v2" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

if ($found.Count -eq 0) {
    Write-Host "Could not find MT5 data folder automatically." -ForegroundColor Red
    Write-Host "Please ensure MetaTrader 5 is installed and has been run at least once." -ForegroundColor Red
    Write-Host ""
    Write-Host "Common locations:" -ForegroundColor Gray
    Write-Host "  C:\Program Files\MetaTrader 5" -ForegroundColor Gray
    Write-Host "  C:\Program Files (x86)\MetaTrader 5" -ForegroundColor Gray
    Write-Host "  %APPDATA%\MetaQuotes\Terminal" -ForegroundColor Gray
    Write-Host ""
    Write-Host "If MT5 is installed elsewhere, run:" -ForegroundColor Yellow
    Write-Host '  .\scripts\install_mt5_ea.ps1 -Mt5DataPath "C:\Your\MT5\Path"' -ForegroundColor Yellow
    exit 1
}

$mt5DataPath = $found[0]
Write-Host "Using MT5 data folder: $mt5DataPath" -ForegroundColor Green

if ($found.Count -gt 1) {
    Write-Host "Other MT5 installations found:" -ForegroundColor Yellow
    $found | Select-Object -Skip 1 | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
}

# Ensure MQL5 structure
$mql5Path = Join-Path $mt5DataPath "MQL5"
$expertsPath = Join-Path $mql5Path "Experts"
$filesPath = Join-Path $mql5Path "Files"

if (-not (Test-Path $expertsPath)) { New-Item -ItemType Directory -Force -Path $expertsPath | Out-Null; Write-Host "Created Experts folder" }
if (-not (Test-Path $filesPath)) { New-Item -ItemType Directory -Force -Path $filesPath | Out-Null; Write-Host "Created Files folder" }

# Copy EA
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$eaSource = Join-Path $scriptDir "..\backend\infrastructure\mt5\ea\ATI_EA.mq5"
$eaSource = Resolve-Path $eaSource -ErrorAction SilentlyContinue
if (-not $eaSource) { $eaSource = Get-ChildItem -Recurse -Filter "ATI_EA.mq5" -ErrorAction SilentlyContinue | Select-Object -First 1 }
if (-not $eaSource) { Write-Host "Could not find ATI_EA.mq5" -ForegroundColor Red; exit 1 }

$eaDest = Join-Path $expertsPath "ATI_EA.mq5"
Write-Host "Installing EA to $eaDest..."
Copy-Item -Path $eaSource -Destination $eaDest -Force
Write-Host "EA installed to $eaDest" -ForegroundColor Green

# Update .env with MT5 path
$envPath = Join-Path (Split-Path -Parent $scriptDir) "..\.env"
$envContent = @"
MT5_DATA_FOLDER=$mt5DataPath
MT5_MAGIC_NUMBER=123456
"@
$envContent | Out-File -FilePath $envPath -Encoding utf8 -Append -Force
Write-Host "Updated .env with MT5_DATA_FOLDER" -ForegroundColor Green

Write-Host ""
Write-Host "========================================"
Write-Host "  INSTALLATION COMPLETE"
Write-Host "========================================"
Write-Host ""
Write-Host "Next steps:"
Write-Host "1. Open MetaEditor (F4 in MT5 or Start > MetaEditor)"
Write-Host "2. Open the EA file that was copied"
Write-Host "3. Press F7 to compile (should show 0 errors, 0 warnings)"
Write-Host "4. Drag 'ATI_EA' from Navigator onto any chart"
Write-Host "5. In EA settings:"
Write-Host "   - Allow WebRequest: ADD 'http://localhost:8080'"
Write-Host "   - BridgePort: 8080"
Write-Host "   - MagicNumber: 123456 (matches .env)"
Write-Host "   - AllowedSymbols: (leave empty for all)"
Write-Host ""
Write-Host "Then run: python -m backend.main --mode paper"

# Auto-open MetaEditor if available
$metaEditor = "C:\Program Files\MetaTrader 5\metaeditor.exe"
if (-not (Test-Path $metaEditor)) { $metaEditor = "C:\Program Files (x86)\MetaTrader 5\metaeditor.exe" }
if (Test-Path $metaEditor) {
    Write-Host "Opening MetaEditor..."
    Start-Process $metaEditor -ArgumentList (Join-Path $expertsPath "ATI_EA.mq5")
}