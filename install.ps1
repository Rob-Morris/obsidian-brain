#!/usr/bin/env pwsh
<#
Install an Obsidian Brain vault on native Windows.

This is a thin launcher: it finds Python, asks for the few install-time choices
that are platform-specific to the shell, then hands policy to
src/brain-core/scripts/install.py.
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$VaultPath,

    [ValidateSet("project", "user", "skip")]
    [string]$McpScope,

    [ValidateSet("claude", "codex", "grok", "all")]
    [string]$Client,

    [ValidateSet("codex", "claude", "all")]
    [string]$ApprovalClient,
    [ValidateSet("user", "project", "local")]
    [string]$ApprovalScope,
    [ValidateSet("mcp", "cli", "both")]
    [string]$Approvals,

    [string]$Version,
    [switch]$ReplaceCli,
    [string]$Id,
    [string]$Launcher,
    [switch]$SkipMcp,
    [switch]$SkipCli,
    [switch]$NonInteractive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

if ($SkipCli -and $ReplaceCli) { throw "-SkipCli and -ReplaceCli cannot be combined." }

if (($ApprovalClient -or $ApprovalScope -or $Approvals) -and
    -not ($ApprovalClient -and $ApprovalScope -and $Approvals)) {
    throw "Approval opt-in requires -ApprovalClient, -ApprovalScope and -Approvals."
}

function Find-CompatiblePython {
    param([string]$ExplicitLauncher)

    $candidates = @()
    if ($ExplicitLauncher) {
        $candidates += $ExplicitLauncher
    }
    $candidates += @("python", "py", "python3.13", "python3.12")

    foreach ($candidate in $candidates) {
        try {
            $version = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if (-not $version) {
                continue
            }
            $parts = $version.Trim().Split(".")
            if ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 12)) {
                return $candidate
            }
        } catch {
            continue
        }
    }
    throw "Python 3.12+ is required. Install Python 3.12+ and rerun this installer."
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Find-CompatiblePython -ExplicitLauncher $Launcher
if ($Version -and $Version -ne "latest" -and $Version -notmatch '^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$') {
    throw "-Version requires latest or an exact X.Y.Z."
}
if (($Version -or -not (Test-Path -LiteralPath (Join-Path $repoRoot "src/brain-core/VERSION"))) -and $env:BRAIN_INSTALL_ACQUIRED -ne $repoRoot) {
    $acquired = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString())
    $selectedVersion = if ($Version) { $Version } else { "latest" }
    $ref = if ($selectedVersion -eq "latest") { "main" } else { "v$selectedVersion" }
    $acquisitionCompleted = $false
    try {
        & git clone --branch $ref --depth 1 --quiet "https://github.com/rob-morris/obsidian-brain.git" $acquired
        if ($LASTEXITCODE -ne 0) { throw "Release source acquisition failed." }
        $acquisition = Join-Path $acquired "cli\_acquisition.py"
        if (-not (Test-Path -LiteralPath $acquisition)) { throw "Historical verified installation is best-effort; use an explicit local checkout." }
        & $python $acquisition $selectedVersion $acquired --certify-checkout
        if ($LASTEXITCODE -ne 0) { throw "Release source verification failed." }
        $env:BRAIN_INSTALL_ACQUIRED = $acquired
        & (Join-Path $acquired "install.ps1") @PSBoundParameters
        $acquisitionCompleted = $LASTEXITCODE -eq 0
        exit $LASTEXITCODE
    } finally {
        Remove-Item Env:BRAIN_INSTALL_ACQUIRED -ErrorAction SilentlyContinue
        if ($acquisitionCompleted -or -not (Test-Path -LiteralPath (Join-Path $acquired ".brain-source-receipt.json"))) {
            if (Test-Path -LiteralPath $acquired) { Remove-Item -LiteralPath $acquired -Recurse -Force }
        } else {
            Write-Warning "Retained verified source at $acquired. Retry its install.ps1 with the same options and without -Version; this reuses the pinned source."
        }
    }
}
if (Test-Path -LiteralPath (Join-Path $repoRoot ".brain-source-receipt.json")) {
    & $python (Join-Path $repoRoot "cli/_acquisition.py") latest $repoRoot --verify-retained
    if ($LASTEXITCODE -ne 0) { throw "Retained source verification failed." }
}
$currentDefaultBrain = ""
$registryScript = Join-Path $repoRoot "src\brain-core\scripts\vault_registry.py"
if (Test-Path -LiteralPath $registryScript) {
    try {
        $defaultOutput = & $python $registryScript --get-default 2>$null
        if ($defaultOutput) {
            $currentDefaultBrain = ($defaultOutput | Select-Object -First 1).ToString().Trim()
        }
    } catch {
        $currentDefaultBrain = ""
    }
}

if (-not $VaultPath) {
    if ($NonInteractive) {
        $VaultPath = (Get-Location).Path
    } else {
        $defaultPath = (Get-Location).Path
        $inputPath = Read-Host "Where should your brain live? [$defaultPath]"
        $VaultPath = if ($inputPath) { $inputPath } else { $defaultPath }
    }
}

if (Test-Path -LiteralPath (Join-Path $VaultPath ".brain-core")) {
    $upgradeArgs = @((Join-Path $repoRoot "src/brain-core/scripts/upgrade.py"), "--source", (Join-Path $repoRoot "src/brain-core"), "--vault", $VaultPath)
    if ($NonInteractive) { $upgradeArgs += "--unattended" }
    if ($ReplaceCli) { $upgradeArgs += "--replace-cli" }
    & $python @upgradeArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if ($Approvals) {
        $surfaces = if ($Approvals -eq "both") { @("mcp", "cli") } else { @($Approvals) }
        & $python (Join-Path $repoRoot "src/brain-core/scripts/configure.py") approvals --client $ApprovalClient --scope $ApprovalScope --workspace $VaultPath --surfaces @surfaces
        exit $LASTEXITCODE
    }
    exit 0
}


if ($SkipMcp) {
    $McpScope = "skip"
} elseif (-not $McpScope) {
    if ($NonInteractive) {
        $McpScope = "project"
    } else {
        Write-Host ""
        Write-Host "How would you like to register this Brain MCP server?"
        Write-Host "  1) This vault only  (project scope - recommended)"
        if ($currentDefaultBrain) {
            Write-Host "  2) Override current default brain ($currentDefaultBrain) with this vault  (user scope)"
        } else {
            Write-Host "  2) Make this your default brain  (user scope)"
        }
        Write-Host "  3) Skip MCP registration  (the managed runtime and CLI are still installed)"
        $choice = Read-Host "Choice [1]"
        switch ($choice) {
            "2" { $McpScope = "user" }
            "3" { $McpScope = "skip" }
            default { $McpScope = "project" }
        }
    }
}

if (-not $Client) {
    if ($McpScope -eq "skip") { $Client = "all" }
    elseif ($NonInteractive) { throw "MCP setup requires -Client claude|codex|grok|all (or -SkipMcp)." }
    else { $Client = Read-Host "MCP client: claude, codex, grok, or all (All supported clients)" }
}


if (-not $SkipCli) {
    $localRoot = if ($env:LOCALAPPDATA) {
        Join-Path $env:LOCALAPPDATA "Programs\Brain"
    } else {
        Join-Path $HOME ".local"
    }
    $cliTarget = Join-Path $localRoot "bin\brain.cmd"
    if (-not $ReplaceCli) {
        $existingCli = Get-Command brain -ErrorAction SilentlyContinue
        if ($existingCli -and $existingCli.Source) { $cliTarget = $existingCli.Source }
    }
    $distributionInstaller = Join-Path $repoRoot "cli\_distribution.py"
    $cliPolicy = if ($ReplaceCli) { @() } else { @("--preserve-existing") }
    & $python $distributionInstaller $repoRoot $cliTarget @cliPolicy
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "The Brain CLI distribution failed to install; vault installation has not started. Retry from the same source checkout with Python 3.12+ and cli/_distribution.py, or repair the existing CLI explicitly."
        exit 1
    }
    Write-Host "Installed Brain CLI distribution: $cliTarget"
    $env:PATH = (Split-Path -Parent $cliTarget) + ";" + $env:PATH
    $cliDirectory = Split-Path -Parent $cliTarget
    if (($env:PATH -split ';') -notcontains $cliDirectory) {
        Write-Warning "$cliDirectory is not on PATH. Add it to your user PATH to run 'brain'."
    }
}

$installScript = Join-Path $repoRoot "src\brain-core\scripts\install.py"

$argsList = @(
    $installScript,
    $VaultPath,
    "--source-root", $repoRoot,
    "--launcher", $python,
    "--mcp-scope", $McpScope,
    "--client", $Client
)
if ($SkipCli) { $argsList += "--skip-cli" }
if ($Id) {
    $argsList += @("--id", $Id)
}
if ($ApprovalClient -or $ApprovalScope -or $Approvals) {
    $argsList += @("--approval-client", $ApprovalClient, "--approval-scope", $ApprovalScope, "--approvals", $Approvals)
}

& $python @argsList
$exitCode = $LASTEXITCODE
if ($exitCode -eq 2) {
    exit 2
}
if ($exitCode -eq 1) {
    Write-Warning "Brain install completed with follow-up work. Review the notes above."
    $exitCode = 0
}
if ($exitCode -ne 0) {
    exit $exitCode
}

exit 0
