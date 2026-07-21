@echo off
cd /d D:\VENV\Fn_Comp_Shorts_v5

:: 매뉴얼 옵션으로 수동 실행

:: 첫 번째 Python 스크립트 실행
.\Scripts\python.exe 01_generate_video.py --manual 20260716 am --limit 2

:: 10초 대기
timeout /t 10 /nobreak

.\Scripts\python.exe 02_upload_private.py --manual 20260716 am
:: 로그인 설정(최초 1회 또는 세션 만료 시): .\Scripts\python.exe 02_upload_private.py --setup-login

:: 5초 대기
timeout /t 5 /nobreak

.\Scripts\python.exe 03_publish_video.py --manual 20260716 am

pause