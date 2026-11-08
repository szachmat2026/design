# Tworzy ikonkę "Voice Boss" na pulpicie. Uruchom raz: powershell -ExecutionPolicy Bypass -File install-shortcut.ps1
param([string]$Cwd = $HOME, [switch]$Screen, [switch]$Autostart)
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (Get-Command python -ErrorAction Stop).Source
$flags = "--open --cwd `"$Cwd`"" + $(if ($Screen) { " --screen" } else { "" })
$ws = New-Object -ComObject WScript.Shell
function New-Link($path) {
  $l = $ws.CreateShortcut($path)
  $l.TargetPath = $py
  $l.Arguments = "`"$dir\server.py`" $flags"
  $l.WorkingDirectory = $dir
  $l.WindowStyle = 7   # zminimalizowane okno konsoli
  $l.IconLocation = "$env:SystemRoot\System32\mmres.dll,0"  # ikonka mikrofonu
  $l.Save()
}
New-Link ([Environment]::GetFolderPath("Desktop") + "\Voice Boss.lnk")
if ($Autostart) { New-Link ([Environment]::GetFolderPath("Startup") + "\Voice Boss.lnk") }
Write-Host "Gotowe: ikonka 'Voice Boss' na pulpicie" $(if ($Autostart) { "i w autostarcie." } else { "." })
