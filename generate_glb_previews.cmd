@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
python "%SCRIPT_DIR%scripts\batch_glb_previews.py" %*
endlocal
