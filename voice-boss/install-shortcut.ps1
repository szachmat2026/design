# Tworzy ikonkę "Voice Boss" na pulpicie. Uruchom raz: powershell -ExecutionPolicy Bypass -File install-shortcut.ps1
param([string]$Cwd = $HOME, [switch]$Screen, [switch]$Autostart)
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (Get-Command python -ErrorAction Stop).Source
# logo ze strony kadm.app -> kadm.ico (przy błędzie zostaje domyślna ikonka)
$ico = "$dir\kadm.ico"
if (-not (Test-Path $ico)) { try { & $py "$dir\make_icon.py" "https://kadm.app" $ico } catch { Write-Host "Brak logo: $_" } }
$flags = "--open --cwd `"$Cwd`"" + $(if ($Screen) { " --screen" } else { "" })
$ws = New-Object -ComObject WScript.Shell
function New-Link($path) {
  $l = $ws.CreateShortcut($path)
  $l.TargetPath = $py
  $l.Arguments = "`"$dir\server.py`" $flags"
  $l.WorkingDirectory = $dir
  $l.WindowStyle = 7   # zminimalizowane okno konsoli
  $l.IconLocation = $(if (Test-Path $ico) { $ico } else { "$env:SystemRoot\System32\mmres.dll,0" })
  $l.Save()
}
New-Link ([Environment]::GetFolderPath("Desktop") + "\KADM Studio.lnk")
if ($Autostart) { New-Link ([Environment]::GetFolderPath("Startup") + "\KADM Studio.lnk") }
Write-Host "Gotowe: ikonka 'KADM Studio' na pulpicie" $(if ($Autostart) { "i w autostarcie." } else { "." })
