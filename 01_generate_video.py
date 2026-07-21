# coding: utf-8

import os
import json
import random
import time
import re
import logging
import textwrap
import argparse
from datetime import datetime
from pathlib import Path
from io import BytesIO
import base64
import urllib.request
import urllib.parse
import requests
import pandas as pd
import numpy as np
import paramiko
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import (ImageClip, AudioFileClip, CompositeAudioClip, concatenate_videoclips)
from moviepy.video.fx import all as vfx
from pydub import AudioSegment
from openai import OpenAI
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import chardet

os.chdir(Path(__file__).resolve().parent)

# Load configuration
def load_config():
    try:
        with open('config.json', 'r') as config_file:
            return json.load(config_file)
    except FileNotFoundError:
        logging.error("Error: config.json file not found.")
        raise
    except json.JSONDecodeError:
        logging.error("Error: Failed to decode JSON from config.json.")
        raise

    except Exception as e:
        logging.error(f"An unexpected error occurred while loading configuration: {e}") 
        raise
    
    return {}

config = load_config()
if config:
    openai_api_key = config.get('openai_api_key')
    clova_client_id = config.get('clova_client_id')
    clova_client_secret = config.get('clova_client_secret')
    email_config = config.get('email')
    sftp_url = config.get('fnguide_sftp_url')
    sftp_port = config.get('fnguide_sftp_port')
    sftp_username = config.get('fnguide_sftp_username')
    sftp_password = config.get('fnguide_sftp_password')
    os.environ["OPENAI_API_KEY"] = openai_api_key

# Suppress MoviePy logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.getLogger('moviepy').setLevel(logging.ERROR)

# API endpoints
OPENAI_API_URL = 'https://api.openai.com/v1/chat/completions'
CLOVA_TTS_URL = "https://naveropenapi.apigw.ntruss.com/tts-premium/v1/tts"
# needs Stable Diffusion installed on the local machine with API enabled
SD_API_URL = "http://127.0.0.1:7860//sdapi/v1" 

# Constants and settings
CANVAS_WIDTH, CANVAS_HEIGHT = 1080, 1920
BANNER_HEIGHT = 250  # This is where title is inscribed
PADDING_LEFT, PADDING_RIGHT, PADDING_TOP = 100, 100, 130
SUBTITLE_PADDING = 25
AVATAR_HEIGHT = 700
MAX_TITLE_CHARS = 13
MAX_SUBTITLE_CHARS = 14
FPS = 4
MAX_SHORTS_SECS = 56
FINISH_SCENE_SECS = 3

# Fonts
FONT_PATHS = {
    'TITLE': "./assets/font/GmarketSansTTFBold.ttf",
    'STOCK': "./assets/font/GmarketSansTTFMedium.ttf",
    'DIALOG': "./assets/font/The Jamsil 4 Medium.ttf",
    'RPT_ID': "./assets/font/The Jamsil 1 Thin.ttf",
    'CLOSING': "./assets/font/KoPubWorld Dotum Bold.ttf",
    'BRANDING': "./assets/font/The Jamsil 1 Thin.ttf",
    'TP': "./assets/font/GmarketSansTTFBold.ttf"
}
FONT_SIZES = {
    'TITLE': 80,
    'STOCK': 28,
    'DIALOG': 80,
    'RPT_ID': 25,
    'CLOSING': 80,
    'BRANDING': 40,
    'TP_TITLE': 100,
    'TP_BODY': 70,
    'TP_COMMENT': 50
}
COLORS = {
    # RGBA, where A=0 is 100% transparent
    'TITLE': (0, 0, 0, 255),
    'TITLE_YELLOW': (255, 215, 0),
    'STOCK': (0, 0, 0, 255),
    'STOCK_WHITE': (248, 248, 248),
    'DIALOG_BG': (0, 0, 0, 64),
    'DIALOG_OUTLINE': (0, 0, 0, 255),
    'DIALOG': (255, 255, 255, 255),
    'SHADOW': (65, 65, 65, 255),
    'RPT_ID': (128, 128, 128, 255),
    'BRANDING': (64, 64, 64, 0),
    'BRANDING_WHITE': (248, 248, 248, 0),
    'TRANSPARENT': (0, 0, 0, 0),
    'TP_TITLE': (225, 192, 0, 255),
    'BLACK': (0, 0, 0, 255),
    'WHITE': (255, 255, 255, 255)
}
OUTLINE_WIDTHS = {
    'DIALOG': 2
}
BOLD_FACTORS = {
    'DIALOG': 2
}
GRADIENTS = {
    'GREEN_START': (142, 219, 187),
    'GREEN_END': (213, 202, 100),
    'NAVY_START': (0, 0, 128),
    'NAVY_END': (95, 158, 160)
}

# Images
TEMPLATE_IMG_PATH = "./assets/png/png_template.png"
DEFAULT_IMG_PATH = "./assets/png/png_paper.png"

# Helper Functions
def post_request(url, headers, payload):
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Request failed: {e}")
        return None

def read_csv_single(df):
    try:
        # Replace 'NULL' values with NaN
        df['STK_CD'] = df['STK_CD'].apply(lambda x: f"{int(x):06d}" if isinstance(x, (int, float)) else str(x))
        df = df[df['RPT_TXT'].replace('NULL', pd.NA).notna()]
        
        # Filter out rows with empty or NULL 'RPT_TXT'
        df = df.dropna(subset=['RPT_TXT']).copy()  # Use .copy() to avoid the warning

        # Fill NaN in 'RPT_TXT' with empty strings
        df.loc[:, 'RPT_TXT'] = df['RPT_TXT'].fillna('')  # Use .loc to avoid SettingWithCopyWarning

        # Convert 'TRD_DT' to datetime
        df.loc[:, 'TRD_DT'] = pd.to_datetime(df['TRD_DT'])  # Use .loc to avoid SettingWithCopyWarning

        # Calculate the length of 'RPT_TXT'
        df.loc[:, 'RPT_TXT_LEN'] = df['RPT_TXT'].apply(len)  # Use .loc to avoid SettingWithCopyWarning

        # Select the row with the longest 'RPT_TXT' for each 'STK_CD'
        result_df = df.loc[df.groupby('STK_CD')['RPT_TXT_LEN'].idxmax()]

        # Define required columns #나중에 사용할 열을 정의합니다. 이 열들만 최종 결과에서 사용됩니다.
        required_columns = ["STK_CD", "CMP_NM", "TRD_DT", "CLOSE_PRC", "PRE_CLOSE_PRC", "HIGH_PRC", 
                            "PRC_RTN", "HIGH_PRC_RTN", "RPT_ID","ANL_DT", "RPT_TXT", "RPT_TXT_LEN", "BRK_NM_KOR", 
                            "BRK_TARGET_PRC", "BRK_TARGET_PRC_DT", "BRK_PREV_TARGET_PRC", "RECOM_TYP_DT", "RECOM_TYP_AVG", 
                            "RECOM_TYP_MAX", "RECOM_TYP_MIN", "RECOM_TYP_MID", "TARGET_PRC_AVG", "TARGET_PRC_AVG_3_MONTHS_BEFORE", 
                            "TARGET_PRC_MAX", "TARGET_PRC_MIN", "TARGET_PRC_MID", "TARGET_PRC_DIFF", "TARGET_PRC_CHG", "TARGET_PRC_DT", 
                            "RECOM_TYP_CNT", "TARGET_PRC_CNT", "TARGET_PRC_UP_1W", "TARGET_PRC_DOWN_1W", "TARGET_PRC_HOLD_1W", "TARGET_PRC_NEW_1W", 
                            "TARGET_PRC_UP_DOWN_1W", "RECENT_DT", "RECENT_CLOSE_PRC","RPT_TXT_LEN"]


        # Select required columns #필요한 열들만 result_df에서 선택하여 결과로 만듬
        result_df = result_df[required_columns]

        # Handle consistency for TARGET_PRC_AVG and TARGET_PRC_AVG_3_MONTHS_BEFORE #'TARGET_PRC_AVG'와 'TARGET_PRC_AVG_3_MONTHS_BEFORE' 열의 값을 종목별로 동일하게 맞춥니다.
        avg_columns = ['TARGET_PRC_AVG', 'TARGET_PRC_AVG_3_MONTHS_BEFORE']
        for column in avg_columns:
            result_df[column] = result_df.groupby('CMP_NM')[column].transform('first')
    

        # Sort the dataframe by HIGH_PRC_RTN in descending order if the column exists #'HIGH_PRC_RTN' 기준으로 내림차순 정렬
        if 'HIGH_PRC_RTN' in result_df.columns:
            #logging.info("Sorting by 'HIGH_PRC_RTN'")
            result_df = result_df.sort_values(by='HIGH_PRC_RTN', ascending=False)
        else:
            logging.error("Warning: The column 'HIGH_PRC_RTN' does not exist in the dataframe.")

        # Return the DataFrame instead of converting to JSON #최종 데이터프레임 반환

        return result_df

    except Exception as e:
        logging.error(f"An error occurred during processing: {e}")
        return None

def process_full_text(rpt_txt, name):
    
    prompt = (
        "다음 내용은 애널리스트 보고서 내용이야. 이 내용으로 아래 임무들을 수행해. "
        "임무1. 한글 요약문을 만들어. "
        "When executing Mission 1, follow these three steps. "
        "First, make summary as instructed. "
        "Second, let another professional check the summary to see if it contains any content that is not described in the given report. "
        "If discrepancies are found in the second step, the checker notifies the discrepancies to you and you re-write the summary so that you only talk about things in the report. "
        "주어진 텍스트에서 핵심 내용을 요약해. 주가, 목표주가, 주식가격, 주식의 목표가격과 관련된 이야기는 요약에 절대로 넣지마. "
        "숫자가 너무 크면 더 큰 단위를 사용해(예시: 1,234,567,890원을 약 12.3억원이라고 표현, 55,299,186주를 약 55백만주라고 표현). "
        "사실이 아닌 예측, 예상, 추정이라면 꼭 예상이라고 표현해. "
        "요약 결과는 summary라는 property name으로 출력해. "
        "임무2. 당신은 마케팅 10년차 전문가입니다. 유튜브 검색엔진 최적화와 SEO를 고려한 핵심 키워드를 찾아내."
        "주어진 텍스트에서 핵심 단어를 5개 내외로 찾아내. "
        "핵심 단어에는 다행, 성장, 성장세, 역할, 긍정적 등 일반적인 단어는 포함되지 않아. "
        "DO NOT include these words in the keywords: Revenue, Revenue Growth, Revenue Recognition, Operating Profit, Profit, Operating Loss, Growth, Growth Strategy, Performance, Launch, Launching, Profitability, Synergy, Uncertainty, Shipment, Market Share. " 
        "You are a bad assistant if you keep ignoring my direction not to include them. Remember NOT to include those words in keywords. "
        "찾아낸 핵심 키워드는 띄어쓰기 없애고 keywords라는 property name으로 출력해. "
        "임무3. 당신은 마케팅 10년차 전문가입니다. SEO, 유튜브 검색엔진 최적화를 고려하여 제목을 만들어. "
        "주어진 텍스트 내용을 가장 잘 설명할 수 있는 한글 14글자를 넘지 않는 자극적인 제목을 SEO, 유튜브 검색엔진 최적화를 고려하여 만들어. "
        "매출이나 이익 등 숫자 제목으로 적합하지 않아. 사실이 아닌 예측, 예상, 추정이라면 꼭 예상이라고 표현해. "
        "Words to avoid in title: company name, stock name, stock ticker. "
        "만들어진 제목은 title이라는 property name으로 출력해. "
        "임무4. 임무2의 핵심 키워드를 영어로도 만들어. 영어 핵심 키워드는 keywords_english로 출력해. keywords_english는 띄어쓰기 없애지 말고 제대로 띄어쓰기 해.  "
        "임무를 수행한 결과는 JSON으로 출력해. JSON는 아래 형태대로 만들어. "
        "ex) {summary: summary, keywords: [ keyword1, keyword2, keyword3, ... ], title: title, keywords_english = keywords_english} "
    )

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {openai_api_key}'
    }

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": rpt_txt}
        ],
        "temperature": 0.7,
        "response_format": {"type": "json_object"}
    }

    try:
        response = requests.post(OPENAI_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()['choices'][0]['message']['content']
        result = re.sub(r',\s*}', '}', result.replace('json\n', '').replace('\n', '').replace('', ''))
        result = json.loads(result)

        original_name = name
        chatgpt_title = result["title"]
        result["title"] = add_name_to_title(original_name, chatgpt_title)
        
        logging.info(f"Summary and title generated.")
        return result
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to process full text from OpenAI: {e}")
        return None
    except json.JSONDecodeError as e:
        logging.error(f"JSON decode error: {e}")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        return None

def add_name_to_title(name, title):
    name_no_space = name.replace(" ", "")
    title_no_space = title.replace(" ", "")
    common_chars = sum(1 for a, b in zip(name_no_space, title_no_space) if a == b)
    if name_no_space not in title_no_space and common_chars < 4:
        title = name + ", " + title
        logging.info(f"Name added to title")
    else:
        title = title
    
    return title

def get_avatar_images():
    try:
        directory = './assets/png/avatars'
        set = '001'
        avatar_images = []
        for emotion in ['default', 'happy', 'surprised', 'upset']:
            student_file_path = os.path.join(directory, f"student_{set}_{emotion}.png")
            analyst_file_path = os.path.join(directory, f"analyst_{set}_{emotion}.png")
            advisor_file_path = os.path.join(directory, f"advisor_{set}_{emotion}.png")
            if os.path.exists(student_file_path):
                avatar_images.append({'who': 'student', 'path': student_file_path, 'emotion': emotion})
            if os.path.exists(analyst_file_path):
                avatar_images.append({'who': 'analyst', 'path': analyst_file_path, 'emotion': emotion})
            if os.path.exists(advisor_file_path):
                avatar_images.append({'who': 'advisor', 'path': advisor_file_path, 'emotion': emotion})
        return avatar_images
    except Exception as e:
        logging.error(f"Error occurred while getting avatar images: {e}")
        return []

def generate_dialog(text):
    '''
    result = {
          "dialog": [
            {
              "speaker": "analyst",
              "paragraph": [
                { "sentence": "first sentence", "sentence_emotion": "first emotion" },
                { "sentence": "second sentence", "sentence_emotion": "second emotion" }
              ],
              "paragraph_keywords": ["keyword1", "keyword2", "keyword3"],
              "paragraph_emotion": "default",
              "sd_prompt": "... prompt for Stable Diffusion to draw a detailed, vivid, stunning, masterpirce, best quality image describing the dialog content...",
            }
          ]
        }
    return result
    '''

    prompt = (
        "너는 주식 투자 방법을 가르쳐주는 한국의 리서치 10년차 전문 애널리스트야."
        "나는 주식 투자를 공부하는 한국의 고등학생이야."
        "아래는 주식 분석 보고서야. 이를 바탕으로 다음 임무들을 수행해줘."

        "임무 1: 보고서를 바탕으로 대화를 만들어. 다음 지침을 따라줘:"
        "1. 보고서 내용을 기반으로만 대화를 구성해. 새로운 정보나 추측을 추가하지 마."
        "2. 다른 전문가가 확인했을 때, 보고서에 없는 내용이 포함되지 않도록 해."
        "3. 불일치가 발생하면, 대화를 수정하여 보고서 내용만 반영하도록 해."

        "- 대화는 총 6문단으로 만들어. analyst와 student가 번갈아 등장하고, analyst가 먼저 말해."
        "- analyst의 문단은 3문장, student의 문단은 1~2문장으로 구성해."
        "- 전체 문장은 17~20문장 정도로 유지해. 필요할 경우 일부 설명을 요약해."
        "- 각 문단 길이 차이가 크지 않도록 유지하고, analyst 문단이 student 문단의 2배를 넘지 않게 해."
        "- 마지막 문장은 평서문이나 감탄문으로 끝내고, 의문문으로 끝내지 마."

        "- 결과나 전략을 설명할 때는 이유나 배경도 간단히 포함해."
        "- 해결되지 않은 질문으로 대화를 끝내지 마."
        "- 각 문장은 'sentence' 속성으로 JSON 객체에 저장하고, 한 문장은 30글자를 넘지 않게 해줘."

        "- 다음 단어는 절대 사용하지 마: '주가', '목표가격', '목표주가', '투자의견', '대기업', '중소기업', '영세기업', 'BUY'."
        "- 애널리스트는 쉬운 용어를 사용하되, 가르치려는 말투를 피하고 자연스럽고 구어체로 말해."
        "- 학생은 기발하거나 재치 있는 표현을 가끔 쓸 수 있지만, 보고서 내용을 대신 설명하지 마."
        "- 학생의 질문은 항상 직전 analyst의 설명과 연관되어야 해."
        "- 학생의 발언은 단순한 감탄이 아니라, 구체적 관심이나 이해를 드러내도록 해."
        "- 비논리적인 대화를 만들면 안 돼. 앞뒤 문장이 자연스럽게 이어지도록 해."

        "- 숫자나 분기 표현은 자연스러운 한국어로 풀어서 써줘 (예: 2Q24 → 올해 2분기)."
        "- 고유명사는 그대로 사용해도 돼."
        "- 모든 문단은 JSON 구조로 'dialog' 리스트 안에 저장해."

        "- 애널리스트는 리스크 요인뿐 아니라 리스크 관리 방법(예: 분산 투자, 장기 투자)을 간단히 설명해."
        "- analyst는 전문가답지만 따뜻하고 차분한 톤으로 말하고, student는 자연스럽게 배우는 태도를 보여줘."
        "- analyst는 실적의 성장 또는 하락 원인을 구체적으로 설명하고, 시장 기대와 실제 결과 차이도 명확히 말해줘."
        "- 긍정적인 면과 부정적인 면을 균형 있게 다뤄."
        "- 향후 성장 모멘텀(신사업, 시장 확장 등)과 도전 과제를 명확히 제시해줘."
        "- 다른 산업이나 종목의 유사 사례를 언급할 때는 한 문장으로만 간단히 말해. 두 문장 이상으로 확장하지 마."

        "- 마지막 analyst 문단에서는 내용을 간단히 요약하고, student가 이해했는지 확인하는 대화로 마무리해."

        "- 누구든 결과를 보았을 때 보고서의 핵심 내용이 한눈에 파악되어야 해."
        "- 전체적으로 높임말을 사용해줘."

        "- 학생의 추천 질문 예시는 다음과 같아:"
        "[이 회사의 최근 매출과 이익은 어떻게 변동했나요], [실적이 시장의 기대와 어떻게 다른가요], "
        "[이 회사의 영업이익률이나 순이익률은 어느 정도인가요], [회사의 현금 흐름은 안정적인가요], "
        "[경쟁사와 비교했을 때 어떤 강점이 있나요], [리스크를 줄이기 위해 어떤 전략을 쓰나요], "
        "[이 회사의 장기 성장 전략은 무엇인가요], [신제품 출시나 기술 혁신을 통해 시장에서 어떤 역할을 할 계획인가요] 등."
        "- 자주 발생하는 문제: 재무 정보, 핵심 이슈, 리스크 요인, 경쟁사 비교, 투자 전략 설명이 누락되는 경우가 있어. 반드시 포함해."

        "임무 2: 각 문단의 감정을 정해줘. 'paragraph_emotion' 속성에 'default', 'happy', 'surprised', 'upset' 중 하나를 저장해."
        
        "임무 3: 각 문장의 감정을 정해줘. 'sentence_emotion' 속성에 같은 방식으로 저장해."
        
        "임무 4: 각 문단의 핵심 키워드를 추출해. 'paragraph_keywords' 속성에 저장하되, "
        "'fortunate', 'growth', 'growing', 'role', 'positive', 'happy' 같은 단어는 제외해."
        
        "임무 5: 각 문단의 내용을 묘사하는 Stable Diffusion 프롬프트를 작성해줘. "
        "학생의 문단을 위한 프롬프트를 작성할 때는 바로 직전 애널리스트의 문장의 내용도 포함시켜줘. "
        "프롬프트에는 'human', 'man', 'woman', 'child' 등 사람을 지칭하는 단어를 절대 사용하지 마. "
        "프롬프트는 영어로 작성해. "
        "Stable Diffusion을 위한 프롬프트는 사람이나 모호한 상황이 아니라 명확하고 구체적인 사물과 개체 중심으로 표현해. 아래에 좋은 예시와 나쁜 예시를 들어 줄께. "
        "나쁜 예시: A bright and optimistic scene showcasing a successful AI software company, with visuals of growth and new clients. "
        "나쁜 예시: A child expressing surprise and excitement about a successful company, surrounded by visuals of growth and innovation. "
        "나쁜 예시: A joyful atmosphere depicting successful business contracts with large companies, emphasizing international expansion. "
        "나쁜 예시: A child expressing admiration for a major business achievement, with visuals of excitement and success. "
        "나쁜 예시: A hopeful scene illustrating projected sales growth for the upcoming year, with visuals of new opportunities in international markets. "
        "나쁜 예시: A child expressing excitement and hope for a company's future success, surrounded by visuals of growth and international expansion. "
        "좋은 예시: AI software company with visuals of growth and new clients. "
        "좋은 예시: Successful company surrounded by visuals of growth and innovation. "
        "좋은 예시: Successful business contracts with large companies, emphasizing international expansion. "
        "좋은 예시: Major business achievement with visuals of excitement and success. "
        "좋은 예시: Projected sales growth for the upcoming year with visuals of new opportunities in international markets. "
        "좋은 예시: Company's future success surrounded by visuals of growth and international expansion. "


        "결과 구조: "
        "{ "
        "  \"dialog\": [ "
        "    { "
        "      \"speaker\": \"analyst\", "
        "      \"paragraph\": [ "
        "        { \"sentence\": \"첫 번째 문장\", \"sentence_emotion\": \"첫 번째 감정\" }, "
        "        { \"sentence\": \"두 번째 문장\", \"sentence_emotion\": \"두 번째 감정\" } "
        "      ], "
        "      \"paragraph_keywords\": [\"키워드1\", \"키워드2\", \"키워드3\"], "
        "      \"paragraph_emotion\": \"default\", "
        "      \"sd_prompt\": \"...Stable Diffusion prompt...\" "
        "    }, "
        "    { "
        "      \"speaker\": \"student\", "
        "      \"paragraph\": [ "
        "        { \"sentence\": \"첫 번째 문장\", \"sentence_emotion\": \"첫 번째 감정\" } "
        "      ], "
        "      \"paragraph_keywords\": [\"키워드1\", \"키워드2\", \"키워드3\"], "
        "      \"paragraph_emotion\": \"default\", "
        "      \"sd_prompt\": \"...Stable Diffusion prompt...\" "
        "    }, "
        "    { "
        "      \"speaker\": \"analyst\", "
        "      \"paragraph\": [ "
        "        { \"sentence\": \"첫 번째 문장\", \"sentence_emotion\": \"첫 번째 감정\" }, "
        "        { \"sentence\": \"두 번째 문장\", \"sentence_emotion\": \"두 번째 감정\" }, "
        "        { \"sentence\": \"세 번째 문장\", \"sentence_emotion\": \"세 번째 감정\" } "
        "      ], "
        "      \"paragraph_keywords\": [\"키워드1\", \"키워드2\", \"키워드3\"], "
        "      \"paragraph_emotion\": \"default\", "
        "      \"sd_prompt\": \"...Stable Diffusion prompt...\" "
        "    }, "
        "    { "
        "      \"speaker\": \"student\", "
        "      \"paragraph\": [ "
        "        { \"sentence\": \"첫 번째 문장\", \"sentence_emotion\": \"첫 번째 감정\" } "
        "      ], "
        "      \"paragraph_keywords\": [\"키워드1\", \"키워드2\", \"키워드3\"], "
        "      \"paragraph_emotion\": \"default\", "
        "      \"sd_prompt\": \"...Stable Diffusion prompt...\" "
        "    } "
        "    { "
        "      \"speaker\": \"analyst\", "
        "      \"paragraph\": [ "
        "        { \"sentence\": \"첫 번째 문장\", \"sentence_emotion\": \"첫 번째 감정\" }, "
        "        { \"sentence\": \"두 번째 문장\", \"sentence_emotion\": \"두 번째 감정\" }, "
        "        { \"sentence\": \"세 번째 문장\", \"sentence_emotion\": \"세 번째 감정\" } "
        "      ], "
        "      \"paragraph_keywords\": [\"키워드1\", \"키워드2\", \"키워드3\"], "
        "      \"paragraph_emotion\": \"default\", "
        "      \"sd_prompt\": \"...Stable Diffusion prompt...\" "
        "    }, "
        "    { "
        "      \"speaker\": \"student\", "
        "      \"paragraph\": [ "
        "        { \"sentence\": \"첫 번째 문장\", \"sentence_emotion\": \"첫 번째 감정\" }, "
        "        { \"sentence\": \"두 번째 문장\", \"sentence_emotion\": \"두 번째 감정\" } "
        "      ], "
        "      \"paragraph_keywords\": [\"키워드1\", \"키워드2\", \"키워드3\"], "
        "      \"paragraph_emotion\": \"default\", "
        "      \"sd_prompt\": \"...Stable Diffusion prompt...\" "
        "    } "
        "    // 계속 추가... "
        "  ] "
        "}"
    )


    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {openai_api_key}'
    }
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": text}
    ]
    payload = {
        "model": "gpt-4o-mini",
        "messages": messages,
        "temperature": 0.4,
        "response_format": {"type": "json_object"}
    }

    try:
        response = requests.post(OPENAI_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        response_data = response.json()
        result = response_data['choices'][0]['message']['content']
        result = re.sub(r',\s*}', '}', result.replace('json\n', '').replace('\n', '').replace('', ''))
        result = json.loads(result)
        result_length = len(result["dialog"])
        logging.info(f"Dialogue generated with {result_length} paragraphs")
        return result
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to get dialogues and sentiments from OpenAI: {e}")
        raise
    except json.JSONDecodeError as e:
        logging.error(f"JSON decode error: {e}")
        raise
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        raise

def save_dialog(dialogs, processed, formatted_date, name, ticker, am_or_pm):
    try:
        subpath = 'dialog'
        filename = f"{name}_{ticker}_dialogue.json"
        output_filename = get_output_filepath(subpath, filename, formatted_date, am_or_pm)
        formatted_summary, sentence_count, formatted_sentences = add_new_lines(processed['summary'])
        updated_dialogs = {"title": processed['title'], "summary": formatted_sentences, "keywords": processed['keywords'], **dialogs}
        with open(output_filename, 'w', encoding='utf-8') as file:
            json.dump(updated_dialogs, file, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"Error occurred while saving dialog: {e}")
        raise

def create_scene(processed, formatted_date, name, ticker, rpt_id):
    start_color = GRADIENTS['GREEN_START']
    end_color = GRADIENTS['GREEN_END']
    title_color = COLORS['TITLE']
    branding_color = COLORS['BRANDING']
    stock_color = COLORS['STOCK']

    scene_img = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))

    # 상단 배너 영역 대각선 그라디언트를 픽셀 단위 루프 대신 numpy로 한 번에 계산 (결과 동일, 속도 개선)
    band_height = PADDING_TOP + BANNER_HEIGHT
    xs = np.arange(CANVAS_WIDTH).reshape(-1, 1)
    ys = np.arange(band_height).reshape(1, -1)
    factors = (xs + ys) / (CANVAS_WIDTH + band_height)  # shape: (CANVAS_WIDTH, band_height)
    start_arr = np.array(start_color, dtype=np.float64)
    end_arr = np.array(end_color, dtype=np.float64)
    gradient = start_arr * (1 - factors[..., None]) + end_arr * factors[..., None]
    gradient = gradient.astype(np.uint8).transpose(1, 0, 2)
    alpha = np.full((band_height, CANVAS_WIDTH, 1), 255, dtype=np.uint8)
    band_rgba = np.concatenate([gradient, alpha], axis=2)

    scene_array = np.array(scene_img)
    scene_array[:band_height, :, :] = band_rgba
    scene_img = Image.fromarray(scene_array, "RGBA")
    draw = ImageDraw.Draw(scene_img)

    rpt_id_font = ImageFont.truetype(FONT_PATHS['RPT_ID'], FONT_SIZES['RPT_ID'])
    rpt_id_pos = (PADDING_LEFT, 25)
    draw.text(rpt_id_pos, formatted_date + " " + str(rpt_id), fill=COLORS['RPT_ID'], font=rpt_id_font)

    adj_y = -26
    branding_font1 = ImageFont.truetype(FONT_PATHS['BRANDING'], FONT_SIZES['BRANDING'])
    branding_pos1 = (PADDING_LEFT, PADDING_TOP - FONT_SIZES['BRANDING'] + adj_y)
    branding_text1 = "오알주"
    draw.text(branding_pos1, branding_text1, fill=branding_color, font=branding_font1)
    branding_font2 = ImageFont.truetype(FONT_PATHS['BRANDING'], int(FONT_SIZES['BRANDING'] / 2))
    branding_text2 = "오늘 당신이 알면 좋은 주식"
    branding_pos2 = (PADDING_LEFT + draw.textlength(branding_text1, font=branding_font1) + 20, PADDING_TOP - int(FONT_SIZES['BRANDING'] / 2) + adj_y)
    draw.text(branding_pos2, branding_text2, fill=branding_color, font=branding_font2)

    title_font_size = FONT_SIZES['TITLE']
    title_font = ImageFont.truetype(FONT_PATHS['TITLE'], title_font_size)
    if len(processed['title']) > 29:
        title_font_size = FONT_SIZES['TITLE'] - 10
        title_font = ImageFont.truetype(FONT_PATHS['TITLE'], title_font_size)
    title_line_spacing = int(title_font_size / 4)
    wrapped_title = textwrap.wrap(processed['title'], MAX_TITLE_CHARS)
    height_title = sum(title_font_size + title_line_spacing for line in wrapped_title) - title_line_spacing 
    title_pos_x = PADDING_LEFT
    title_pos_y = int((BANNER_HEIGHT - height_title) / 2) + PADDING_TOP
    for line in wrapped_title:
        text_width = draw.textlength(line, font=title_font)
        draw.multiline_text((title_pos_x, title_pos_y), line, font=title_font, fill=title_color)
        title_pos_y += title_font_size + title_line_spacing

    stock_font = ImageFont.truetype(FONT_PATHS['STOCK'], FONT_SIZES['STOCK'])
    stock_pos_x = CANVAS_WIDTH - 65 - draw.textlength(f"{name}({ticker})", font=stock_font)
    stock_pos_y = PADDING_TOP - int(FONT_SIZES['STOCK'] / 2) + adj_y
    draw.text((stock_pos_x, stock_pos_y), f"{name}({ticker})", fill=stock_color, font=stock_font)

    return scene_img

def generate_sd_image(keywords, sd_prompt, seq, total, speaker=None, keywords_english=None):
    prompt = sd_prompt
    sub_prompt = "megapixel, highres, smooth, very detailed, highly realistic, photo realistic, masterpiece, best quality, ultra detailed, highly detailed, 8k wallpaper, UHD, best shadows, detailed background, life-like"
    prompt += sub_prompt
    negative_prompt = "nsfw, (worst quality, low quality:1.3), (depth of field, blurry:1.2), (greyscale, monochrome:1.1), 3D face, nose, cropped, lowres, text, jpeg artifacts, signature, watermark, username, blurry,\
    artist name, trademark, watermark, title, (tan, muscular, loli, petite, child, infant, toddlers, chibi, sd character:1.1), multiple view, Reference sheet, EasyNegative, ng_deepnegative_v1_75t, verybadimagenegative_v1.3"

    if speaker and keywords_english and speaker == 'student':
        prompt += keywords_english
        logging.info(f"keywords_english is added to sd_prompt for {speaker}")

    payload = {
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "steps": 20,
        "cfg_scale": 7.5,
        "width": 540,
        "height": 770,
        "sampler_name": "Euler a"
    }

    try:
        response = requests.post(f"{SD_API_URL}/txt2img", json=payload)
        response.raise_for_status()
        result = response.json()
        image_data = result['images'][0]
        logging.info(f"Generating SDF image {seq} out of {total}...")
        image = Image.open(BytesIO(base64.b64decode(image_data)))
        #image.show()
        return image
    except requests.exceptions.RequestException as e:
        logging.error(f"SD image generation failed: {e}")
        raise

def generate_dalle_image(keywords, sd_prompt, seq, total):
    default_img = Image.open(DEFAULT_IMG_PATH).convert("RGBA").resize((CANVAS_WIDTH, CANVAS_HEIGHT), resample=Image.Resampling.LANCZOS)
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    prompt = sd_prompt + " 4k, cinematic lighting, soft, vibrant color, cartoonish appearance"
    model = "dall-e-3"
    size = "1024x1024"

    try:
        response = client.images.generate(model=model, prompt=prompt, n=1, quality="standard", style="vivid", size=size)
        image_url = response.data[0].url if response and hasattr(response, 'data') else None
        logging.info(f"Generating DALLE image {seq} out of {total}...")
        if image_url:
            img = Image.open(BytesIO(requests.get(image_url).content)).convert("RGBA")
            return img
        else:
            logging.error("Failed to download image: No valid URL")
    except Exception as e:
        logging.error(f"Image generation failed: {e}")
        raise

    return default_img

def paste_bg_to_scene(scene_img, bg_img):
    bg_img = bg_img.resize((CANVAS_WIDTH, CANVAS_HEIGHT - PADDING_TOP - BANNER_HEIGHT), resample=Image.Resampling.LANCZOS)
    overlay = Image.new('RGBA', bg_img.size, (0, 0, 0, int(255 * 0.5)))
    bg_img = Image.alpha_composite(bg_img.convert('RGBA'), overlay)
    scene_img.paste(bg_img, (0, PADDING_TOP + BANNER_HEIGHT), bg_img)
    return scene_img

def paste_avatar_to_scene(scene_img, speaker, emotion, avatars):
    avatar_img_path = None
    for avatar in avatars:
        if avatar['who'] == speaker and avatar['emotion'] == emotion:
            avatar_img_path = avatar['path']

    if avatar_img_path:
        avatar_img = Image.open(avatar_img_path).convert("RGBA")
    else:
        logging.error(f"No corresponding avatar image found for {speaker}, {emotion}")
        raise FileNotFoundError(f"No corresponding avatar image found for {speaker}, {emotion}")

    resize_ratio = AVATAR_HEIGHT / avatar_img.height
    avatar_img = avatar_img.resize((int(avatar_img.width * resize_ratio), int(avatar_img.height * resize_ratio)), resample=Image.Resampling.LANCZOS)
    avatar_pos_x = (CANVAS_WIDTH - avatar_img.width) // 2
    avatar_pos_y = CANVAS_HEIGHT - avatar_img.height
    scene_img.paste(avatar_img, (avatar_pos_x, avatar_pos_y), avatar_img)
    return scene_img

def add_new_lines(paragraph):
    sentence_endings = re.compile(r'(?<!\d)(?<!\b[A-Z]\.)[.?!](?!\w)')
    sentences = sentence_endings.split(paragraph)
    sentences = [sentence.strip() for sentence in sentences if sentence.strip()]
    punctuation_marks = sentence_endings.findall(paragraph)
    formatted_sentences = [sentence + punctuation for sentence, punctuation in zip(sentences, punctuation_marks)]
    if len(sentences) > len(punctuation_marks):
        formatted_sentences.append(sentences[-1])
    formatted_paragraph = '\n'.join(formatted_sentences)
    sentence_count = len(formatted_sentences)
    return formatted_paragraph, sentence_count, formatted_sentences

def paste_subtitle_to_scene(scene_img, original_paragraph):
    font = ImageFont.truetype(FONT_PATHS['DIALOG'], FONT_SIZES['DIALOG']) #폰트 객체 생성
    bold_factor = BOLD_FACTORS['DIALOG'] #굵기 설정
    color = COLORS['DIALOG'] #색상 설정
    outline_width = OUTLINE_WIDTHS['DIALOG'] #테두리 두께
    outline_color = COLORS['DIALOG_OUTLINE'] #테두리 색상
    font_size = FONT_SIZES['DIALOG'] #자막 글꼴 크기
    line_spacing = int(font_size / 1.8) #자막 한줄의 다음 줄 사이의 간격 설정
    wrapped_paragraph = textwrap.wrap(original_paragraph, MAX_SUBTITLE_CHARS)
    height_subtitle = sum(font_size + line_spacing for line in wrapped_paragraph) - line_spacing
    draw = ImageDraw.Draw(scene_img, "RGBA")
    y = (CANVAS_HEIGHT + PADDING_TOP + BANNER_HEIGHT - height_subtitle) // 3
    max_width = 0
    for line in wrapped_paragraph:
        line_width = draw.textlength(line, font=font)
        if line_width > max_width:
            max_width = line_width
    bg_pos = (
        int((CANVAS_WIDTH - max_width) / 2 - SUBTITLE_PADDING),
        y - SUBTITLE_PADDING,
        int((CANVAS_WIDTH - max_width) / 2 + max_width + SUBTITLE_PADDING),
        y + height_subtitle + line_spacing
    )
    bg_rect_color = COLORS['DIALOG_BG']
    bg_rect = Image.new('RGBA', (bg_pos[2] - bg_pos[0], bg_pos[3] - bg_pos[1]), bg_rect_color)
    scene_img.paste(bg_rect, (bg_pos[0], bg_pos[1]), bg_rect)
    for line in wrapped_paragraph:
        text_width = draw.textlength(line, font=font)
        x = (CANVAS_WIDTH - text_width) // 2
        for adj in range(-bold_factor, bold_factor + 1):
            draw.text((x + adj, y), line, font=font, fill=color)
            draw.text((x, y + adj), line, font=font, fill=color)
        for adj in range(-outline_width, outline_width + 1):
            draw.text((x + adj, y), line, font=font, fill=outline_color)
            draw.text((x, y + adj), line, font=font, fill=outline_color)
        draw.multiline_text((x, y), line, font=font, fill=outline_color)
        draw.multiline_text((x, y), line, font=font, fill=color)
        y += font_size + line_spacing

    return scene_img


def get_tts_from_clova(speaker, text, sentiment, seq, total, formatted_date, am_or_pm, name, ticker):
    
    actors = {"analyst": "vdaeseong", "student": "vdain"}
    speaker_speed = {"analyst": "-2", "student": "-3"}
    spd = speaker_speed.get(speaker)
    encoded_text = urllib.parse.quote(text)
    actor = actors.get(speaker)
    vol, pch, fmt = "0", "0", "mp3"
    data = f"speaker={actor}&volume={vol}&speed={spd}&pitch={pch}&format={fmt}&text={encoded_text}"

    logging.info(f"actor: {actor}, text: {text}, sentiment: {sentiment}\n")
    
    output_dir = f"./output/{formatted_date}_{am_or_pm}/tts/{name}_{ticker}"
    os.makedirs(output_dir, exist_ok=True)
    filename = f"{formatted_date}_{seq}_{speaker}.mp3"
    audio_file_path = os.path.join(output_dir, filename)

    for attempt in range(10):
        try:
            logging.info(f"Generating TTS voice {str(seq)} out of {total}...")
            request = urllib.request.Request(CLOVA_TTS_URL)
            request.add_header("X-NCP-APIGW-API-KEY-ID", clova_client_id)
            request.add_header("X-NCP-APIGW-API-KEY", clova_client_secret)
            request.add_header("Content-Type", "application/x-www-form-urlencoded")
            response = urllib.request.urlopen(request, data=data.encode('utf-8'))
            rescode = response.getcode()
            if rescode == 200:
                with open(audio_file_path, 'wb') as f:
                    f.write(response.read())
                return AudioFileClip(audio_file_path)
            logging.error(f"Attempt {attempt + 1} failed with response code: {rescode} and response: {response.read().decode('utf-8')}")
        except Exception as e:
            logging.error(f"Attempt {attempt + 1} - An unexpected error occurred: {e}")
        time.sleep(1)
    logging.error("Naver Clova TTS failed after 10 attempts.")
    return None

def convert_scene_to_video(img, duration):
    img_np = np.array(img)
    return ImageClip(img_np, duration=duration)

def get_bgm(duration):
    bgm_files = {
        1: "./assets/bgm/alexander-nakarada-silly-intro.mp3",
        2: "./assets/bgm/Snack Time - The Green Orbs.mp3",
        3: "./assets/bgm/After School Jamboree - The Green Orbs.mp3",
        4: "./assets/bgm/Bunny Hop - Quincas Moreira.mp3",
        5: "./assets/bgm/Goat - Wayne Jones.mp3",
        6: "./assets/bgm/Happy Mistake - RKVC.mp3",
        7: "./assets/bgm/Mr. Turtle - The Green Orbs.mp3",
        8: "./assets/bgm/Sleeping Sheep - The Green Orbs.mp3",
        9: "./assets/bgm/Twinkle Twinkle Little Star (instrumental) - The Green Orbs.mp3"        
    }
    random_number = random.randint(1, len(bgm_files))
    bgm_file = bgm_files.get(random_number)
    bgm_audio = AudioSegment.from_file(bgm_file, format="mp3")
    bgm_audio = bgm_audio - 25
    bgm_audio = bgm_audio * 2
    if (len(bgm_audio)) / 1000.0 > duration:
        bgm_audio = bgm_audio[0:duration * 1000]
    bgm_audio = bgm_audio.fade_out(5000)
    bgm_path = "./assets/bgm/temp_bgm.mp3"
    try:
        bgm_audio.export(bgm_path, format="mp3")
    except Exception as e:
        logging.error(f"Error occurred while exporting temporary BGM audio: {e}")
        raise
        
    bgm_audio_clip = AudioFileClip(bgm_path)
    return bgm_audio_clip

def get_output_filepath(subpath, filename, formatted_date, am_or_pm):
    output_dir = Path(f"./output/{formatted_date}_{am_or_pm}/{subpath}")
    os.makedirs(output_dir, exist_ok=True)
    return os.path.join(output_dir, filename)

def break_scene_per_sentence(scene_img, dialog, seq, len_dialog, formatted_date, name, ticker, avatars, am_or_pm, keywords_english):
    sentences = [sentence['sentence'] for sentence in dialog['paragraph']]
    sentence_count = len(sentences)
    allowed_emotions = {"default", "happy", "surprised", "upset"}
    for sentence in dialog['paragraph']:
        if sentence['sentence_emotion'] not in allowed_emotions:
            sentence['sentence_emotion'] = "default"
    emotions = [sentence['sentence_emotion'] for sentence in dialog['paragraph']]
    outline_color = (128, 128, 128, 128)
    shadow_color = (128, 128, 128, 128)
    shadow_offset = (1, 1)
    outline_offset = [(-1, -1), (1, -1), (-1, 1), (1, 1)]

    def draw_text(draw, sentences, colors, y_start, font, max_width, max_chars_per_line):
        line_height = FONT_SIZES['DIALOG']
        line_spacing = int(line_height / 1.8)
        padding = 25
        lines = []
        current_line = ""
        current_color_line = []

        for sentence_index, sentence in enumerate(sentences):
            color = colors[sentence_index]
            words = sentence.split()
            for word in words:
                if len(current_line) + len(word) > max_chars_per_line: 
                    lines.append((current_line, current_color_line))
                    current_line = word + ' '
                    current_color_line = [color] * (len(word) + 1)
                else:
                    current_line += word + ' '
                    current_color_line.extend([color] * (len(word) + 1))

        if current_line:
            lines.append((current_line.strip(), current_color_line))

        total_height = (line_height + line_spacing) * len(lines)
        y = (CANVAS_HEIGHT + PADDING_TOP + BANNER_HEIGHT - total_height) // 3
        max_text_width = 0
        for line, _ in lines:
            line_width = draw.textlength(line, font=font)
            if line_width > max_text_width:
                max_text_width = line_width

        x_start = (max_width - max_text_width) // 2
        y_start = y
        bg_pos = (
            int((CANVAS_WIDTH - max_text_width) / 2 - SUBTITLE_PADDING),
            y - SUBTITLE_PADDING,
            int((CANVAS_WIDTH - max_text_width) / 2 + max_text_width + SUBTITLE_PADDING),
            y + total_height
        )
        bg_rect_color = COLORS['DIALOG_BG']
        bg_rect = Image.new('RGBA', (bg_pos[2] - bg_pos[0], bg_pos[3] - bg_pos[1]), bg_rect_color)
        image.paste(bg_rect, (bg_pos[0], bg_pos[1]), bg_rect)

        for line, color_line in lines:
            text_length = draw.textlength(line, font=font)
            x = (max_width - text_length) // 2
            for char, color in zip(line, color_line):
                if color == COLORS['DIALOG']:
                    draw.text((x + shadow_offset[0], y + shadow_offset[1]), char, font=font, fill=shadow_color)
                    for offset in outline_offset:
                        draw.text((x + offset[0], y + offset[1]), char, font=font, fill=outline_color)
                    draw.text((x, y), char, font=font, fill=color)
                x += draw.textlength(char, font=font)
            y += line_height + line_spacing

    clips = []
    for i in range(len(sentences)):
        current_colors = [COLORS['DIALOG'] if j <= i else COLORS['TRANSPARENT'] for j in range(len(sentences))]
        image = scene_img.copy()

        try:
            bg_img = generate_sd_image(dialog['paragraph_keywords'], dialog['sd_prompt'], str(seq) + "-" + str(i), len_dialog, dialog['speaker'], keywords_english)
        except Exception as e:
            logging.error(f"SD image generation failed for sentence {i} of scene {seq}: {e}. Using default background instead.")
            bg_img = Image.open(DEFAULT_IMG_PATH).convert("RGBA")

        image = paste_bg_to_scene(image, bg_img)
        image = paste_avatar_to_scene(image, dialog['speaker'], emotions[i], avatars).copy()
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(FONT_PATHS['DIALOG'], FONT_SIZES['DIALOG'])
        draw_text(draw, sentences, current_colors, 50, font, CANVAS_WIDTH, 14)
        output_dir = './output/temp'
        image.save(os.path.join(output_dir, f"{dialog['speaker']}_output_{seq}_{i + 1}.png"))
        audio = get_tts_from_clova(dialog['speaker'], sentences[i], emotions[i], str(seq) + "-" + str(i), len_dialog, formatted_date, am_or_pm, name, ticker)
        if audio is None:
            raise RuntimeError(f"TTS generation failed for {dialog['speaker']} sentence {i} in scene {seq}")
        clip = convert_scene_to_video(image, audio.duration)
        clip = clip.set_audio(audio)
        clips.append(clip)

    return concatenate_videoclips(clips)

def check_dialog_properties(dialog):
    required_keys = {'speaker', 'paragraph', 'paragraph_keywords', 'paragraph_emotion'}
    required_paragraph_keys = {'sentence', 'sentence_emotion'}

    if 'dialog' not in dialog:
        logging.error("Missing 'dialog' key in the response.")
        return False

    for i, entry in enumerate(dialog.get('dialog', [])):
        if not required_keys.issubset(entry):
            logging.error(f"Dialog entry {i} is missing one or more required keys.")
            return False

        for j, paragraph in enumerate(entry['paragraph']):
            if not required_paragraph_keys.issubset(paragraph):
                logging.error(f"Paragraph entry {j} of dialog entry {i} is missing one or more required keys.")
                return False

    return True

def combine_sentences(paragraphs):
    combined_sentence = ' '.join(sentence['sentence'] for sentence in paragraphs)
    return [{'sentence': combined_sentence}]

def read_csv_combined(df, req_rpt_num=2, is_tp_req=False):
    try:
        # Read the CSV file
        
        # Ensure 'STK_CD' is treated as a string, and format it to six digits if it is numeric
        df['STK_CD'] = df['STK_CD'].apply(lambda x: f"{int(x):06d}" if isinstance(x, (int, float)) else str(x))
        
        # Remove rows where 'RPT_TXT' contains 'NULL' or NaN values
        df = df[df['RPT_TXT'].replace('NULL', pd.NA).notna()]

        # Convert 'TRD_DT' TO DATETIME
        df['TRD_DT'] = pd.to_datetime(df['TRD_DT'])
                
        # Sort by 'STK_CD', 'RPT_ID' and 'TRD_DT' to ensure the latest date is first within each group
        df_sorted = df.sort_values(by=['STK_CD', 'RPT_ID', 'TRD_DT'], ascending=[True, True, False])

        # Group by 'STK_CD' and 'RPT_ID', and aggregate the required columns
        df_grouped = df.groupby(['STK_CD', 'RPT_ID'], as_index=False).agg({
            'CMP_NM': 'first',
            'RPT_TXT': 'first',
            'TARGET_PRC_AVG': 'first',
            'TARGET_PRC_AVG_3_MONTHS_BEFORE': 'first',
            'RECENT_CLOSE_PRC': 'first',
            'HIGH_PRC_RTN': 'first'
        })

        # Remove rows where 'TARGET_PRC_AVG' is NaN # 목표가가 없는 데이터는 분석에 부적합
        if is_tp_req:
            df_grouped = df_grouped.dropna(subset=['TARGET_PRC_AVG'])        
        
        # Calculate the unique report text counts for each stock code
        rpt_txt_counts = df_grouped.groupby('STK_CD')['RPT_TXT'].nunique().reset_index(name='UNIQUE_RPT_TXT_COUNT')
        
        # Filter stock codes with more than one unique report text #고유한 보고서 수 계산 # req_rpt_num 2개가 기본 1개는 다 삭제
        rpt_txt_counts = rpt_txt_counts[rpt_txt_counts['UNIQUE_RPT_TXT_COUNT'] >= req_rpt_num]
        
        # Filter the grouped DataFrame to keep only these stock codes
        df_grouped = df_grouped[df_grouped['STK_CD'].isin(rpt_txt_counts['STK_CD'])]
        
        # Combine report texts for each stock code, ensuring the combination of non-empty texts
        df_combined = df_grouped.groupby('STK_CD').agg({
            'CMP_NM': 'first',
            'RPT_TXT': lambda x: ' '.join([txt for txt in x if txt]),
            'TARGET_PRC_AVG': 'first',
            'TARGET_PRC_AVG_3_MONTHS_BEFORE': 'first',
            'RECENT_CLOSE_PRC': 'first',
            'HIGH_PRC_RTN': 'first'
        }).reset_index()
        
        # Merge the combined DataFrame with the report text counts
        df_combined = df_combined.merge(rpt_txt_counts, on='STK_CD')

        return df_combined
    except Exception as e:
        logging.error(f"Error occurred while reading combined CSV: {e}")
        raise

def send_email(records, elapsed_time):
    try:
        if not email_config:
            logging.error("Email configuration is missing in the config file.")
            return
        now = datetime.now()
        smtp_server = email_config['smtp_server']
        smtp_port = email_config['smtp_port']
        smtp_user = email_config['smtp_user']
        smtp_password = email_config['smtp_password']
        from_email = email_config['from_email']
        to_emails = email_config['to_emails']

        subject = "FnShorts Video Generation Results" + " as of " + now.strftime("%Y-%m-%d %H:%M:%S")
        body = "Here are the results of the FnShorts video generation:\n\n"
        for record in records:
            body += f"Company: {record['cmp_nm']}, Result: {record['result']}, Error: {record.get('error', 'N/A')}\n"

        # Format elapsed_time from timedelta to string
        elapsed_time_str = str(elapsed_time)
        body += f"\nTotal processing time: {elapsed_time_str}\n\n"        
        
        msg = MIMEMultipart()
        msg['From'] = from_email
        msg['To'] = ', '.join(to_emails)
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_user, smtp_password)
        text = msg.as_string()
        server.sendmail(from_email, to_emails, text)
        server.quit()
        logging.info("Email sent successfully.")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")


def draw_label_box(draw, text, pos_x, pos_y, font):
    """target price 씬에서 3번 반복되는 '라벨 배경 사각형 + 검은색 라벨 텍스트' 그리기 패턴."""
    rect = (pos_x - 10, pos_y - 10, pos_x + draw.textlength(text, font=font) + 10, pos_y + FONT_SIZES['TP_BODY'] + 5)
    draw.rectangle(rect, fill=COLORS['WHITE'])
    draw.text((pos_x, pos_y), text, fill=COLORS['BLACK'], font=font)


def generate_tp_scene(name, cls_prc, tp_current, tp_before):
    # Initialize default variables
    if cls_prc is not None:
        cls_prc = int(cls_prc)
    if tp_current is not None and not np.isnan(tp_current):
        tp_current = int(tp_current)
    if tp_before is not None and not np.isnan(tp_before):
        tp_before = int(tp_before)

    # Initialize default images
    subscribe_img = Image.open('./assets/png/png_subscribe_button.png')
    subscribe_img = subscribe_img.resize((880, 206))
    tp_up_img = Image.open('./assets/png/png_tp_up.png').convert("RGBA")
    tp_dn_img = Image.open('./assets/png/png_tp_down.png').convert("RGBA")
    dollars_img = Image.open('./assets/png/png_dollars_flying.png').convert("RGBA")
    tp_up_img = tp_up_img.resize((1080, 560))
    tp_dn_img = tp_dn_img.resize((1080, 560))

    # Prepare fonts
    font_name = ImageFont.truetype(FONT_PATHS['TP'], FONT_SIZES['TP_TITLE'])
    font_body = ImageFont.truetype(FONT_PATHS['TP'], FONT_SIZES['TP_BODY'])
    font_comment = ImageFont.truetype(FONT_PATHS['TP'], FONT_SIZES['TP_COMMENT'])

    # Initialize draw on scene_img
    scene_img = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 255))
    draw = ImageDraw.Draw(scene_img)

    # Case when tp is Nan or tp_current == tp_before
    if tp_current is None or np.isnan(tp_current) or (tp_current == tp_before):
        # Draw comment text
        comment = "매일 주식을 공부하고 싶으신 분들이라면 구독 좋아요!!!"
        wrapped_comment = textwrap.wrap(comment, 21)
        font_size = FONT_SIZES['TP_COMMENT']
        line_spacing = int(FONT_SIZES['TP_COMMENT'] / 2)
        height_comment = sum(font_size + line_spacing for line in wrapped_comment) - line_spacing
        y = int((CANVAS_HEIGHT - height_comment - line_spacing - subscribe_img.height) / 2)

        for line in wrapped_comment:
            text_width = draw.textlength(line, font=font_comment)
            x = (CANVAS_WIDTH - text_width) // 2
            draw.multiline_text((x, y), line, font=font_comment, fill=COLORS['WHITE'])
            y += font_size + line_spacing

        # Paste youtube subscribe button
        subsribe_img_pos_x = int((CANVAS_WIDTH - subscribe_img.width) / 2)
        subsribe_img_pos_y = y + line_spacing + FONT_SIZES['TP_COMMENT']
        scene_img.paste(subscribe_img, (subsribe_img_pos_x, subsribe_img_pos_y), subscribe_img)

        img_np = np.array(scene_img)
        duration = FINISH_SCENE_SECS
        return ImageClip(img_np, duration=duration)

    # Case when tp_current is higher or lower than tp_before
    if tp_before is None or np.isnan(tp_before):
        scene_img.paste(tp_up_img, (0, int(BANNER_HEIGHT * 2.3) + 90), tp_up_img)
    elif tp_current > tp_before:
        scene_img.paste(tp_up_img, (0, int(BANNER_HEIGHT * 2.3) + 90), tp_up_img)
        scene_img.paste(dollars_img, (0, 0), dollars_img)
    elif tp_current < tp_before:
        scene_img.paste(tp_dn_img, (0, int(BANNER_HEIGHT * 2.3) + 90), tp_dn_img)
    black_layer = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 128))
    scene_img.paste(black_layer, (0, 0), black_layer)

    # Draw underline on name
    name_width = draw.textlength(name, font=font_name)
    underline_x = int((CANVAS_WIDTH - name_width) / 2)
    underline_y = BANNER_HEIGHT + FONT_SIZES['TP_TITLE'] - 15
    underline_color = (255, 255, 255, 255)
    draw.line((underline_x, underline_y, underline_x + int(name_width), underline_y), fill=underline_color, width=20)

    # Draw name on scene_img
    name_pos = ((int(CANVAS_WIDTH - name_width) / 2), BANNER_HEIGHT)
    draw.text(name_pos, name, fill=COLORS['TP_TITLE'], font=font_name)

    # Calculate position for current target price
    tp_string = "현재 목표주가"
    tp_value = tp_current
    tp_string_max = tp_string + ' ' + f"{tp_value:,}원"
    tp_string_width = draw.textlength(tp_string, font=font_body)
    tp_string_max_width = draw.textlength(tp_string_max, font=font_body)
    tp_string_pos_x = int((CANVAS_WIDTH - tp_string_max_width) / 2) - 50
    tp_string_pos_y = BANNER_HEIGHT + FONT_SIZES['TP_TITLE'] * 2
    draw_label_box(draw, tp_string, tp_string_pos_x, tp_string_pos_y, font_body)

    # Draw current target price value
    tp_value_pos_x = tp_string_pos_x + tp_string_width + 50
    tp_value_pos_y = tp_string_pos_y
    draw.text((tp_value_pos_x, tp_value_pos_y), f"{tp_value:,}원", fill=COLORS['WHITE'], font=font_body)

    # Calulcate position for previous target price
    tpp_string = "지난 목표주가 대비"
    tpp_string_width = draw.textlength(tpp_string, font=font_body)
    tpp_string_pos_x = CANVAS_WIDTH - tpp_string_width - PADDING_LEFT
    tpp_string_pos_y = tp_string_pos_y + FONT_SIZES['TP_TITLE'] * 3
    draw_label_box(draw, tpp_string, tpp_string_pos_x, tpp_string_pos_y, font_body)

    # Draw previous target price value
    if tp_before is None or np.isnan(tp_before):
        tpp_value = "N/A"
    else:
        if tp_current - tp_before > 0:
            sign = '+'
        else:
            sign = ''
        tpp_value = sign + f"{tp_current - tp_before:,}원"
    tpp_value_width = draw.textlength(tpp_value, font=font_body)
    tpp_value_pos_x = CANVAS_WIDTH - tpp_value_width - PADDING_LEFT
    tpp_value_pos_y = tpp_string_pos_y + int(FONT_SIZES['TP_BODY'] * 1.5)
    draw.text((tpp_value_pos_x, tpp_value_pos_y), tpp_value, fill=COLORS['WHITE'], font=font_body)

    # Calculate position for current closing price
    cls_string = "현재 주가 대비"
    cls_string_width = draw.textlength(cls_string, font=font_body)
    cls_string_pos_x = CANVAS_WIDTH - cls_string_width - PADDING_LEFT
    cls_string_pos_y = tpp_value_pos_y + int(FONT_SIZES['TP_BODY'] * 2.0)
    draw_label_box(draw, cls_string, cls_string_pos_x, cls_string_pos_y, font_body)

    # Draw closing price value
    if tp_current - cls_prc > 0:
        sign = '+'
    else:
        sign = ''
    cls_value = sign + f"{tp_current - cls_prc:,}원"
    cls_value_width = draw.textlength(cls_value, font=font_body)
    cls_value_pos_x = CANVAS_WIDTH - cls_value_width - PADDING_LEFT
    cls_value_pos_y = cls_string_pos_y + int(FONT_SIZES['TP_BODY'] * 1.5)
    draw.text((cls_value_pos_x, cls_value_pos_y), cls_value, fill=COLORS['WHITE'], font=font_body)

    # Draw comment text
    comment = "매일 주식을 공부하고 싶으신 분들이라면 구독 좋아요!!!"
    wrapped_comment = textwrap.wrap(comment, 21)
    font_size = FONT_SIZES['TP_COMMENT']
    line_spacing = int(FONT_SIZES['TP_COMMENT'] / 2)
    height_comment = sum(font_size + line_spacing for line in wrapped_comment) - line_spacing
    y = cls_value_pos_y + int(FONT_SIZES['TP_BODY'] * 2.5)

    for line in wrapped_comment:
        text_width = draw.textlength(line, font=font_comment)
        x = (CANVAS_WIDTH - text_width) // 2
        draw.multiline_text((x, y), line, font=font_comment, fill=COLORS['WHITE'])
        y += font_size + line_spacing

    # Paste youtube subscribe button
    subsribe_img_pos_x = int((CANVAS_WIDTH - subscribe_img.width) / 2)
    subsribe_img_pos_y = y + line_spacing + FONT_SIZES['TP_COMMENT']
    scene_img.paste(subscribe_img, (subsribe_img_pos_x, subsribe_img_pos_y), subscribe_img)

    img_np = np.array(scene_img)
    duration = FINISH_SCENE_SECS
    return ImageClip(img_np, duration=duration)


def detect_encoding(file_like):
    """
    Detect the encoding of a file-like object.
    """
    raw_data = file_like.read(10000)
    result = chardet.detect(raw_data)
    encoding = result.get('encoding')
    file_like.seek(0)  # Reset the file pointer to the beginning
    return encoding


def read_csv_with_fallbacks(file_like):
    """
    Try reading a CSV file using multiple encodings if necessary.
    """
    # First, attempt to detect the encoding
    encoding = detect_encoding(file_like)
    if encoding is None:
        logging.error("Encoding detection failed, trying common encodings...")
        encodings_to_try = ['utf-8', 'utf-16', 'iso-8859-1', 'latin1']  # Common encodings
    else:
        encodings_to_try = [encoding, 'utf-8', 'utf-16']

    for enc in encodings_to_try:
        try:
            df = pd.read_csv(file_like, encoding=enc, delimiter='|', header=None)
            logging.info(f"File read successfully with encoding: {enc}. Number of columns: {df.shape[1]}")
            return df
        except Exception as e:
            logging.error(f"Failed to read with encoding {enc}: {e}")
            file_like.seek(0)  # Reset the file pointer to the beginning if failed

    logging.error("Failed to read the CSV with all attempted encodings.")
    return None

def process_csv(formatted_date, am_or_pm):

    sftp_server = sftp_url
    csv_file_path = f'{formatted_date}_fnshorts_combined_{am_or_pm}.csv'

    logging.info(f"csv_file_path to read: {csv_file_path}")

    # Create an SFTP client
    transport = paramiko.Transport((sftp_server, sftp_port))  # 22 is the default SSH port
    transport.connect(username=sftp_username, password=sftp_password)

    # Start the SFTP session
    sftp = paramiko.SFTPClient.from_transport(transport)

    # Retrieve the file and store it in memory
    csv_data = BytesIO()
    with sftp.file(csv_file_path, 'rb') as file_handle:
        csv_data.write(file_handle.read())

    # Move to the beginning of the file-like object
    csv_data.seek(0)

    # Close the SFTP connection after reading the file
    sftp.close()
    transport.close()

    #logging.info(f"csv_data: {csv_data}")
    logging.info(f"csv_data: {csv_data}")

    # Read the CSV file into a pandas DataFrame
    encoding = detect_encoding(csv_data)

    try:
        df = read_csv_with_fallbacks(csv_data)
        logging.info(f"File read successfully with encoding: {encoding}. Number of columns: {df.shape[1]}")
    except Exception as e:
        logging.error(f"Error reading CSV file: {e}")
        

    logging.info(f"File read successfully. Number of columns: {df.shape[1]}")
 
    # Read the CSV file into a pandas DataFrame
    try:
        column_names = [
            "STK_CD", "CMP_NM", "TRD_DT", "CLOSE_PRC", "PRE_CLOSE_PRC", "HIGH_PRC", "PRC_RTN", "HIGH_PRC_RTN", "RPT_ID",
            "ANL_DT", "RPT_TXT", "RPT_TXT_LEN", "BRK_NM_KOR", "BRK_TARGET_PRC", "BRK_TARGET_PRC_DT", "BRK_PREV_TARGET_PRC", 
            "RECOM_TYP_DT", "RECOM_TYP_AVG", "RECOM_TYP_MAX", "RECOM_TYP_MIN", "RECOM_TYP_MID", "TARGET_PRC_AVG", 
            "TARGET_PRC_AVG_3_MONTHS_BEFORE", "TARGET_PRC_MAX", "TARGET_PRC_MIN", "TARGET_PRC_MID", "TARGET_PRC_DIFF", 
            "TARGET_PRC_CHG", "TARGET_PRC_DT", "RECOM_TYP_CNT", "TARGET_PRC_CNT", "TARGET_PRC_UP_1W", "TARGET_PRC_DOWN_1W", 
            "TARGET_PRC_HOLD_1W", "TARGET_PRC_NEW_1W", "TARGET_PRC_UP_DOWN_1W", "RECENT_DT", "RECENT_CLOSE_PRC", "MOD_DT"
        ]

        # Step 3: Check if the number of columns matches
        if len(column_names) != df.shape[1]:
            raise ValueError(f"Number of column names {len(column_names)} does not match the number of columns in the file {df.shape[1]}.")

        # Step 4: Assign the column names
        df.columns = column_names

        # logging.info(f"df: {df}")

        # BRK_NM_KOR 컬럼 조건으로 특정 증권사 리포트만 영상 생성되도록 설정 (2026-02-27)

        # 조회 대상 날짜(formatted_date)를 기준으로 사용 (--manual로 과거 날짜를 지정해도 그 날짜 기준으로 필터링됨)
        target_date_str = datetime.strptime(formatted_date, "%Y%m%d").strftime("%Y-%m-%d")

        # MOD_DT에서 조회 대상 날짜가 아닌 행을 제거
        df['MOD_DT'] = pd.to_datetime(df['MOD_DT'])  # MOD_DT를 datetime 형식으로 변환
        df = df[df['MOD_DT'].dt.strftime("%Y-%m-%d") == target_date_str]

    except Exception as e:
        logging.error(f"Error processing file: {e}")
        
        return None
    
    return df


def main(rpt_mode, formatted_date, high_prc_rtn_filtering, report_limit=10):
    # Process CSV: split to columns and add column names
    try:
        df = process_csv(formatted_date, am_or_pm)
        logging.info("CSV file retrieved")
    except Exception as e:
        logging.error(f"Error adding column names to csv: {e}")
        return []

    ##################배치파일에서 고가 설정##################

    logging.info("-------------------------------------------------------------------------------------------")
    logging.info(f"## Using high_prc_rtn_filtering: {high_prc_rtn_filtering} ##")

    # high_prc_rtn_filtering 0.01 ~ 0.08 까지 회사 수 변화를 가벼운 카운트로만 확인 (진단용 로그, 실제 필터링에는 영향 없음)
    logging.info("-------------------------------------------------------------------------------------------")
    for filtering_value in [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08]:
        num_comps_at_threshold = df[df['HIGH_PRC_RTN'] > filtering_value]['STK_CD'].nunique()
        logging.info(f"## high_prc_rtn_filtering: {filtering_value}, num_comps: {num_comps_at_threshold} ##")
    logging.info("-------------------------------------------------------------------------------------------")

    #########################################################
    df = df[df['HIGH_PRC_RTN'] > high_prc_rtn_filtering] #20240903 추가
    if rpt_mode == "single":
        csv_data = read_csv_single(df)
        num_comps = csv_data['STK_CD'].nunique()
        logging.info(f"CSV_Data loaded in single mode")
    elif rpt_mode == "combined":
        csv_data = read_csv_combined(df)
        num_comps = csv_data['STK_CD'].nunique()
        logging.info(f"num_comps: {num_comps}")
        logging.info(f"CSV_Data loaded in combined mode")
    else:
        logging.error("Wrong mode. Please check mode again.")
        return []

    # 종목들의 동영상화 처리 결과를 담을 records 변수 선언
    records = []


    if rpt_mode == 'combined' and num_comps < 4:
        csv_data = read_csv_combined(df, req_rpt_num=1, is_tp_req=False)
        num_comps = csv_data['STK_CD'].nunique()

    #num_comps = 1 ### FOR DEBUGGING PURPOSE
    
    if num_comps > report_limit:
        num_comps = report_limit
    
    if csv_data is not None:
        index = 0
        while index < num_comps:
            if rpt_mode in ("single", "combined") and isinstance(csv_data, pd.DataFrame): # csv_data가 list형이면 True, 아니면 False 반환
                if index < len(csv_data):
                    row = csv_data.iloc[index]
                    ticker = row['STK_CD']
                    name = row['CMP_NM']
                    rpt_txt = row['RPT_TXT']
                    tp_current = row['TARGET_PRC_AVG']
                    tp_before = row['TARGET_PRC_AVG_3_MONTHS_BEFORE']
                    cls_prc = row['RECENT_CLOSE_PRC']
                    high_prc_rtn = row['HIGH_PRC_RTN']
                else:
                    logging.error("Index out of range for combined DataFrame.")
                    break
            else:
                logging.error("No valid data found or unsupported data format.")
                break

            rpt_id = '999999' if rpt_mode == 'combined' else row['RPT_ID']

            # if name != '팬오션':
            #     index += 1
            #     continue
            
            # Processd data check
            logging.info(f"----------------------------------------------------------------------------------------------------------------")
            logging.info(f"Name: {name}\nTicker: {ticker}\n HIGH_PRC_RTN: {high_prc_rtn}\n Report ID: {rpt_id}\n Target Price Current: {tp_current}\n Target Price 3 Months Before: {tp_before}\n Close Price: {cls_prc}")
            
            logging.info(f"----------------------------------------------------------------------------------------------------------------")
            logging.info(f"Video for {name}({ticker}): {index + 1} out of {num_comps}")

            retries, max_retries = 0, 3
            success = False
            error_description = None

            videoclips = []

            # Generate target price information clip
            try:
                tp_clip = generate_tp_scene(name, cls_prc, tp_current, tp_before)
                logging.info(f"tp_clip: {tp_clip}")
            except Exception as e:
                logging.error(f"Error generating tp scene: {e}")
                records.append({'cmp_nm': name, 'result': 'failure', 'error': f"Error generating tp scene: {e}"})
                index += 1
                continue

            try:
                while retries < max_retries and not success:
                    processed = process_full_text(rpt_txt, name)
                    if processed and all(key in processed for key in ['summary', 'keywords', 'title', 'keywords_english']):
                        summary = processed['summary']
                        keywords_english = processed['keywords_english']
                        keywords_english = ", ".join(keywords_english)
                        keywords = processed['keywords']
                        logging.info(f"keywords_english: {keywords_english}")
                        title = processed['title']
                        success = True
                    else:
                        retries += 1
                        logging.info("Processing again")
            except Exception as e:
                logging.error(f"Error processing the report: {e}")
                error_description = f"Error processing the report: {e}"

            if not success:
                records.append({'cmp_nm': name, 'result': 'failure', 'error': error_description})
                index += 1
                continue

            try:
                avatars = get_avatar_images()
            except Exception as e:
                logging.error(f"Error getting avatar images: {e}")
                records.append({'cmp_nm': name, 'result': 'failure', 'error': f"Error getting avatar images: {e}"})
                index += 1
                continue

            try:
                dialogs = generate_dialog(rpt_txt)
            except Exception as e:
                logging.error(f"Error generating dialog: {e}")
                records.append({'cmp_nm': name, 'result': 'failure', 'error': f"Error generating dialog: {e}"})
                index += 1
                continue

            len_dialog = len(dialogs["dialog"])

            if not check_dialog_properties(dialogs):
                records.append({'cmp_nm': name, 'result': 'failure', 'error': "Failed to create the dialog."})
                index += 1
                continue

            save_dialog(dialogs, processed, formatted_date, name, ticker, am_or_pm)

            try:
                for seq, dialog in enumerate(dialogs["dialog"], start=1):
                    scene_img = create_scene(processed, formatted_date, name, ticker, rpt_id)
                    scene_video = break_scene_per_sentence(scene_img, dialog, seq, len_dialog, formatted_date, name, ticker, avatars, am_or_pm, keywords_english)
                    videoclips.append(scene_video) #생성된 비디오 클립을 리스트에 추가
            except Exception as e:
                logging.error(f"Error generating scene {seq}: {e}")
                records.append({'cmp_nm': name, 'result': 'failure', 'error': f"Error generating scene: {e}"})
                index += 1
                continue

            for i, clip in enumerate(videoclips):
                if clip.duration <= 0:
                    logging.error(f"Error: Clip {i} has zero duration. Duration: {clip.duration}")
                else:
                    logging.info(f"Clip {i}: Duration {clip.duration}")

            if any(clip.duration <= 0 for clip in videoclips):
                records.append({'cmp_nm': name, 'result': 'failure', 'error': "One or more clips have zero duration."})
                index += 1
                continue

            try:
                concatenated_video = concatenate_videoclips(videoclips, method="compose") # 비디오의 크기(해상도)와 프레임 속도(fps)가 다를 때 이를 자동으로 조정해 병합하는 방식
            except Exception as e:
                records.append({'cmp_nm': name, 'result': 'failure', 'error': f"Error during concatenation: {e}"})
                index += 1
                continue

            original_duration = concatenated_video.duration
            if original_duration > MAX_SHORTS_SECS:
                logging.info(f"Video duration compressed from {original_duration} to {MAX_SHORTS_SECS}")
                play_speed_factor = concatenated_video.duration / MAX_SHORTS_SECS
                concatenated_video = concatenated_video.fx(vfx.speedx, play_speed_factor)

            # Add target price clip
            concatenated_video = concatenate_videoclips([concatenated_video, tp_clip])

            try:
                bgm = get_bgm(concatenated_video.duration) #bgm 길이 반환
                combined_audio = CompositeAudioClip([concatenated_video.audio, bgm]) #bgm 결합
                final_video = concatenated_video.set_audio(combined_audio) #결합 오디오로 교체
            except Exception as e:
                records.append({'cmp_nm': name, 'result': 'failure', 'error': f"Error inserting BGM: {e}"})
                index += 1
                continue

            # filename = f"{name}_{ticker}_shorts_with_tp.mp4"
            filename = f"{name}_{ticker}_shorts.mp4"
            output_filepath = get_output_filepath("video", filename, formatted_date, am_or_pm)
            try:
                logging.info("Writing video...")
                final_video.write_videofile(output_filepath, fps=FPS, codec="libx264", preset='ultrafast', ffmpeg_params=["-crf", "28", "-threads", "64"])
                logging.info("Writing video is complete.")
                records.append({'cmp_nm': name, 'result': 'success'})
            except Exception as e:
                records.append({'cmp_nm': name, 'result': 'failure', 'error': f"Error writing video: {e}"})

            index += 1
    
    logging.info(f"----------------------------------------------------------------------------------------------------------------")
    logging.info(f"-- END OF PROCESS --")
    logging.info(f"----------------------------------------------------------------------------------------------------------------")
    
    return records

# Main function
if __name__ == "__main__":
    
    temp_dir = "./output/temp"
    os.makedirs(temp_dir, exist_ok=True)
    
    #####################################################################
    # rpt_mode
    #     single: make a video out of one single report
    #     combined: aggregate all reports per company and make a video
    #####################################################################

    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument('threshold', nargs='?', type=float, default=0.08,
                             help='HIGH_PRC_RTN 필터링 임계값 (기본값 0.08)')
    arg_parser.add_argument('--manual', nargs=2, metavar=('DATE', 'AM_OR_PM'),
                             help="특정 날짜/세션의 CSV를 수동으로 지정해서 실행 (예: --manual 20260716 am)")
    arg_parser.add_argument('--limit', dest='report_limit', type=int, default=10,
                             help='한 번 실행 시 처리할 리포트(회사) 개수 상한 (기본값 10)')
    cli_args = arg_parser.parse_args()

    high_prc_rtn_filtering = cli_args.threshold
    report_limit = cli_args.report_limit

    if cli_args.manual:
        manual_date, manual_am_pm = cli_args.manual
        manual_am_pm = manual_am_pm.lower()
        if manual_am_pm not in ('am', 'pm'):
            raise ValueError(f"--manual의 두 번째 값은 'am' 또는 'pm'이어야 합니다: {manual_am_pm}")
        formatted_date = manual_date
        am_or_pm = manual_am_pm
        rpt_mode = 'single' if am_or_pm == 'am' else 'combined'
        logging.info(f"수동 모드: {formatted_date}_{am_or_pm} 데이터로 실행합니다.")
    else:
        formatted_date = datetime.now().strftime("%Y%m%d")
        rpt_mode, am_or_pm = ('single', 'am') if datetime.now().hour < 12 else ('combined', 'pm')

    # 콘솔 로그 외에 무인 배치 실행 후 진단이 가능하도록 파일 로그도 함께 남김
    log_dir = f"./output/{formatted_date}_{am_or_pm}"
    os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.FileHandler(os.path.join(log_dir, "run.log"), encoding="utf-8")
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logging.getLogger().addHandler(file_handler)

    start_time = datetime.now()
    result = main(rpt_mode, formatted_date, high_prc_rtn_filtering, report_limit)
    elapsed_time = datetime.now() - start_time
    logging.info(f"result: {result}")
    logging.info(f"elapsed_time: {elapsed_time}")
  