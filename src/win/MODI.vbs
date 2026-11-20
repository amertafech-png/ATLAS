' MODI launcher - runs MODI.ps1 completely hidden (no console flash).
'   MODI.vbs            start bridge if needed, open the MODI window
'   MODI.vbs stop       stop the bridge
'   MODI.vbs bridge     start only the bridge (used by "start at login")
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
mode = "Launch"
If WScript.Arguments.Count > 0 Then
  a = LCase(WScript.Arguments(0))
  If a = "stop" Then mode = "Stop"
  If a = "bridge" Then mode = "Bridge"
End If
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & dir & "\MODI.ps1"" -Mode " & mode, 0, False
