# CyrusAsha_GuiForOpenDataLoaderPDF-1.0.0 - create a Desktop shortcut for the control center
# Usage (PowerShell):  .\install-shortcut.ps1
# This file is ASCII-only on purpose: Windows PowerShell 5.1 reads
# UTF-8 files without a BOM as ANSI, which breaks non-ASCII text.
param(
  [string]$Name = "CyrusAsha_GuiForOpenDataLoaderPDF-1.0.0"
)

$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$target = Join-Path $here "start-silent.bat"
$icon = Join-Path $here "icon.ico"

if (-not (Test-Path $target)) {
  Write-Error "start-silent.bat not found in: $here"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "$Name.lnk"
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = $target
$lnk.WorkingDirectory = $root
$lnk.WindowStyle = 7
$lnk.Description = "CyrusAsha_GuiForOpenDataLoaderPDF-1.0.0 - OpenDataLoader PDF control center"
if (Test-Path $icon) { $lnk.IconLocation = "$icon,0" }
$lnk.Save()

Write-Host ""
Write-Host "Desktop shortcut created: $lnkPath"
Write-Host ""
