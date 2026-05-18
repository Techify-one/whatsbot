@echo off
echo Stopping WhatsBot...

taskkill /F /IM gowa.exe >nul 2>&1

:: Achar todos os processos na porta 8080, matar filhos e depois os pais
powershell -Command ^
  "$pids = @(netstat -ano | Select-String 'LISTENING' | Select-String ':8080 ' | ForEach-Object { ($_ -split '\s+')[-1] } | Select-Object -Unique); " ^
  "foreach ($p in $pids) { " ^
  "  Get-CimInstance Win32_Process -Filter \"ParentProcessId=$p\" | ForEach-Object { taskkill /F /PID $_.ProcessId 2>&1 | Out-Null }; " ^
  "  taskkill /F /PID $p 2>&1 | Out-Null " ^
  "}" >nul 2>&1

echo WhatsBot stopped.
timeout /t 2 /nobreak >nul
