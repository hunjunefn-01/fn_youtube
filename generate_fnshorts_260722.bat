@echo off
cd /d D:\VENV\Fn_Comp_Shorts_v5

:: 디폴트 조건으로 매일 오전/오후 규칙적 실행

:: 첫 번째 Python 스크립트 실행
D:\VENV\Fn_Comp_Shorts_v5\Scripts\python.exe 01_generate_video.py 0.04 --limit 0

:: 30초 대기
timeout /t 15 /nobreak

D:\VENV\Fn_Comp_Shorts_v5\Scripts\python.exe 02_upload_private.py

:: 5초 대기
timeout /t 5 /nobreak

D:\VENV\Fn_Comp_Shorts_v5\Scripts\python.exe 03_publish_video.py

pause