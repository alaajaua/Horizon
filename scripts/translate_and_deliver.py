#!/usr/bin/env python3
"""Rewrite the latest Horizon briefing into easy Korean and deliver it."""

from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
import sys
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

import markdown
import requests
from dotenv import load_dotenv
from google import genai


ROOT = Path(__file__).resolve().parents[1]
SUMMARY_DIR = ROOT / "data" / "summaries"
CONFIG_PATH = ROOT / "data" / "config.json"


def load_settings() -> tuple[str, str]:
    load_dotenv(ROOT / ".env")

    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY가 .env 또는 GitHub Secrets에 설정되어 있지 않습니다.")

    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = json.load(file)

    model = str(config.get("ai", {}).get("model", "")).strip()
    if not model:
        raise RuntimeError("data/config.json의 ai.model을 찾을 수 없습니다.")

    return api_key, model


def find_latest_source(explicit_source: str | None = None) -> Path:
    if explicit_source:
        path = Path(explicit_source).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"지정한 파일이 없습니다: {path}")
        return path

    if not SUMMARY_DIR.exists():
        raise FileNotFoundError(
            f"브리핑 폴더가 없습니다: {SUMMARY_DIR}\n"
            "먼저 Horizon을 실행해 브리핑을 생성하세요."
        )

    candidates = [
        path
        for path in SUMMARY_DIR.glob("*.md")
        if not re.search(r"(?:^|[-_])ko$", path.stem, re.IGNORECASE)
    ]
    if not candidates:
        raise FileNotFoundError("재작성할 Markdown 브리핑을 찾지 못했습니다.")

    english_candidates = [
        path
        for path in candidates
        if re.search(r"(?:^|[-_])en(?:$|[-_])", path.stem, re.IGNORECASE)
        or "english" in path.stem.lower()
    ]
    non_chinese_candidates = [
        path
        for path in candidates
        if not re.search(r"(?:^|[-_])zh(?:$|[-_])", path.stem, re.IGNORECASE)
        and "chinese" not in path.stem.lower()
    ]

    pool = english_candidates or non_chinese_candidates or candidates
    return max(pool, key=lambda path: path.stat().st_mtime)


def clean_model_output(text: str) -> str:
    text = text.strip()
    fenced = re.fullmatch(
        r"```(?:markdown|md)?\s*\n(?P<body>.*)\n```",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fenced:
        return fenced.group("body").strip()
    return text


def translate_to_korean(source_text: str, api_key: str, model: str) -> str:
    client = genai.Client(api_key=api_key)

    prompt = f"""
너는 AI·기술 뉴스를 일반 독자가 빠르고 정확하게 이해하도록 편집하는
한국어 데일리 브리핑 에디터다.

아래 Horizon 원문을 단순 번역하지 말고, 핵심 내용을 선별한 뒤
쉽고 자연스러운 한국어 데일리 브리핑으로 다시 작성하라.

독자:
- AI·개발 비전공자
- 중학생도 큰 어려움 없이 이해할 수 있는 설명을 원하는 사람
- AI 에이전트, 업무 자동화, 마케팅, AEO·GEO·SEO,
  이커머스, 시장조사, 무역 실무에 관심이 있는 사람

핵심 목표:
- 전체를 약 4~6분 안에 읽을 수 있어야 한다.
- 오늘의 중요한 변화가 무엇인지 먼저 이해하게 한다.
- 공식 사실, 의미 해석, 커뮤니티 반응을 서로 섞지 않는다.
- 뉴스의 양보다 중요도와 이해도를 우선한다.

절대 원칙:
1. 원문에 없는 사실, 숫자, 날짜, 인용, 평가를 새로 만들지 않는다.
2. 기업명, 제품명, 모델명, 수치, 날짜, URL과 출처를 정확히 보존한다.
3. 공식 발표나 확인된 내용은 '사실'로, 영향에 대한 설명은 '왜 중요한가'로 구분한다.
4. Reddit, X, Hacker News 등의 의견은 '현장 반응'으로 따로 표시한다.
5. 일부 사용자의 경험을 전체의 공통 의견처럼 확대 해석하지 않는다.
6. 반응이 엇갈리거나 검증이 부족하면 '아직 확인이 필요하다'고 쓴다.
7. 어려운 용어는 처음 등장할 때 한 번만 쉬운 뜻을 붙인다.
8. 문장은 짧게 쓰고, 한 문장에는 하나의 핵심만 담는다.
9. 번역투, 과장 표현, 불필요한 수식어, 같은 내용의 반복을 제거한다.
10. 원문에 근거가 없는 실무 아이디어는 억지로 만들지 않는다.
11. 최종 결과에는 편집 과정이나 지시문에 대한 설명을 넣지 않는다.
12. Markdown 코드 블록으로 전체 결과를 감싸지 않는다.

뉴스 선별 기준:
- 가장 중요한 뉴스는 최대 5개만 '핵심 뉴스 TOP 5'에 넣는다.
- 중요도는 산업 영향, 사용자 영향, 기술 변화의 크기,
  여러 출처의 동시 주목 여부를 기준으로 판단한다.
- 비슷한 뉴스는 하나로 통합한다.
- 단순 제품 홍보, 작은 기능 변경, 반복 보도는 줄이거나 제외한다.
- 핵심 뉴스에 들지 못했지만 관찰할 가치가 있는 내용은
  '짧게 볼 소식'에 최대 3개만 넣는다.

최종 출력 형식:

# 오늘의 AI 트렌드 브리핑

> 읽는 시간: 약 4~6분

## 오늘 꼭 알아야 할 3가지
- 오늘 가장 중요한 변화 3개를 각각 한 문장으로 쓴다.
- 이 부분만 읽어도 전체 흐름을 이해할 수 있어야 한다.

## 핵심 뉴스 TOP 5

각 뉴스는 아래 형식을 따른다.

### 1. 쉬운 한국어 제목

**사실**
무슨 일이 있었는지 2~4문장으로 설명한다.
배경지식이 없어도 이해할 수 있어야 한다.

**왜 중요한가**
산업, 기업, 일반 사용자 또는 업무 방식에 미칠 영향을
1~2문장으로 설명한다.
이 부분이 추론이라면 단정하지 말고 가능성으로 표현한다.

**현장 반응**
원문에 Reddit, X, Hacker News, 개발자 또는 사용자의 반응이 실제로 있을 때만 쓴다.
다수 의견인지, 일부 경험인지, 의견이 갈리는지 구분한다.
반응이 없으면 이 항목 자체를 만들지 않는다.

**출처**
출처명 · 날짜 · URL
원문에 날짜가 없으면 날짜를 새로 만들지 않는다.

## 새 도구·오픈소스
GitHub, 신규 AI 도구, 오픈소스 프로젝트 가운데
실제로 알아둘 가치가 있는 것만 최대 3개 소개한다.

각 항목은 아래 한 줄 형식으로 쓴다.
- **이름** — 무엇을 하는 도구인지 / 왜 주목받는지 / 판단: 지금 시험 · 관찰 · 보류

도구의 안정성이나 실사용 가능성이 원문에서 확인되지 않으면
'관찰' 또는 '보류'로 표시한다.

## 내 업무에 연결
시장조사, 마케팅, 콘텐츠, 이커머스, 무역, 문서 작업,
업무 자동화와 직접 연결되는 내용이 있을 때만 최대 2개 작성한다.

각 항목은 아래 형식으로 쓴다.
- **적용 아이디어:** 오늘 바로 해볼 수 있는 작은 실험
- **근거:** 어떤 뉴스에서 나온 아이디어인지
- **주의:** 아직 검증되지 않은 부분이 있으면 표시

직접 연결되는 내용이 없으면 이 섹션을 생략한다.

## 아직 지켜봐야 할 신호
과장 가능성, 검증 부족, 의견 충돌, 정책 불확실성처럼
확정적으로 받아들이면 안 되는 내용이 있을 때만 최대 2개 쓴다.
없으면 이 섹션을 생략한다.

## 짧게 볼 소식
핵심 뉴스에는 들지 않았지만 알아둘 만한 내용을 최대 3개,
각각 한 문장으로 정리한다.
없으면 이 섹션을 생략한다.

편집 기준:
- '한 문장으로 말하면', '기억할 포인트', '오늘의 한 줄 결론'처럼
  앞 내용과 반복되는 별도 항목은 만들지 않는다.
- 표는 사용하지 않는다.
- 긴 문단을 피하고, Telegram에서 읽기 좋은 간격으로 작성한다.
- 이모지는 제목에 과하게 사용하지 않는다.
- 최종 결과는 완성된 Markdown 본문만 출력한다.

원문:
---
{source_text}
---
""".strip()

    response = client.models.generate_content(model=model, contents=prompt)
    text = clean_model_output(response.text or "")
    if not text:
        raise RuntimeError("Gemini가 빈 한국어 재작성 결과를 반환했습니다.")
    return text


def output_path_for(source: Path) -> Path:
    stem = re.sub(r"(?:[-_])en$", "", source.stem, flags=re.IGNORECASE)
    return source.with_name(f"{stem}-ko.md")


def split_text(text: str, limit: int = 3800) -> Iterable[str]:
    text = text.strip()
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        yield text[:cut].strip()
        text = text[cut:].strip()
    if text:
        yield text


def send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False

    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = list(split_text(text))

    for index, chunk in enumerate(chunks, start=1):
        prefix = (
            f"🤖 AI 트렌드 브리핑 ({index}/{len(chunks)})\n\n"
            if len(chunks) > 1
            else "🤖 AI 트렌드 브리핑\n\n"
        )
        response = requests.post(
            endpoint,
            json={
                "chat_id": chat_id,
                "text": prefix + chunk,
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        response.raise_for_status()
        time.sleep(0.5)

    return True


def send_email(text: str, date_label: str) -> bool:
    host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
    port = int(os.getenv("SMTP_PORT", "465"))
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_APP_PASSWORD", "").replace(" ", "").strip()
    recipient = os.getenv("EMAIL_TO", "").strip()

    if not user or not password or not recipient:
        return False

    msg = EmailMessage()
    msg["Subject"] = f"[AI 트렌드 브리핑] {date_label}"
    msg["From"] = f"AI Trend Briefing <{user}>"
    msg["To"] = recipient
    msg.set_content(text)

    html = markdown.markdown(text, extensions=["extra", "sane_lists"])
    msg.add_alternative(
        f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Noto Sans KR", sans-serif;
       line-height: 1.7; max-width: 760px; margin: 32px auto; padding: 0 20px; }}
h1, h2, h3 {{ line-height: 1.35; }}
a {{ word-break: break-all; }}
blockquote {{ border-left: 4px solid #ddd; margin-left: 0; padding-left: 16px; color: #555; }}
code {{ background: #f4f4f4; padding: 2px 5px; border-radius: 4px; }}
</style>
</head>
<body>{html}</body>
</html>""",
        subtype="html",
    )

    with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)

    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", help="재작성할 Markdown 파일 경로")
    parser.add_argument(
        "--no-send",
        action="store_true",
        help="한국어 파일만 생성하고 Telegram·이메일 전송은 생략",
    )
    args = parser.parse_args()

    try:
        api_key, model = load_settings()
        source = find_latest_source(args.source)
        source_text = source.read_text(encoding="utf-8")

        print(f"📄 재작성 원문: {source}")
        print(f"🤖 사용 모델: {model}")

        korean = translate_to_korean(source_text, api_key, model)
        output = output_path_for(source)
        output.write_text(korean + "\n", encoding="utf-8")
        print(f"✅ 쉬운 한국어 브리핑 저장: {output}")

        if args.no_send:
            return 0

        sent_any = False

        if send_telegram(korean):
            print("✅ 텔레그램 전송 완료")
            sent_any = True
        else:
            print("ℹ️ 텔레그램 설정 없음: 전송 생략")

        date_match = re.search(r"\d{4}[-_]\d{2}[-_]\d{2}", output.stem)
        date_label = (
            date_match.group(0).replace("_", "-")
            if date_match
            else output.stem
        )

        if send_email(korean, date_label):
            print("✅ 이메일 전송 완료")
            sent_any = True
        else:
            print("ℹ️ 이메일 설정 없음: 전송 생략")

        if not sent_any:
            print("ℹ️ 전송 채널이 설정되지 않아 한국어 파일만 생성했습니다.")

        return 0

    except Exception as exc:
        print(f"❌ 실패: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
