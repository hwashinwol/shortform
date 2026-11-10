import os
import re
import time
import html
import asyncio
import datetime
import pathlib
import edge_tts
from typing import List
from pydantic import BaseModel
from html2image import Html2Image
from dotenv import load_dotenv
from openai import OpenAI
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips
from flask import Flask, render_template, request, send_file

from typing import List, Optional
from pydantic import BaseModel, Field

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WIDTH, HEIGHT = 1080, 1920

app = Flask(__name__)

# app.py 와 같은 폴더의 .env 파일에서 API 키 읽기
load_dotenv(os.path.join(BASE_DIR, '.env'))

openai_api_key = os.environ.get('OPENAI_API_KEY','').strip()
if not openai_api_key:
    raise SystemExit('OPENAI_API_KEY가 없습니다. shortform 폴더의 .env 파일을 확인하세요.')
client = OpenAI(api_key = openai_api_key)

# 크롬 스크린샷 도구 초기화 (HTML 씬을 1080x1920 PNG로 캡처)
hti = Html2Image(size = (WIDTH, HEIGHT), output_path = BASE_DIR)


LAYOUTS = ('hero', 'cards', 'steps', 'stat', 'cta')

class Item(BaseModel):
    title: Optional[str] = ""
    desc: Optional[str] = ""
    badge: Optional[str] = ""

class Scene(BaseModel):
    layout: str      # hero | cards | steps | stat | cta
    kicker: str = ""    # 타이틀 위 작은 라벨 (없으면 "")
    title: str = ""
    subtitle: str = ""
    items: List[Item] = Field(default_factory = list)
    stat_value: str = "" # stat 레이아웃의 큰 숫자 (예 : 7천만 원)
    stat_label: str = ""
    cta_title: str = ""  # 하단 강조 버튼 (없으면 "")
    cta_desc: str = ""
    tip: str = ""        # 하단 한 줄 팁 (없으면 "")
    voice: str

class Script(BaseModel):
    scenes: List[Scene]

# ---------------------------------------------------------------------------
# 1. GPT : 대본 + 씬별 레이아웃/내용 생성
# ---------------------------------------------------------------------------
def generate_script_and_design(keyword, max_retries = 5):
    """GPT-4o를 사용하여 HTML 코드와 대본(JSON)을 생성하는 모듈.
    """
    current_year = datetime.datetime.now().year
    system_instruction = f"""
    당신은 숏폼 인포그래픽 디자이너이자 기획자입니다. 
    현재 연도는 {current_year}년입니다. 연도가 들어갈 경우 반드시 {current_year}년을 반영하세요. 
    주어진 주제를 4~6개의 씬으로 나누고, 씬마다 아래 레이아웃 중 하나를 골라 화면 문구를 채웁니다.

    [레이아웃 종류]
    - intro : 첫 씬 전용. 시청자의 시선을 끄는 Hooking 용도. kicker(예 : 주제 분류/채널명), 메인 title, subtitle로 구성. items는 비움.
    - list : 병렬적인 정보(특징, 종류, 장단점, top3 등)를 나열할 때 사용. items 2~3개 활용(title, desc, 필요시 badge).
    - sequence : 순서, 흐름, 타임라인, 인과관계(기승전결)를 설명할 때 사용. items 3개 활용(title = 단계/시점, desc = 내용).
    - highlight : 결정적인 키워드, 핵심 문장, 충격적인 숫자나 통계 하나를 화면 중앙에 크게 띄워 강조할 때 사용. stat_value(강조할 텍스트/숫자), stat_label(부연 설명), items는 0~1개.
    - outro : 마지막 씬 전용. 전체 요약이나 행동 유도. title, subtitle, cta_title(예 : 구독/좋아요/공유 유도), cta_desc.
    
    [레이아웃 배치 규칙]
    - 첫 씬은 반드시 'intro', 마지막 씬은 반드시 'outro'로 고정하세요.
    - 중간 씬들은 텍스트의 맥락(나열, 순서 강조)에 맞춰 'list','sequence','highlight' 중 가장 적합한 레이아웃을 스스로 판단하여 선택하되, 시각적으로 지루하지 않게 서로 다른 레이아웃을 교차 배치하세요.
    
    [화면 문구 규칙]
    1. title은 14자 이내, subtitle은 24자 이내, items는 15~25자, badge는 6자 이내.
    2. 문구는 명사형으로 짧게 끝내고 큰따옴표나 이모지는 쓰지 마세요.
    3. 쓰지 않는 필드는 빈 문자열 ""또는 빈 배열 []로 두세요.
    4. 확실하지 않은 수치는 지어내지 말고 "공고 확인"처럼 표현하세요.

    [TTS 대본(voice) 작성 규칙]
    1. "첫 번째 씬에서는~", "이 화면은 ~을 설명합니다" 같은 상황 설명이나 지시문은 절대 넣지 마세요.
    2. 시청자에게 직접 말을 건네는 구어체 내레이션만 쓰세요. 씬당 1~3문장.
    3. '청년고용연계자금' 같은 복합 명사는 TTS가 자연스럽게 읽도록 띄어쓰기하세요.(예시 : '청년 고용 연계 자금') 

        [출력 형식]
    결과는 반드시 아래 구조의 JSON으로만 출력하세요. (Output must be in JSON)
    voice는 별도 배열로 모으지 말고, 반드시 각 씬 객체 안에 그 씬의 내레이션 문자열로 넣으세요.
    {{
      "scenes": [
        {{
          "layout": "intro",
          "kicker": "",
          "title": "",
          "subtitle": "",
          "items": [{{"title": "", "desc": "", "badge": ""}}],
          "stat_value": "",
          "stat_label": "",
          "cta_title": "",
          "cta_desc": "",
          "tip": "",
          "voice": "이 씬의 내레이션"
        }}
      ]
    }}

    """

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model = 'gpt-4o',
                messages = [
                    {'role':'system','content':system_instruction},
                    {'role':'user','content':f'주제 : {keyword}'}
                ],
                response_format = {'type' : 'json_object'}
            )

            raw_text = response.choices[0].message.content
            script = Script.model_validate_json(raw_text)


            if not script.scenes:
                raise ValueError('씬이 비어 있습니다')
            return script.model_dump()
        
        except Exception as e:
            print(f"GPT-4o 호출 실패, 3초 후 재시도 ({attempt + 1}/{max_retries}) : {e}")
            import time
            time.sleep(3)

    raise Exception('GPT-4o API 호출 실패')


# ---------------------------------------------------------------------------
# 2. 씬 → HTML 인포그래픽 → PNG
# ---------------------------------------------------------------------------
def _font_url(filename):
    return pathlib.Path(BASE_DIR, filename).as_uri()

BASE_CSS = f"""
@font-face {{ font-family: 'Gmarket'; font-weight: 300; src: url('{_font_url('GmarketSansTTFLight.ttf')}'); }}
@font-face {{ font-family: 'Gmarket'; font-weight: 500; src: url('{_font_url('GmarketSansTTFMedium.ttf')}'); }}
@font-face {{ font-family: 'Gmarket'; font-weight: 700; src: url('{_font_url('GmarketSansTTFBold.ttf')}'); }}

:root {{
    --bg: #080c14;
    --card: rgba(255,255,255,0.045);
    --line: rgba(34,211,238,0.28);
    --line-soft: rgba(255,255,255,0.09);
    --text: #ffffff;
    --sub: #9aa8bd;
    --accent: #22d3ee;
    --accent2: #38bdf8;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ width: {WIDTH}px; height: {HEIGHT}px; overflow: hidden; }}
body {{
    font-family: 'Gmarket', 'Malgun Gothic', sans-serif;
    color: var(--text);
    background:
        radial-gradient(900px 700px at 85% 8%, rgba(34,211,238,0.10), transparent 70%),
        radial-gradient(800px 800px at 0% 100%, rgba(56,189,248,0.08), transparent 70%),
        var(--bg);
    word-break: keep-all;
}}
/* 숏폼 UI(하단 자막/버튼)에 가리지 않도록 위아래 안전 여백 */
.frame {{ position: absolute; inset: 150px 90px 330px 90px; display: flex; flex-direction: column; }}
.middle {{ flex: 1; display: flex; flex-direction: column; justify-content: center; gap: 36px; }}

.kicker {{
    align-self: flex-start; font-size: 38px; font-weight: 500; color: var(--accent);
    border: 2px solid var(--line); background: rgba(34,211,238,0.08);
    padding: 16px 30px; border-radius: 999px; margin-bottom: 40px;
}}
.title {{ font-size: 96px; font-weight: 700; line-height: 1.22; letter-spacing: -2px; }}
.title.center, .subtitle.center {{ text-align: center; }}
.subtitle {{ font-size: 44px; font-weight: 500; color: var(--sub); margin-top: 26px; line-height: 1.4; }}
.accent {{ color: var(--accent); }}

.card {{
    display: flex; align-items: center; gap: 44px;
    background: var(--card); border: 2px solid var(--line);
    border-radius: 36px; padding: 50px 50px;
}}
.card .num {{ font-size: 88px; font-weight: 700; color: var(--accent); min-width: 120px; letter-spacing: -2px; }}
.card .circle {{
    flex: none; width: 96px; height: 96px; border-radius: 50%;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    color: #04121a; font-size: 48px; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
}}
.card .body {{ flex: 1; min-width: 0; }}
.card .row {{ display: flex; align-items: center; justify-content: space-between; gap: 20px; }}
.card .t {{ font-size: 54px; font-weight: 700; line-height: 1.25; }}
.card .d {{ font-size: 38px; font-weight: 500; color: var(--sub); margin-top: 16px; line-height: 1.4; }}
.card .badge {{
    flex: none; font-size: 32px; font-weight: 700; color: var(--accent);
    border: 2px solid var(--accent); background: rgba(34,211,238,0.10);
    padding: 12px 22px; border-radius: 16px;
}}
.steps .card {{ border-color: var(--line-soft); }}

.stat {{
    text-align: center; padding: 80px 40px; border-radius: 44px;
    background: linear-gradient(160deg, rgba(34,211,238,0.14), rgba(56,189,248,0.03));
    border: 2px solid var(--line);
}}
.stat .v {{ font-size: 136px; font-weight: 700; letter-spacing: -4px; color: var(--accent); line-height: 1.1; }}
.stat .l {{ font-size: 46px; font-weight: 500; color: var(--sub); margin-top: 26px; }}

.cta {{
    border-radius: 36px; padding: 52px 40px; text-align: center;
    background: linear-gradient(120deg, var(--accent), var(--accent2));
    box-shadow: 0 0 80px rgba(34,211,238,0.35);
    color: #04121a;
}}
.cta .t {{ font-size: 58px; font-weight: 700; }}
.cta .d {{ font-size: 38px; font-weight: 500; margin-top: 18px; opacity: 0.8; }}

.tip {{
    font-size: 38px; font-weight: 500; color: #fcd34d; text-align: center;
    background: rgba(252,211,77,0.07); border: 2px solid rgba(252,211,77,0.25);
    border-radius: 28px; padding: 34px 30px; line-height: 1.4;
}}
"""


def _e(text):
    return html.escape(str(text or '').strip())


def _cards_html(items, numbered = 'num'):
    parts = []
    for i, item in enumerate(items[:3], start = 1):
        if numbered == 'circle':
            marker = f'<div class="circle">{i}</div>'
        else:
            marker = f'<div class="num">{i:02d}</div>'
        badge = f'<div class="badge">{_e(item.get("badge"))}</div>' if _e(item.get('badge')) else ''
        desc = f'<div class="d">{_e(item.get("desc"))}</div>' if _e(item.get('desc')) else ''
        parts.append(
            f'<div class="card">{marker}<div class="body">'
            f'<div class="row"><div class="t">{_e(item.get("title"))}</div>{badge}</div>{desc}'
            f'</div></div>'
        )
    return ''.join(parts)


def build_scene_html(scene):
    """씬 데이터(dict)를 레이아웃별 HTML 문자열로 변환"""
    raw_layout = scene.get('layout','list')

    layout_map = {
        'intro' : 'hero',
        'highlight' : 'stat',
        'sequence' : 'steps',
        'outro' : 'cta',
        'list' : 'cards',
    }

    layout = layout_map.get(raw_layout, raw_layout)

    items = scene.get('items') or []
    kicker = f'<div class="kicker">{_e(scene.get("kicker"))}</div>' if _e(scene.get('kicker')) else ''
    subtitle = _e(scene.get('subtitle'))
    cta = ''

    if _e(scene.get('cta_title')):
        cta = (f'<div class="cta"><div class="t">{_e(scene.get("cta_title"))}</div>'
               + (f'<div class="d">{_e(scene.get("cta_desc"))}</div>' if _e(scene.get('cta_desc')) else '')
               + '</div>')
    tip = f'<div class="tip">{_e(scene.get("tip"))}</div>' if _e(scene.get('tip')) else ''
    bottom = cta or tip

    if layout == 'hero':
        body = (f'<div class="middle" style="gap:0">{kicker}'
                f'<div class="title">{_e(scene.get("title"))}</div>'
                + (f'<div class="subtitle">{subtitle}</div>' if subtitle else '')
                + '</div>' + bottom)
    elif layout == 'stat':
        stat = (f'<div class="stat"><div class="v">{_e(scene.get("stat_value"))}</div>'
                + (f'<div class="l">{_e(scene.get("stat_label"))}</div>' if _e(scene.get('stat_label')) else '')
                + '</div>')
        body = (f'{kicker}<div class="title">{_e(scene.get("title"))}</div>'
                + (f'<div class="subtitle">{subtitle}</div>' if subtitle else '')
                + f'<div class="middle">{stat}{_cards_html(items[:2])}</div>' + bottom)
    elif layout == 'steps':
        body = (f'<div class="title center">{_e(scene.get("title"))}</div>'
                + (f'<div class="subtitle center">{subtitle}</div>' if subtitle else '')
                + f'<div class="middle steps">{_cards_html(items, "circle")}</div>' + bottom)
    elif layout == 'cta':
        kicker = kicker.replace('class="kicker"', 'class="kicker" style="align-self:center"')
        body = (f'<div class="middle" style="gap:0">{kicker}'
                f'<div class="title center">{_e(scene.get("title"))}</div>'
                + (f'<div class="subtitle center">{subtitle}</div>' if subtitle else '')
                + '</div>' + (cta or tip))
    else:  # cards
        body = (f'{kicker}<div class="title">{_e(scene.get("title"))}</div>'
                + (f'<div class="subtitle">{subtitle}</div>' if subtitle else '')
                + f'<div class="middle">{_cards_html(items)}</div>' + bottom)

    return f'<div class="frame">{body}</div>'


def create_scene_visuals(scene, output_path):
    """씬을 HTML 인포그래픽으로 그려 크롬으로 1080x1920 PNG 캡처"""
    filename = os.path.basename(output_path)
    hti.output_path = os.path.dirname(os.path.abspath(output_path))
    hti.screenshot(
        html_str = f'<!doctype html><html lang="ko"><head><meta charset="utf-8"></head>'
                   f'<body>{build_scene_html(scene)}</body></html>',
        css_str = BASE_CSS,
        save_as = filename,
    )


# ---------------------------------------------------------------------------
# 3. 음성 + 영상 합성
# ---------------------------------------------------------------------------
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


def safe_filename(text):
    """파일명에 쓸 수 없는 문자 제거"""
    return re.sub(r'[\\/:*?"<>|\s]+', '_', text).strip('_') or 'shorts'


def build_dynamic_shorts(keyword, script_data):
    """HTML 렌더링 이미지와 오디오를 클립 단위로 이어붙여 렌더링"""
    scenes = script_data['scenes']
    name = safe_filename(keyword)
    video_clips = []
    temp_files = []

    try:
        for i, scene in enumerate(scenes):
            audio_path = os.path.join(BASE_DIR, f"temp_{name}_scene{i}.mp3")
            img_path = os.path.join(BASE_DIR, f"temp_{name}_scene{i}.png")
            temp_files += [audio_path, img_path]

            # 씬별 tts + 인포그래픽 생성
            generate_scene_audio(scene['voice'], audio_path)
            create_scene_visuals(scene, img_path)

            # 영상 합성 (음성 뒤에 0.4초 여유)
            audio_clip = AudioFileClip(audio_path)
            img_clip = ImageClip(img_path).set_duration(audio_clip.duration + 0.4).set_audio(audio_clip)
            video_clips.append(img_clip)

        final_video = concatenate_videoclips(video_clips, method = 'compose')
        output_filename = os.path.join(BASE_DIR, f"{name}_숏폼최종(인포그래픽).mp4")
        final_video.write_videofile(output_filename, fps = 24, codec = 'libx264', audio_codec = 'aac')
    finally:
        for clip in video_clips:
            try:
                clip.audio.close()
                clip.close()
            except Exception:
                pass
        for path in temp_files:
            try:
                os.remove(path)
            except OSError:
                pass

    return output_filename


@ app.route('/')
def index():
    """기본 접속 시 HTML 페이지 띄우기"""
    return render_template('index.html')

@ app.route('/generate', methods = ['POST'])
def generate():
    keyword = (request.form.get('keyword') or '').strip()
    print(f"\n 키워드 : {keyword} - 렌더링 시작")

    try :
        script_data = generate_script_and_design(keyword)
        output_mp4 = build_dynamic_shorts(keyword, script_data)

        # 렌더링이 끝나면 브라우저를 통해 완성된 MP4 파일 즉시 다운로드
        return send_file(output_mp4, as_attachment = True)

    except Exception as e:
        return f"렌더링 중 오류가 발생했습니다 : {e}", 500

if __name__ == '__main__':
    # 5000번 포트에서 웹 서버 실행
    app.run(host = '0.0.0.0', port = 5000, debug = True)
