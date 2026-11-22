# Nodo Chile en Windows: corre el radar SOLO para tiendas que bloquean a GitHub (Unimarc, Alvi)
# y sube lo leído a la rama `node-data`. La nube lo integra en cada ventana.
#
# Instalación:   powershell -ExecutionPolicy Bypass -File tools\node_windows.ps1 -Install
# Desinstalar:   powershell -ExecutionPolicy Bypass -File tools\node_windows.ps1 -Uninstall
# (La tarea programada "RadarSuperNodo" lo inicia oculto al iniciar sesión.)
param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Once,
    [string]$Stores = "unimarc,alvi",
    [int]$Minutes = 12
)
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
# carpeta en el perfil del usuario (no en AppData: algunas apps empaquetadas la redirigen)
$Base = Join-Path $env:USERPROFILE "radar-node"
$Repo = Join-Path $Base "repo"
$Venv = Join-Path $Base "venv"
$Py = Join-Path $Venv "Scripts\python.exe"
$Log = Join-Path $Base "node.log"
$TaskName = "RadarSuperNodo"
$Origin = "https://github.com/DiCloudd/radar-super-cl.git"

function Write-Log([string]$msg) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $Log -Value $line -Encoding UTF8
    if ((Test-Path $Log) -and ((Get-Item $Log).Length -gt 5MB)) {
        Move-Item -Force $Log "$Log.1"
    }
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
        Where-Object { $_.CommandLine -like "*node_windows.ps1*" -and $_.ProcessId -ne $PID } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Output "Nodo desinstalado (la carpeta $Base se conserva; puedes borrarla)."
    exit 0
}

if ($Install) {
    New-Item -ItemType Directory -Force -Path $Base | Out-Null
    if (-not (Test-Path (Join-Path $Repo ".git"))) {
        git clone -q $Origin $Repo
    }
    if (-not (Test-Path $Py)) {
        python -m venv $Venv
        & $Py -m pip install -q --upgrade pip
    }
    & $Py -m pip install -q -r (Join-Path $Repo "requirements.txt")
    $script = Join-Path $Repo "tools\node_windows.ps1"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) `
        -StartWhenAvailable -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Description "Radar de precios: nodo Chile (Unimarc, Alvi)" -Force | Out-Null
    Write-Output "Instalado. Tarea '$TaskName' creada. Log: $Log"
    exit 0
}

# ---------------- bucle del nodo ----------------
Set-Location $Repo
$env:MONITOR_RUNNER = "chile"
$env:RADAR_DB = Join-Path $Base "chile.db"
$env:PYTHONIOENCODING = "utf-8"
$Out = Join-Path $Base "out"
New-Item -ItemType Directory -Force -Path $Out | Out-Null
Write-Log "nodo iniciado (tiendas: $Stores)"

do {
    git -C $Repo pull -q --ff-only origin main 2>&1 | Out-Null
    & $Py -m radar.main window --minutes $Minutes --only $Stores 2>&1 |
        Select-String -Pattern "RESUMEN|peticiones=|BD |pausa|HTTP|bloque|dise" | ForEach-Object { Write-Log $_.Line }
    $file = Join-Path $Out "node.json.gz"
    & $Py -m radar.main node-export $file --stores $Stores 2>&1 | ForEach-Object { Write-Log $_ }

    # subir a la rama node-data (un solo commit, force-push: los datos completos viven en la nube)
    $tmp = Join-Path $Out "push"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    Copy-Item $file (Join-Path $tmp "node.json.gz")
    git -C $tmp init -q -b node-data 2>&1 | Out-Null
    git -C $tmp add node.json.gz 2>&1 | Out-Null
    git -C $tmp -c user.name="radar-node" -c user.email="radar-node@users.noreply.github.com" `
        commit -q -m ("nodo chile " + (Get-Date -Format "s")) 2>&1 | Out-Null
    $push = git -C $tmp push -q --force $Origin node-data 2>&1
    if ($LASTEXITCODE -eq 0) { Write-Log "subido a node-data" } else { Write-Log "no se pudo subir: $push" }

    if (-not $Once) { Start-Sleep -Seconds 30 }
} while (-not $Once)
