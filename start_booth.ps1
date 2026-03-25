# Project Iris - Arduin-o-vation Booth Launcher
# Run this script to start Iris with proper environment configuration

# Audio configuration - adjust these if needed for the exhibit floor
$env:MIC_INDEX = $null  # Use $null for OS default, or set to specific device index (e.g., "1")
$env:AEC_MULTIPLIER = "2.0"  # Threshold multiplier for echo cancellation (tune on-site)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Project Iris - Arduin-o-vation" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Configuration:" -ForegroundColor Yellow
Write-Host "  MIC_INDEX:     $(if ($env:MIC_INDEX) { $env:MIC_INDEX } else { 'OS Default' })"
Write-Host "  AEC_MULTIPLIER: $env:AEC_MULTIPLIER"
Write-Host ""

# Check for Python
try {
    $pythonVersion = python --version 2>&1
    Write-Host "Python: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "ERROR: Python not found in PATH" -ForegroundColor Red
    Write-Host "Please install Python and ensure it's in your PATH" -ForegroundColor Red
    exit 1
}

# Start the booth
Write-Host "Starting Iris..." -ForegroundColor Cyan
Write-Host ""

python wake_up.py
