#requires -Version 5.1
<#
.SYNOPSIS
  Instala/desinstala un proceso de presencia Harness para MiniMax Code en Windows.

.DESCRIPTION
  MiniMax Code puede arrancar el servidor MCP stdio solo durante un turno.
  Este script crea un wrapper que mantiene el WebSocket/heartbeat del bridge
  Harness con `--presence`, y lo lanza automaticamente al iniciar sesion.

.PARAMETER McpConfig
  Ruta al mcp.json de MiniMax Code. Por defecto: $env:USERPROFILE\.minimax\mcp.json

.PARAMETER Python
  Ejecutable de Python. Por defecto: python

.PARAMETER Uninstall
  Elimina el arranque automatico y detiene el proceso presence.

.PARAMETER DryRun
  Muestra lo que haria sin escribir archivos ni detener procesos.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install_minimax_presence.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install_minimax_presence.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [string]$McpConfig = (Join-Path $env:USERPROFILE ".minimax\mcp.json"),
    [string]$Python = "python",
    [switch]$Uninstall,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$InstallDir = Join-Path $env:LOCALAPPDATA "Harness2"
$WrapperPath = Join-Path $InstallDir "minimax-presence.ps1"
$LogPath = Join-Path $InstallDir "minimax-presence.log"
$StartupDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
$StartupCmd = Join-Path $StartupDir "Harness2-MiniMax-Presence.cmd"

function Stop-PresenceProcess {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*harness_mcp_bridge.py*--presence*" } |
        ForEach-Object {
            Write-Host "Deteniendo presence PID $($_.ProcessId)"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

if ($Uninstall) {
    if ($DryRun) {
        Write-Host "[dry-run] Eliminaria $StartupCmd"
        Write-Host "[dry-run] Eliminaria $WrapperPath"
        Write-Host "[dry-run] Detendria procesos bridge --presence"
        return
    }
    if (Test-Path -LiteralPath $StartupCmd) { Remove-Item -LiteralPath $StartupCmd -Force }
    if (Test-Path -LiteralPath $WrapperPath) { Remove-Item -LiteralPath $WrapperPath -Force }
    Stop-PresenceProcess
    Write-Host "Presence de MiniMax Code desinstalado."
    return
}

if (-not (Test-Path -LiteralPath $McpConfig)) {
    throw "No existe el mcp.json de MiniMax Code: $McpConfig"
}

$config = Get-Content -Raw -LiteralPath $McpConfig | ConvertFrom-Json
$server = $config.mcpServers.harness
if (-not $server) {
    throw "No existe mcpServers.harness en $McpConfig"
}
if (-not $server.args -or $server.args.Count -lt 1) {
    throw "mcpServers.harness.args esta vacio en $McpConfig"
}
$bridge = [string]$server.args[0]
if (-not [System.IO.Path]::IsPathRooted($bridge)) {
    throw "MiniMax Code resuelve args contra el home del usuario. Usa una ruta absoluta al harness_mcp_bridge.py en $McpConfig"
}
if (-not (Test-Path -LiteralPath $bridge)) {
    throw "No existe el bridge en la ruta indicada: $bridge"
}
if (-not $server.env -or -not $server.env.HARNESS_SESSION_ID -or -not $server.env.HARNESS_SESSION_KEY) {
    throw "Faltan HARNESS_SESSION_ID / HARNESS_SESSION_KEY en mcpServers.harness.env"
}

$wrapper = @'
$ErrorActionPreference = "Stop"
$mutex = New-Object System.Threading.Mutex($false, "Global\Harness2MiniMaxPresence")
if (-not $mutex.WaitOne(0)) { exit 0 }
try {
    $config = Get-Content -Raw -LiteralPath "__MCP_CONFIG__" | ConvertFrom-Json
    $server = $config.mcpServers.harness
    if (-not $server) { exit 2 }
    foreach ($item in $server.env.PSObject.Properties) {
        Set-Item -Path ("Env:" + $item.Name) -Value $item.Value
    }
    $bridge = [string]$server.args[0]
    if (-not (Test-Path -LiteralPath $bridge)) { exit 3 }
    & "__PYTHON__" $bridge --presence *>> "__LOG_PATH__"
}
finally {
    try { $mutex.ReleaseMutex() } catch {}
}
'@
$wrapper = $wrapper.Replace("__MCP_CONFIG__", $McpConfig).Replace("__PYTHON__", $Python).Replace("__LOG_PATH__", $LogPath)

$startup = @"
@echo off
start "" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$WrapperPath"
"@

if ($DryRun) {
    Write-Host "[dry-run] Crearia $InstallDir"
    Write-Host "[dry-run] Escribiria wrapper en $WrapperPath"
    Write-Host "[dry-run] Escribiria arranque en $StartupCmd"
    Write-Host "[dry-run] Bridge: $bridge"
    Write-Host "[dry-run] Log: $LogPath"
    return
}

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
Set-Content -LiteralPath $WrapperPath -Value $wrapper -Encoding UTF8
New-Item -ItemType Directory -Path $StartupDir -Force | Out-Null
Set-Content -LiteralPath $StartupCmd -Value $startup -Encoding ASCII

Write-Host "Presence instalado."
Write-Host "  Wrapper: $WrapperPath"
Write-Host "  Arranque: $StartupCmd"
Write-Host "  Log: $LogPath"
Write-Host ""
Write-Host "Se ejecutara en el proximo inicio de sesion. Para iniciarlo ahora:"
Write-Host "  powershell -NoProfile -ExecutionPolicy Bypass -File `"$WrapperPath`""
