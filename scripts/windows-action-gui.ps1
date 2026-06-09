# Lightweight Windows GUI wrapper for IvyeaOps update/stop actions.

param(
    [ValidateSet("update", "stop")]
    [string]$Mode = "update"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ScriptPath = if ($Mode -eq "update") {
    Join-Path $RepoRoot "scripts\update-exe.ps1"
} else {
    Join-Path $RepoRoot "scripts\stop-hidden.ps1"
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

[System.Windows.Forms.Application]::EnableVisualStyles()

$title = if ($Mode -eq "update") { "IvyeaOps Update" } else { "Stop IvyeaOps" }
$subtitle = if ($Mode -eq "update") {
    "Updating program files while keeping your data and config."
} else {
    "Stopping the background service."
}

$form = New-Object System.Windows.Forms.Form
$form.Text = $title
$form.StartPosition = "CenterScreen"
$form.Size = New-Object System.Drawing.Size(560, 360)
$form.MinimumSize = New-Object System.Drawing.Size(520, 320)
$form.BackColor = [System.Drawing.Color]::FromArgb(18, 24, 32)
$form.ForeColor = [System.Drawing.Color]::White
$form.Font = New-Object System.Drawing.Font("Segoe UI", 9)

$header = New-Object System.Windows.Forms.Panel
$header.Dock = "Top"
$header.Height = 86
$header.BackColor = [System.Drawing.Color]::FromArgb(22, 31, 42)
$form.Controls.Add($header)

$mark = New-Object System.Windows.Forms.Label
$mark.Text = "◆"
$mark.ForeColor = [System.Drawing.Color]::FromArgb(74, 222, 128)
$mark.Font = New-Object System.Drawing.Font("Segoe UI", 24, [System.Drawing.FontStyle]::Bold)
$mark.Location = New-Object System.Drawing.Point(22, 20)
$mark.Size = New-Object System.Drawing.Size(40, 42)
$header.Controls.Add($mark)

$titleLabel = New-Object System.Windows.Forms.Label
$titleLabel.Text = $title
$titleLabel.ForeColor = [System.Drawing.Color]::White
$titleLabel.Font = New-Object System.Drawing.Font("Segoe UI", 15, [System.Drawing.FontStyle]::Bold)
$titleLabel.Location = New-Object System.Drawing.Point(68, 18)
$titleLabel.Size = New-Object System.Drawing.Size(440, 28)
$header.Controls.Add($titleLabel)

$subLabel = New-Object System.Windows.Forms.Label
$subLabel.Text = $subtitle
$subLabel.ForeColor = [System.Drawing.Color]::FromArgb(170, 184, 200)
$subLabel.Location = New-Object System.Drawing.Point(70, 49)
$subLabel.Size = New-Object System.Drawing.Size(460, 22)
$header.Controls.Add($subLabel)

$progress = New-Object System.Windows.Forms.ProgressBar
$progress.Style = "Marquee"
$progress.MarqueeAnimationSpeed = 28
$progress.Location = New-Object System.Drawing.Point(20, 104)
$progress.Anchor = "Top,Left,Right"
$progress.Size = New-Object System.Drawing.Size(504, 10)
$form.Controls.Add($progress)

$log = New-Object System.Windows.Forms.TextBox
$log.Multiline = $true
$log.ReadOnly = $true
$log.ScrollBars = "Vertical"
$log.BorderStyle = "FixedSingle"
$log.BackColor = [System.Drawing.Color]::FromArgb(11, 15, 20)
$log.ForeColor = [System.Drawing.Color]::FromArgb(210, 220, 230)
$log.Font = New-Object System.Drawing.Font("Consolas", 9)
$log.Location = New-Object System.Drawing.Point(20, 128)
$log.Anchor = "Top,Bottom,Left,Right"
$log.Size = New-Object System.Drawing.Size(504, 150)
$form.Controls.Add($log)

$closeButton = New-Object System.Windows.Forms.Button
$closeButton.Text = "Close"
$closeButton.Enabled = $false
$closeButton.Anchor = "Bottom,Right"
$closeButton.Size = New-Object System.Drawing.Size(96, 30)
$closeButton.Location = New-Object System.Drawing.Point(428, 292)
$closeButton.FlatStyle = "Flat"
$closeButton.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(74, 222, 128)
$closeButton.ForeColor = [System.Drawing.Color]::White
$closeButton.BackColor = [System.Drawing.Color]::FromArgb(30, 41, 54)
$closeButton.Add_Click({ $form.Close() })
$form.Controls.Add($closeButton)

function Append-Log($text) {
    if ($form.IsDisposed) { return }
    $form.BeginInvoke([System.Action]{
        $log.AppendText($text + [Environment]::NewLine)
        $log.SelectionStart = $log.TextLength
        $log.ScrollToCaret()
    }) | Out-Null
}

$form.Add_Shown({
    Append-Log "[IvyeaOps] Starting $Mode..."
    if (-not (Test-Path $ScriptPath)) {
        Append-Log "[IvyeaOps] ERROR: script not found: $ScriptPath"
        $progress.Style = "Blocks"
        $progress.Value = 0
        $closeButton.Enabled = $true
        return
    }

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "powershell.exe"
    $args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$ScriptPath`"")
    if ($Mode -eq "update") { $args += "-NonInteractive" }
    $psi.Arguments = ($args -join " ")
    $psi.WorkingDirectory = $RepoRoot
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $psi.EnvironmentVariables["IVYEAOPS_NONINTERACTIVE"] = "1"

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    $proc.EnableRaisingEvents = $true
    $proc.add_OutputDataReceived({ if ($_.Data) { Append-Log $_.Data } })
    $proc.add_ErrorDataReceived({ if ($_.Data) { Append-Log $_.Data } })
    $proc.add_Exited({
        $code = $proc.ExitCode
        $form.BeginInvoke([System.Action]{
            $progress.Style = "Blocks"
            $progress.Value = if ($code -eq 0) { 100 } else { 0 }
            if ($code -eq 0) {
                Append-Log "[IvyeaOps] Done."
                $subLabel.Text = if ($Mode -eq "update") { "Update complete." } else { "Service stopped." }
            } else {
                Append-Log "[IvyeaOps] ERROR: exited with code $code"
                $subLabel.Text = "Action failed. Review the log above."
            }
            $closeButton.Enabled = $true
        }) | Out-Null
    })

    try {
        [void]$proc.Start()
        $proc.BeginOutputReadLine()
        $proc.BeginErrorReadLine()
    } catch {
        Append-Log "[IvyeaOps] ERROR: $_"
        $progress.Style = "Blocks"
        $progress.Value = 0
        $closeButton.Enabled = $true
    }
})

[void][System.Windows.Forms.Application]::Run($form)
