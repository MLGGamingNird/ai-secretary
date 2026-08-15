' Double-click this file to start VESPER completely silently -- no console
' window flashes, even briefly. This is also what you'll point Windows'
' Startup folder at if you want VESPER to launch automatically on boot.

Dim fso, scriptDir, pythonw, launcher, shell

Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = scriptDir & "\brain\venv\Scripts\pythonw.exe"
launcher = scriptDir & "\launcher.py"

Set shell = CreateObject("WScript.Shell")
shell.Run """" & pythonw & """ """ & launcher & """", 0, False
