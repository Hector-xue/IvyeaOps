Set WshShell = CreateObject("WScript.Shell")
Set Fso = CreateObject("Scripting.FileSystemObject")
RepoRoot = Fso.GetParentFolderName(WScript.ScriptFullName)
ScriptPath = Fso.BuildPath(RepoRoot, "scripts\start-hidden.ps1")
Cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File " & Chr(34) & ScriptPath & Chr(34)
WshShell.Run Cmd, 0, False
