@echo off
rem === Shin Super Robot Taisen Korean patch v1.0.1 ===
rem ASCII-only launcher. All Korean messages are printed by apply.ps1.
rem Double-click this file, or drag the retail "(Track 1).bin" onto it.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0apply.ps1" %*
rem Keep the window open no matter what (even if apply.ps1 fails to start).
pause >nul
