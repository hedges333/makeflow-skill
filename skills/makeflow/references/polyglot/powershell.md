# Polyglot Stack Guide: PowerShell

This guide covers detection markers, test and lint commands, smoke test templates, and isolation rules for PowerShell.

## 1. Detection Markers

Look for these files when scanning a PowerShell module or script directory:

- `*.ps1`, `*.psm1`, `*.psd1`

## 2. Standard Commands

Run these commands with `pwsh`:

- Targeted Test: `pwsh -Command "Invoke-Pester -Path <path> -Output Detailed"`
- Lint: `pwsh -Command "Invoke-ScriptAnalyzer -Path . -Recurse -EnableExit"`

## 3. Smoke Test Template

If no Pester suite exists, create `tests/Smoke.Tests.ps1`:

```powershell
Describe 'Smoke Test Harness' {
    It 'Runs cleanly' {
        $true | Should -Be $true
    }
}
```

## 4. Test Isolation

Follow three isolation rules in PowerShell tests:

1. Use the `Mock` command in Pester to stub external cmdlets such as `Invoke-RestMethod` or `Send-MailMessage`.
2. Use Pester `TestDrive:` or temporary `PSDrive` roots for file operations during tests.
3. Remove temporary variables and drives inside the `AfterAll` block.

## Idiomatic Directory and Module Structure

- **Library and Domain Layer**: Place exported cmdlets in `Public/*.ps1` and internal helpers in `Private/*.ps1`. Dot-source both folders from `<Module>.psm1`.
- **Executable Entrypoints and Controllers**: Export only `Public/` functions in `FunctionsToExport` inside `<Module>.psd1` and keep automation wrappers in `scripts/` thin.
- **Test Suite Placement and Naming**: Place Pester v5 suites in `tests/` using the `*.Tests.ps1` suffix so `Invoke-Pester` discovers them.
- **Structural Anti-Patterns to Flag at Target 15**: Flag wildcard `FunctionsToExport = '*'` in manifests, top-level execution side effects on `Import-Module`, or unapproved verbs.

## Code Documentation Standards and Tree Assembly

- **Project Convention Detection**: Inspect `*.psd1` manifests and `platyPS` markdown folders for help conventions at Target 15 (`SCAR-PROC-159`).
- **Idiomatic Doc Comment Standard**: Write PowerShell Comment-Based Help blocks (`<# .SYNOPSIS .DESCRIPTION .PARAMETER .EXAMPLE #>`) inside exported cmdlets.
- **Documentation Tree Assembly Command**: Run `Get-Help <Cmdlet> -Full` and `platyPS` (`New-MarkdownHelp`) to assemble and verify the module documentation tree.
- **Documentation Anti-Patterns to Flag at Target 40**: Flag `.PARAMETER` tags that do not match `param()` blocks or exported cmdlets missing `.SYNOPSIS` and `.EXAMPLE`.
