' Double-click this file to open SmartDoc without a console window.
Option Explicit
Dim shell, files, folder, python, command, registry, candidate, result
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
folder = files.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder
python = ""

' Prefer a per-user Python installation; fall back to the Python launcher/PATH.
On Error Resume Next
For Each registry In Array("HKCU\Software\Python\PythonCore\3.14\InstallPath\", "HKCU\Software\Python\PythonCore\3.13\InstallPath\", "HKCU\Software\Python\PythonCore\3.12\InstallPath\", "HKCU\Software\Python\PythonCore\3.11\InstallPath\", "HKCU\Software\Python\PythonCore\3.10\InstallPath\")
    Err.Clear
    candidate = shell.RegRead(registry) & "pythonw.exe"
    If Err.Number = 0 And files.FileExists(candidate) Then
        python = """" & candidate & """"
        Exit For
    End If
Next
If python = "" Then
    For Each candidate In Array("pyw -3", "pythonw")
        Err.Clear
        result = shell.Run(candidate & " -c ""import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)""", 0, True)
        If Err.Number = 0 And result = 0 Then
            python = candidate
            Exit For
        End If
    Next
End If
If python = "" Then
    MsgBox "Python 3.10 or newer was not found. Run setup.bat first.", vbExclamation, "SmartDoc"
    WScript.Quit 1
End If
Err.Clear
command = python & " -B """ & folder & "\launch.pyw"""
shell.Run command, 0, False
If Err.Number <> 0 Then
    MsgBox "SmartDoc could not open. Run setup.bat to check your installation.", vbExclamation, "SmartDoc"
End If
