import os
import json
import time
import asyncio
import datetime
import requests
import urllib.parse
import edge_tts
from PIL import Image, ImageDraw, ImageFont
from html2image import Html2Image
from dotenv import load_dotenv
from google import genai
from google.genai import types
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips
from flask import Flask, render_template, request, send_file

app = Flask(__name__)

# app.py 와 같은 폴더의 .env 파일에서 API 키 읽기
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

gemini_api_key = os.environ.get('GEMINI_API_KEY', '').strip()
if not gemini_api_key:
    raise SystemExit('GEMINI_API_KEY가 없습니다. shortform 폴더의 .env 파일을 확인하세요.')
client = genai.Client(api_key = gemini_api_key)

# 크롬 스크린샷 도구 초기화
hti = Html2Image(size = (1080, 1920))

def generate_dynamic_script_and_design(keyword, max_retries = 5):
    """LLM이 대본도 생성화고 매 씬의 UI/UX 레이아웃도 HTML/CSS로 디자인하여 반환""" 
    current_year = datetime.datetime.now().year
    system_instruction = f"""
    당신은 숏폼 인포그래픽 디자이너이자 기획자입니다. 
    현재 연도는 {current_year}년입니다. 대본이나 화면 텍스트에 연도가 들어갈 경우 반드시 최신 연도({current_year}년)를 반영하세요.
    주어진 주제를 3~4개의 씬으로 나누고, 화면 디자인은 HTML이 아닌 AI이미지 생성용 '영문 프롬프트'로 지시합니다. 

    [디자인 필수 규칙]
    1. image_prompt : 9:16 세로형 다크모드 배경을 묘사하는 디테일한 영문 프롬프트(예 : Dark mode abstract background, glowing neon cyan geometric lines, 9:16 vertical)
    2. 화면 텍스트(screen_title, screen desc)에는 큰따옴표나 복잡한 기호를 빼고 핵심만 적으세요.

    [TTS 대본(voice) 작성 규칙]
    1. "첫 번째 씬에서는~", "이 화면은 ~을 설명합니다" 같은 상황 설명이나 지시문은 절대 넣지 마세요.
    2. 시청자에게 직접 말을 건네는 '구어체 내레이션' 대본만 즉시 시작하세요(예 : "혹시 일시적 경영 애로 자금이라고 들어보셨나요? 지금부터 신청 방법을 알려드리겠습니다." 등)
    3. '청년고용연계자금'이나 '소상공인대리대출' 같은 복합 명사나 전문 용어는 TTS가 어색하게 읽지 않도록 반드시 자연스럽게 띄어쓰기를 포함하여 대본(voice)을 작성하세요.(예 : '청년 고용 연계 자금', '소상공인 대리 대출')

    반드시 아래 JSON 형식으로 응답하세요.
    {{
        "scenes": [
            {{
                "image_prompt": "영문 배경 프롬프트",
                "screen_title": "메인 타이틀 텍스트",
                "screen_desc": "서브 설명 텍스트",
                "voice": "TTS가 읽을 순수 내레이션 구어체 대본"
            }}
        ]
    }}
    """

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model = 'gemini-3.8-flash',
                contents = f"주제 : {keyword}",
                config = types.GenerateContentConfig(
                    system_instruction = system_instruction,
                    response_mime_type = 'application/json',
                )
            )
            raw_text = response.text.strip()
            if raw_text.startswith("```json"): raw_text = raw_text[7:]
            if raw_text.startswith("```"): raw_text = raw_text[3:]
            if raw_text.endswith("```"): raw_text = raw_text[:-3]
            return json.loads(raw_text.strip())
        except Exception as e:
            print(f"Gemini 호출 실패, 3초 후 재시도 ({attempt+1}/{max_retries}) : {e}")
            time.sleep(3)
    raise Exception('Gemini API 호출 실패')

def create_scene_visuals(prompt, title, desc, output_path):
    """Pollinations.ai로 무료 배경 생성 후 파이썬으로 텍스트 오버레이"""
    # 1. 프롬프트를 URL용으로 인코딩하여 Pollinations API 호출(nologo=true 로 워터마크 제거 시도)
    encoded_prompt = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&nologo=true"

    response = requests.get(url)
    if response.status_code == 200:
        with open(output_path, 'wb') as f:
            f.write(response.content)
    else:
        # 통신 실패 시 검은 배경 생성
        img = Image.new('RGB', (1080,1920), color = (20,20,30))
        img.save(output_path)

    # 2. Pillow로 다크 반투명 박스 및 텍스트 합성
    img = Image.open(output_path).convert('RGBA').resize((1080, 1920))
    overlay = Image.new('RGBA', img.size, (255,255,255,0))
    draw = ImageDraw.Draw(overlay)

    try:
        font_title = ImageFont.truetype('GmarketSansTTFBold.ttf', 90)
        font_desc = ImageFont.truetype('GmarketSansTTFLight.ttf', 60)
    except:
        print('폰트를 찾을 수 없어 기본 폰트로 고정합니다')
        font_title = font_desc = ImageFont.load_default()

    # 가독성을 위한 반투명 박스(중앙 정렬)
    draw.rounded_rectangle([90,700,990,1220], radius = 40, fill = (10,15,25,220))

    # 박스 폭(좌우 여백 포함 약 820px)에 맞게 줄바꿈
    def wrap_text(text, font, max_width):
        lines, line = [], ''
        for word in text.split():
            candidate = f"{line} {word}" if line else word
            if draw.textlength(candidate, font = font) <= max_width:
                line = candidate
                continue
            if line:
                lines.append(line)
            # 한 단어가 박스보다 길면 글자 단위로 자름
            line = ''
            for ch in word:
                if draw.textlength(line + ch, font = font) <= max_width:
                    line += ch
                else:
                    lines.append(line)
                    line = ch
        if line:
            lines.append(line)
        return '\n'.join(lines)

    # 텍스트 중앙 정렬 배치 (타이틀은 위, 설명은 아래)
    draw.multiline_text((540, 820), wrap_text(title, font_title, 820), fill = (255,255,255,255), font = font_title, anchor = 'mm', align = 'center', spacing = 16)
    draw.multiline_text((540, 1060), wrap_text(desc, font_desc, 820), fill = (220,225,235,255), font = font_desc, anchor = 'mm', align = 'center', spacing = 14)

    final_img = Image.alpha_composite(img, overlay).convert('RGB')
    final_img.save(output_path)

def generate_scene_audio(text, output_path, max_retries = 5):
    """오디오 생성"""
    for attempt in range(max_retries):
        try:
            async def _amain():
                communicate = edge_tts.Communicate(text, 'ko-KR-InJoonNeural')
                await communicate.save(output_path)
            asyncio.run(_amain())
            return
        except Exception as e:
            time.sleep(3)
            print(e)
    raise Exception("Edge TTS 호출 최종 실패")

def build_dynamic_shorts(keyword, script_data):
    """HTML 렌더링 이미지와 오디오를 클립 단위로 이어붙여 렌더링"""
    scenes = script_data['scenes']
    video_clips = []

    for i, scene in enumerate(scenes):
        audio_path = f"temp_{keyword}_scene{i}.mp3"
        img_path = f"temp_{keyword}_scene{i}.png"

        # 씬별 tts 생성
        generate_scene_audio(scene['voice'], audio_path)
        create_scene_visuals(scene['image_prompt'], scene['screen_title'], scene['screen_desc'], img_path)

        # 영상 합성
        audio_clip = AudioFileClip(audio_path)
        img_clip = ImageClip(img_path).set_duration(audio_clip.duration).set_audio(audio_clip)
        video_clips.append(img_clip)

    final_video = concatenate_videoclips(video_clips, method = 'compose')
    output_filename = f"{keyword}_숏폼최종(폴리네이션).mp4"
    final_video.write_videofile(output_filename, fps = 24, codec = 'libx264', audio_codec = 'aac')
   
    for i in range(len(scenes)): 
        try:
            os.remove(f"temp_{keyword}_scene{i}.mp3")
            os.remove(f"temp_{keyword}_scene{i}.png")
        except:
            pass

    return output_filename

@ app.route('/')
def index():
    """기본 접속 시 HTML 페이지 띄우기"""
    return render_template('index.html')

@ app.route('/generate', methods = ['POST'])
def generate():
    keyword = request.form.get('keyword')
    print(f"\n 키워드 : {keyword} - 렌더링 시작")

    try : 
        script_data = generate_dynamic_script_and_design(keyword)
        output_mp4 = build_dynamic_shorts(keyword, script_data)

        # 렌더링이 끝나면 브라우저를 통해 완성된 MP4 파일 즉시 다운로드
        return send_file(output_mp4, as_attachment = True)
    
    except Exception as e: 
        return f"렌더링 중 오류가 발생했습니다 : {e}", 500

if __name__ == '__main__':
    # 5000번 포트에서 웹 서버 실행
    app.run(host = '0.0.0.0', port = 5000, debug = True)