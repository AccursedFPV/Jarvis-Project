' Always-listening Jarvis at Windows login, with no console window.
'
'  1. Copy this file into your Startup folder: press Win+R, type
'       shell:startup
'     and press Enter.
'  2. Make sure the `jarvis` command is on PATH (installed with pip).
'  3. To stop it, open Task Manager and end the pythonw.exe / jarvis task.
'
' If `jarvis` is not on PATH, replace the command below with the full path,
' for example: "C:\Users\you\AppData\Local\Programs\Python\Python312\Scripts\jarvis.exe" --listen

Set shell = CreateObject("WScript.Shell")
shell.Run "jarvis --listen", 0, False
