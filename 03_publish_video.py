# coding: utf-8

import os
import json
import logging
import argparse
from datetime import datetime
from pathlib import Path
import google_auth_oauthlib.flow
import googleapiclient.discovery
import googleapiclient.errors
import google.auth.transport.requests
from google.oauth2.credentials import Credentials

# https://console.cloud.google.com/auth/clients?authuser=1&hl=ko&inv=1&invt=Ab2PqA&project=shorts-438906
# 위에서 client_secret_~~.json 을 받는다
# 이름 수정 "client_secrets.json"
# Oauth 동의를 데스크톱앱으로 해야되며 테스트가 아닌 실제로 올려야 동작
# token.json은 처음에 없으며 처음 돌리면 생성된다

scopes = [
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/youtube.readonly"
]

def get_authenticated_service():
    credentials = None

    # Delete existing token.json to ensure new authentication
    #if os.path.exists('token.json'):
    #    os.remove('token.json')

    # Check if token.json exists
    if os.path.exists('token.json'): 
        with open('token.json', 'r') as token_file:
            token_info = json.load(token_file) #토큰 제이슨을 받아온다.
            credentials = Credentials.from_authorized_user_info(token_info, scopes)

    # If there are no valid credentials available, prompt the user to log in.
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(google.auth.transport.requests.Request())
        else:
            flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
                'client_secrets.json', scopes)
            credentials = flow.run_local_server(port=0)

        # Save the credentials for the next run
        with open('token.json', 'w') as token_file:
            token_file.write(credentials.to_json())

    return googleapiclient.discovery.build('youtube', 'v3', credentials=credentials)


def get_uploads_playlist_id(youtube, channel_id):
    """Fetch the uploads playlist ID for a given channel."""
    request = youtube.channels().list(
        part="contentDetails",
        id=channel_id
    )
    response = request.execute()
    
    uploads_playlist_id = response['items'][0]['contentDetails']['relatedPlaylists']['uploads']
    return uploads_playlist_id


def get_videos_from_playlist(youtube, playlist_id):
    """Retrieve all videos from the uploads playlist (최근 50개), including private ones."""
    playlist_request = youtube.playlistItems().list(
        part='snippet',
        playlistId=playlist_id,
        maxResults=50,
    )
    playlist_response = playlist_request.execute()
    items = playlist_response['items']
    if not items:
        return []

    video_ids = [item['snippet']['resourceId']['videoId'] for item in items]

    # 영상마다 개별 조회하지 않고, id를 콤마로 묶어 한 번의 videos.list 호출로 상태를 조회 (최대 50개까지 지원)
    status_request = youtube.videos().list(part='status', id=','.join(video_ids))
    status_response = status_request.execute()
    status_by_id = {v['id']: v['status'] for v in status_response.get('items', [])}

    videos = []
    for item in items:
        video_id = item['snippet']['resourceId']['videoId']
        status = status_by_id.get(video_id)
        video_status = status['privacyStatus'] if status else 'Unknown' #공개상태 확인
        is_draft = (video_status == 'private') #초안상태 확인

        videos.append({
            'id': video_id,
            'title': item['snippet']['title'],
            'uploaded_time': item['snippet']['publishedAt'],
            'status': video_status,
            'is_draft': is_draft
        })

    return videos


def get_channel_id(youtube):
    """Fetch the authenticated user's channel ID."""
    request = youtube.channels().list(
        part="id",
        mine=True
    )
    response = request.execute()

    if "items" in response and len(response["items"]) > 0:
        return response["items"][0]["id"]
    else:
        return None


def update_video(youtube, video_id, title, description, tags, category_id): #상태를 변경시킴
    request = youtube.videos().update(
        part="snippet,status",
        body={
            "id": video_id,
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": category_id
            },
            "status": {
                "privacyStatus": "public" #이 부분에서 draft상태를 변경한다.
            }
        }
    )
    response = request.execute() #실행
    return response


def extract_name_ticker(filename, is_json=True): #파일이름이나 영상 제목에서 티커 정보 추출
    if is_json:
        parts = filename.split('_') # "포바이포_123.json" → ['포바이포', '123.json']
        parts = [part.replace('.', '').replace('-', ' ').strip() for part in parts] # S-OIL, JYP ENT. 두개를 고려함

        if 2<= len(parts)<=3:
            name = parts[0]
            ticker = parts[1]
            return name, ticker
        elif len(parts) >=4:
            name = f"{parts[0]} {parts[1]}"
            ticker = parts[2]
            return name, ticker
        else:
            return None, None
        
    else:
        parts = filename.split(' ')
        parts = list(filter(None, parts))

        if 2<= len(parts)<=3:
            name = parts[0]
            ticker = parts[1]
            return name, ticker
        elif len(parts) >=4:
            name = f"{parts[0]} {parts[1]}"
            ticker = parts[2]
            return name, ticker
        else:
            return None, None

    return None, None

def main(today, am_or_pm):
    # YouTube API 인증을 위한 서비스 객체 생성
    youtube = get_authenticated_service()

    # 인증된 사용자의 채널 ID를 가져옴
    channel_id = get_channel_id(youtube)
    if not channel_id:
        logging.error("Unable to retrieve channel ID.")
        return

    # 업로드된 동영상이 포함된 플레이리스트 ID를 가져옴 #전체리스트
    uploads_playlist_id = get_uploads_playlist_id(youtube, channel_id)

    # 업로드 플레이리스트에서 동영상 목록을 가져옴 #전체리스트
    videos = get_videos_from_playlist(youtube, uploads_playlist_id)

    # 동영상의 개수를 셈
    number_of_videos = len(videos)
    logging.info(f"Total number of videos on the channel: {number_of_videos}")
    logging.info("----------------------------------------------------------------------------------")
    logging.info("Video information fetched from Youtube Channel:")

    folder_path = f'./output/{today}_{am_or_pm}/dialog'

    # 폴더가 존재하지 않으면 오류 메시지 출력 후 함수 종료
    if not os.path.exists(folder_path):
        logging.error(f"Folder path {folder_path} does not exist.")
        return

    # 폴더 내에서 .json 파일만 가져와서 리스트로 저장
    json_files = [f for f in os.listdir(folder_path) if f.endswith('.json')]

    # JSON 파일 개수를 셈
    number_of_json_files = len(json_files)
    logging.info(f"Total number of JSON files: {number_of_json_files}")

    match_count = 0  # 매칭된 동영상 수를 카운트하는 변수 초기화

    # 각 JSON 파일을 순회하며 동영상과 매칭 작업 수행
    for json_file in json_files:
        json_file_name = os.path.splitext(json_file)[0] #확장자 제거
        json_name, json_ticker = extract_name_ticker(json_file_name)
        # print("json : ", json_name, json_ticker)

        if json_name is None or json_ticker is None:
            continue  # 이름 또는 티커가 없으면 다음으로 넘어감

        # JSON 파일을 UTF-8로 읽음
        with open(os.path.join(folder_path, json_file), 'r', encoding='utf-8') as file:
            json_data = json.load(file)

        # 가져온 동영상 리스트에서 각 동영상과 비교
        for video in videos:
            video_title = os.path.splitext(video['title'])[0] #확장자 제거
            video_name, video_ticker = extract_name_ticker(video_title, is_json=False)
            # print("video : ", video_name, video_ticker)
          
            if video_name is None or video_ticker is None:
                continue  # 동영상 이름 또는 티커가 없으면 다음 동영상으로 넘어감


            # 임시 저장(draft) 상태인 동영상만 처리
            if video['is_draft']:
                # JSON 파일 이름과 동영상 이름 및 티커가 일치하는지 확인
                if json_name == video_name and json_ticker == video_ticker:
                    # 새로운 제목, 설명, 태그 등을 JSON 데이터 기반으로 설정
                    new_title = f"{json_data.get('title', video['title'])} #{today}#주식#증권정보#주식정보"
                    new_description = ' '.join(json_data.get('summary', ''))
                    #event = f"증권사 리포트 조회 서비스 [ WiseReport ]가 신규 가입 회원을 대상으로 1+1 이벤트를 진행합니다!\n\nWiseReport 서비스는?\n\n리서치기관에서 발간된 리포트 자료를 빠르게 보고자 하는 투자자를 위한 리서치 자료 플랫폼입니다. 국내 전 증권사, 은행, 운용사, 독립리서치, 국책연구기관, 기업 연구소 등 다양한 분야의 리서치기관에서 발간한 보고서를 빠르게 제공합니다.\n\n1+1 혜택 내용\n- 신규 가입 회원이 1개월 이용권 결제 시 추가 1개월 무상 제공\n- 1개월 이용권 가격: 110,000원\n\n이벤트 기간\n- 2024년 10월 14일 ~ 2024년 10월 27일\n\n참여 대상\n- 이벤트 기간 동안 WiseReport에 신규 가입한 회원\n\n더 자세한 내용은 커뮤니티 또는 https://shorturl.at/y158n 를 참조해주세요. "
                    new_description = f"{json_data.get('description', '')}\n오늘 알면 좋은 주식\n\n{new_description}\n\nhttps://t.me/oorrrzzzz\n- 자유로운 의견공유가 가능한 텔래그램방에 들어와보세요. -\n\n#{today}\n#{json_name}\n#{json_ticker}\n#{json_name}({json_ticker})\n#증권사리포트\n#주식투자\n#투자정보\n#기업분석\n#증시투자\n#투자초보\n#주식초보\n#투자팁\n#금융지식\n#개인투자\n#한국증시\n#주가분석\n#투자전략\n#기업리포트\n#증권사\n#리포트분석\n#투자유튜브\n#증권유튜브\n#주식유튜브\n#주식공부\n\n목표주가는 증권사들이 발표한 목표주가의 평균이며, 직전 목표주가는 3개월 전 평균 목표주가입니다.\n\n본 동영상의 내용은 증권사 리포트를 기반으로 인공지능(AI)에 의해 자동으로 작성된 내용이므로 그 완전성을 보장할 수 없으며, 투자를 권유하는 내용이 아닙니다. 투자에 대한 판단 책임은 본인에게 있습니다."
                    new_tags = json_data.get('keywords', [])

                    if len(new_description) > 5000:
                        logging.warning(f"Description too long for video: {video['id']} - Skipping")
                        continue
                    # 동영상 메타데이터 업데이트
                    category_id = '25'  # 카테고리 ID는 25로 설정됨
                    update_video(youtube, video['id'], new_title, new_description, new_tags, category_id)
                    match_count += 1  # 매칭된 동영상 수 증가
                    logging.info(f'Updated video: {video["id"]} with title: {new_title}')

                # 동영상 이름이 'market'이고 티커가 'preview'인 경우(시장 미리보기 영상 처리)
                elif video['is_draft'] and video_name == 'market' and video_ticker == 'preview':
                    # 시장 미리보기 JSON 파일을 확인
                    file_path = f"../Fn_Market_Preview/output/{today}/json/{today}_market_preview.json"
                    if os.path.exists(file_path):
                        # 해당 파일을 읽고 새로운 제목과 설명 설정
                        with open(file_path, 'r', encoding='utf-8') as json_file:
                            data = json.load(json_file)
                        new_title = f"{data.get('title')} #{today}#글로벌마켓#증시#해외증시#국내증시"
                        new_description = f"{data.get('content')}\n\n오늘 알면 좋은 주식\n\n#코스피\n#코스닥\n#S&P500\n#다우존스\n#나스닥\n#주식시장\n#주식투자\n#금융시장\n#증시전망\n#투자전략\n#증권사리포트\n#글로벌증시\n#경제뉴스\n#시장동향\n#주가분석\n#시장전망\n#주식초보\n#투자정보\n#금융뉴스\n#주식리뷰\n#포트폴리오전략\n#주식경제\n#해외증시\n#주식브리핑\n#국내증시\n#오늘의증시\n#증시분석\n#주가동향\n#투자뉴스\n#경제브리핑\n\n본 동영상의 내용은 인공지능(AI)에 의해 자동으로 요약된 내용이므로 그 완전성을 보장할 수 없으며, 투자를 권유하는 내용이 아닙니다. 투자에 대한 판단 책임은 본인에게 있습니다."
                        new_tags = "증시, 당신이 잠든 사이, 오늘의 증시, 국내시황, 해외증시"
                        
                        # 동영상 메타데이터 업데이트
                        category_id = '25'
                        update_video(youtube, video['id'], new_title, new_description, new_tags, category_id)
                        match_count += 1

    # 최종적으로 매칭된 동영상 수 출력
    logging.info(f"Total number of matches: {match_count} out of {number_of_json_files}")
    logging.info("Updating video titles and descriptions completed.")


def get_today_paths():
    today = datetime.today().strftime('%Y%m%d')
    am_or_pm = 'am' if datetime.now().hour < 12 else 'pm'
    return today, am_or_pm


def setup_logging(today, am_or_pm):
    """콘솔 + ./output/{date}_{am_or_pm}/run.log에 로그를 남긴다 (01/02와 동일 파일에 이어서 기록)."""
    log_dir = Path(f"./output/{today}_{am_or_pm}")
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    file_handler = logging.FileHandler(log_dir / "run.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logging.getLogger().addHandler(file_handler)


if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument('--manual', nargs=2, metavar=('DATE', 'AM_OR_PM'),
                             help="특정 날짜/세션의 dialog 폴더를 수동으로 지정 (예: --manual 20260716 am)")
    cli_args = arg_parser.parse_args()

    if cli_args.manual:
        manual_date, manual_am_pm = cli_args.manual
        manual_am_pm = manual_am_pm.lower()
        if manual_am_pm not in ('am', 'pm'):
            raise ValueError(f"--manual의 두 번째 값은 'am' 또는 'pm'이어야 합니다: {manual_am_pm}")
        run_today, run_am_or_pm = manual_date, manual_am_pm
    else:
        run_today, run_am_or_pm = get_today_paths()

    setup_logging(run_today, run_am_or_pm)
    logging.info("업로드 메타가 실행됩니다.")
    main(run_today, run_am_or_pm)


