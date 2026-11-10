# Bringt den lokalen Stand per SSH auf den Raspberry Pi und startet LabControl dort neu.
# Geht direkt PC -> Pi (Bare-Repo ~/labcontrol.git am Pi), nicht ueber GitHub.
#
#   powershell -File tools\pi\deploy_pi.ps1 [-PiHost 192.168.178.181] [-User labcontrol]
param(
    [string]$PiHost = "192.168.178.181",
    [string]$User = "labcontrol",
    [string]$Key = "$env:USERPROFILE\.ssh\labpi_ed25519",
    [string]$Branch = "master"
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$ssh = "ssh -i `"$($Key -replace '\\','/')`" -o BatchMode=yes"

$dirty = git -C $repo status --porcelain --untracked-files=no
if ($dirty) { Write-Warning "Nicht committete Aenderungen werden NICHT uebertragen:`n$dirty" }

$env:GIT_SSH_COMMAND = $ssh
git -C $repo push -q "ssh://$User@$PiHost/home/$User/labcontrol.git" "${Branch}:master"
if ($LASTEXITCODE -ne 0) { throw "git push zum Pi fehlgeschlagen" }
# update_pi.sh vom PC mitschicken: gilt so schon beim ersten Mal und immer in der neuen Fassung
# (CR und BOM entfernen, die Windows-Arbeitskopie hat CRLF)
Get-Content -Raw (Join-Path $PSScriptRoot "update_pi.sh") |
    & ssh -i $Key -o BatchMode=yes "$User@$PiHost" "tr -d '\r' | sed '1s/^\xEF\xBB\xBF//' | bash -s"
if ($LASTEXITCODE -ne 0) { throw "Update am Pi fehlgeschlagen" }
