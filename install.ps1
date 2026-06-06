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

    [ValidateSet("claude", "codex", "all")]
    [string]$Client = "all",

    [string]$Id,
    [string]$Launcher,
    [switch]$SkipMcp,
    [switch]$NonInteractive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

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
        Write-Host "  3) Skip MCP registration  (scaffold only)"
        $choice = Read-Host "Choice [1]"
        switch ($choice) {
            "2" { $McpScope = "user" }
            "3" { $McpScope = "skip" }
            default { $McpScope = "project" }
        }
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
if ($Id) {
    $argsList += @("--id", $Id)
}

& $python @argsList
$exitCode = $LASTEXITCODE
if ($exitCode -eq 2) {
    exit 2
}
if ($exitCode -eq 1) {
    Write-Warning "Brain install completed with follow-up work. Review the notes above."
    exit 0
}
exit $exitCode
