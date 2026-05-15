' Silent launcher for the Claude bridge (shared by LinkedIn + Upwork tools).
' Double-click to start the bridge in the background (no console window).
'
' Why the cmd wrapper instead of plain pythonw.exe:
'   - pythonw discards stdout/stderr, so when something crashes (port
'     conflict, missing dep, etc.) you have no idea why.
'   - cmd /c  python.exe  >> log  2>&1  captures everything to bridge.log
'     which you can tail to debug.
'   - The cmd window itself is hidden via objShell.Run(..., 0, False),
'     so the user still sees no console.
'
' If another instance is already listening on port 8766 the new one will
' error out (logged to bridge.log) and exit - no harm done.

Option Explicit

Dim objShell, objFSO, scriptDir, logPath, cmdline

Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

' cd to this script's folder so server.py resolves
scriptDir = objFSO.GetParentFolderName(WScript.ScriptFullName)
objShell.CurrentDirectory = scriptDir

logPath = scriptDir & "\bridge.log"

' Append a run marker so it's easy to see when each launch happened.
Dim logFile
Set logFile = objFSO.OpenTextFile(logPath, 8, True) ' 8 = ForAppending, create if missing
logFile.WriteLine ""
logFile.WriteLine "=== Bridge launch attempt at " & Now & " ==="
logFile.Close

' Use plain python.exe (not pythonw) inside a hidden cmd /c so we can
' capture stdout/stderr to bridge.log.
cmdline = "cmd /c python.exe server.py >> """ & logPath & """ 2>&1"

' 0 = hidden window, False = don't wait for exit
objShell.Run cmdline, 0, False
