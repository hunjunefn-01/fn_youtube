import os
import time
import logging
import argparse
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options

import gc

# 사용자의 평소 개인 Chrome 프로필과 분리된, 이 자동화 전용 영구 프로필.
# --setup-login 으로 한 번 로그인해두면 이후 실행은 저장된 세션(쿠키)을 재사용해
# 매번 아이디/비밀번호를 자동 입력하지 않는다 (구글의 봇 탐지로 인한 CAPTCHA/2단계인증 유발 방지).
CHROME_PROFILE_DIR = str(Path(__file__).resolve().parent / "chrome_profile")

STUDIO_URL = (
    "https://studio.youtube.com/channel/UC9HdpN9rY_fFMQZoDdfgCIA/videos/upload"
    "?filter=%5B%5D&sort=%7B%22columnType%22%3A%22date%22%2C%22sortOrder%22%3A%22DESCENDING%22%7D"
)


def get_today_paths():
    today = datetime.today().strftime('%Y%m%d')
    am_or_pm = 'am' if datetime.now().hour < 12 else 'pm'
    return today, am_or_pm


def setup_logging(today, am_or_pm):
    """콘솔 + ./output/{date}_{am_or_pm}/run.log에 로그를 남긴다 (01/03과 동일 파일에 이어서 기록)."""
    log_dir = Path(f"./output/{today}_{am_or_pm}")
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler(log_dir / "run.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logging.getLogger().addHandler(file_handler)


def get_video_files(today, am_or_pm):
    """오늘 날짜 + am/pm 기준, (.\\output\\YYYYMMDD_am/pm\\video)에서 mp4 리스트 수집"""
    folder = Path(f'.\\output\\{today}_{am_or_pm}\\video')

    if not folder.exists():
        logging.info(f"업로드 대상 폴더가 없습니다: {folder}")
        return []

    return [str((folder / f).resolve()) for f in os.listdir(folder) if f.lower().endswith('.mp4')]


def build_driver():
    options = Options()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(f"--user-data-dir={CHROME_PROFILE_DIR}")
    return webdriver.Chrome(service=Service(), options=options)


def _at_studio_home(driver):
    """스튜디오 홈 도달 여부: #create-icon 요소가 있거나, URL이 이미 로그인 화면이 아닌
    studio.youtube.com인 경우를 스튜디오 도달로 판단한다."""
    return bool(
        driver.find_elements(By.CSS_SELECTOR, "#create-icon")
        or ("studio.youtube.com" in driver.current_url and "accounts.google.com" not in driver.current_url)
    )


def try_return_to_studio(driver, timeout=8):
    """중간 인터스티셜 페이지가 떴을 때 'Return to studio' 처리 (여러 후보 셀렉터 시도)"""
    candidates = [
        (By.XPATH, "//*[normalize-space(text())='Return to studio']"),
        (By.XPATH, "//*[normalize-space(text())='스튜디오로 돌아가기']"),
        (By.XPATH, "//a[contains(@href,'studio.youtube.com')]"),
    ]
    for by, sel in candidates:
        try:
            btn = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((by, sel))
            )
            btn.click()
            # 스튜디오 홈 도달 확인
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#create-icon"))
            )
            logging.info("'Return to studio' 처리 완료.")
            return True
        except Exception:
            pass
    return False


def upload_file(driver, file_path: str, screenshot_dir: Path) -> bool:
    """단일 mp4 파일 업로드 후 draft 저장. 성공 시 True, 실패 시 False 반환."""
    logging.info(f"Uploading: {file_path}")

    # 1) 만들기 버튼 클릭
    try:
        create_btn = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "ytcp-button.ytcpAppHeaderCreateIcon button"))
        )
        driver.execute_script("arguments[0].click();", create_btn)
        logging.info("'Create' 버튼 클릭 완료.")
    except Exception as e:
        logging.error(f"'Create' 버튼 클릭 실패: {e}")
        driver.save_screenshot(str(screenshot_dir / "error_create_click.png"))
        return False

    # 2) 'Upload videos' 메뉴 클릭
    try:
        upload_menu = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//*[normalize-space(text())='Upload videos' or normalize-space(text())='동영상 업로드']"
            ))
        )
        driver.execute_script("arguments[0].click();", upload_menu)
        logging.info("'Upload videos' 메뉴 클릭 완료.")
    except Exception as e:
        logging.error(f"'Upload videos' 메뉴 클릭 실패: {e}")
        driver.save_screenshot(str(screenshot_dir / "error_upload_menu.png"))
        return False

    # 3) 업로드 다이얼로그 대기
    try:
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "ytcp-uploads-dialog, ytcp-single-file-uploader")
            )
        )
        logging.info("업로드 다이얼로그 감지 완료.")
    except TimeoutException:
        logging.error("업로드 다이얼로그를 찾지 못했습니다.")
        driver.save_screenshot(str(screenshot_dir / "error_no_dialog.png"))
        return False

    # 4) Select files 버튼 클릭
    try:
        select_btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//*[normalize-space(text())='Select files' or normalize-space(text())='파일 선택']"
            ))
        )
        driver.execute_script("arguments[0].click();", select_btn)
        logging.info("'Select files' 버튼 클릭 완료.")
    except Exception as e:
        logging.warning(f"'Select files' 버튼 클릭 실패: {e}")

    # 5) 파일 입력 요소 찾기
    try:
        file_input = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='file']"))
        )
        file_input.send_keys(str(Path(file_path)))
        logging.info("파일 전송 완료. 업로드 진행 중...")
    except Exception as e:
        logging.error(f"파일 입력 실패: {e}")
        driver.save_screenshot(str(screenshot_dir / "error_file_input.png"))
        return False

    # 6) 업로드 완료 대기
    try:
        WebDriverWait(driver, 180).until(
            EC.presence_of_element_located((
                By.XPATH,
                "//*[contains(., '완료') or contains(., 'Checks complete') or contains(., 'Upload complete')]"
            ))
        )
        logging.info("업로드 완료 감지.")
    except TimeoutException:
        logging.warning("업로드 완료 신호 미탐지 - 계속 진행합니다.")

    # 7) 닫기 버튼 클릭 (후보 셀렉터 여러 개 시도)
    close_candidates = [
        (By.CSS_SELECTOR, "ytcp-uploads-dialog #ytcp-uploads-dialog-close-button button"),
        (By.XPATH, "//ytcp-uploads-dialog//button[@aria-label='Close' or @aria-label='닫기']"),
    ]
    closed = False
    for by, sel in close_candidates:
        try:
            close_btn = WebDriverWait(driver, 8).until(EC.element_to_be_clickable((by, sel)))
            driver.execute_script("arguments[0].click();", close_btn)
            closed = True
            break
        except Exception:
            continue

    if closed:
        logging.info(f"Draft saved for: {file_path}")
    else:
        # 닫기에 실패해도 업로드 자체는 완료된 상태. 다이얼로그가 열린 채로 남으면 다음 파일 처리를
        # 막을 수 있으므로, main()이 다음 파일 전에 스튜디오 페이지를 새로고침해 복구한다.
        logging.warning(f"닫기 버튼을 찾지 못했습니다 (업로드는 완료됨): {file_path}")

    return True


def run_login_setup():
    """최초 1회 실행: 영구 프로필로 브라우저를 띄워 사용자가 직접 로그인하도록 함."""
    Path(CHROME_PROFILE_DIR).mkdir(parents=True, exist_ok=True)
    driver = build_driver()
    try:
        driver.get(STUDIO_URL)
        logging.info("브라우저 창에서 구글 로그인을 완료해주세요 (2단계 인증 포함).")
        logging.info("로그인이 끝나면 스튜디오 화면 도달을 자동으로 감지합니다 (최대 5분 대기).")
        WebDriverWait(driver, 300).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#create-icon"))
        )
        logging.info("로그인 세션 저장 완료. 이제부터는 --setup-login 없이 실행하면 됩니다.")
    except TimeoutException:
        logging.error("5분 내에 스튜디오 화면에 도달하지 못했습니다. 로그인을 완료했는지 확인 후 다시 시도해주세요.")
    finally:
        time.sleep(2)
        driver.quit()


def main(today, am_or_pm):
    setup_logging(today, am_or_pm)

    video_files = get_video_files(today, am_or_pm)
    if not video_files:
        logging.info("업로드할 mp4 파일이 없습니다.")
        return

    if not Path(CHROME_PROFILE_DIR).exists():
        logging.error(
            "저장된 로그인 세션이 없습니다. "
            "먼저 'python 02_upload_private.py --setup-login'을 한 번 실행해 로그인을 완료해주세요."
        )
        return

    screenshot_dir = Path(f"./output/{today}_{am_or_pm}/screenshots")
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    driver = None
    success_count = 0
    try:
        driver = build_driver()
        driver.get(STUDIO_URL)

        # 스튜디오 홈 도달 여부(_at_studio_home) 또는 로그인 폼(#identifierId) 등장을 함께 확인.
        try:
            WebDriverWait(driver, 45).until(
                lambda d: _at_studio_home(d) or d.find_elements(By.ID, "identifierId")
            )
        except TimeoutException:
            logging.error("스튜디오 페이지 로딩을 확인하지 못했습니다 (네트워크 상태를 확인해주세요).")
            return

        if driver.find_elements(By.ID, "identifierId"):
            logging.warning("로그인 폼이 감지되었습니다. 일시적인 리디렉션인지 재확인 중...")
            try:
                WebDriverWait(driver, 30).until(_at_studio_home)
                logging.info("일시적인 리디렉션이었습니다. 저장된 세션으로 로그인 계속 진행.")
            except TimeoutException:
                logging.error(
                    "로그인 세션이 만료된 것으로 보입니다. "
                    "'python 02_upload_private.py --setup-login'을 다시 실행해 재로그인해주세요."
                )
                return

        logging.info("저장된 세션으로 로그인 완료. 스튜디오 로딩 대기 중...")
        try_return_to_studio(driver, timeout=8)

        for f in video_files:
            try:
                # 이전 파일에서 업로드 다이얼로그가 제대로 닫히지 않았을 경우를 대비해
                # 매 파일 시작 전 스튜디오 페이지를 새로고침해 항상 깨끗한 상태에서 시작한다.
                driver.get(STUDIO_URL)
                time.sleep(2)
                if upload_file(driver, f, screenshot_dir):
                    success_count += 1
                time.sleep(5)
            except Exception as e:
                logging.error(f"{Path(f).name} 업로드 실패: {e}")
                try:
                    driver.save_screenshot(str(screenshot_dir / f"error_{Path(f).stem}.png"))
                except Exception:
                    pass

        logging.info(f"업로드 완료: {success_count}/{len(video_files)}개 성공.")

    except Exception as e:
        logging.error(f"업로드 프로세스 실패: {e}")
    finally:
        if driver:
            try:
                driver.quit()
                # Selenium의 __del__이 인터프리터 종료 시 이미 닫힌 세션에 quit()을 재호출하며
                # 발생시키는 무해한 오류 메시지를 방지하기 위한 처리.
                driver.quit = lambda: None
            except Exception:
                pass
            driver = None
            gc.collect()
            logging.info("드라이버 정상 종료.")


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument('--setup-login', action='store_true',
                             help='영구 브라우저 프로필로 최초 1회 수동 로그인을 진행합니다.')
    arg_parser.add_argument('--manual', nargs=2, metavar=('DATE', 'AM_OR_PM'),
                             help="특정 날짜/세션의 업로드 폴더를 수동으로 지정 (예: --manual 20260716 am)")
    cli_args = arg_parser.parse_args()

    if cli_args.setup_login:
        setup_today, setup_am_or_pm = get_today_paths()
        setup_logging(setup_today, setup_am_or_pm)
        run_login_setup()
    else:
        if cli_args.manual:
            manual_date, manual_am_pm = cli_args.manual
            manual_am_pm = manual_am_pm.lower()
            if manual_am_pm not in ('am', 'pm'):
                raise ValueError(f"--manual의 두 번째 값은 'am' 또는 'pm'이어야 합니다: {manual_am_pm}")
            run_today, run_am_or_pm = manual_date, manual_am_pm
        else:
            run_today, run_am_or_pm = get_today_paths()
        main(run_today, run_am_or_pm)
