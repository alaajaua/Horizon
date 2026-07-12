#!/usr/bin/env python3
"""Translate the latest Horizon briefing into natural Korean and deliver it.

Required:
- GOOGLE_API_KEY in .env
- data/config.json with an AI model

Optional Telegram:
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID

Optional email:
- SMTP_HOST (default: smtp.gmail.com)
- SMTP_PORT (default: 465)
- SMTP_USER
- SMTP_APP_PASSWORD
- EMAIL_TO
"""

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
        raise RuntimeError("GOOGLE_API_KEY가 .env에 설정되어 있지 않습니다.")

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = json.load(f)

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
        p
        for p in SUMMARY_DIR.glob("*.md")
        if not re.search(r"(?:^|[-_])ko$", p.stem, re.IGNORECASE)
    ]
    if not candidates:
        raise FileNotFoundError("번역할 Markdown 브리핑을 찾지 못했습니다.")

    english_candidates = [
        p
        for p in candidates
        if re.search(r"(?:^|[-_])en(?:$|[-_])", p.stem, re.IGNORECASE)
        or "english" in p.stem.lower()
    ]
    non_chinese_candidates = [
        p
        for p in candidates
        if not re.search(r"(?:^|[-_])zh(?:$|[-_])", p.stem, re.IGNORECASE)
        and "chinese" not in p.stem.lower()
    ]

    pool = english_candidates or non_chinese_candidates or candidates
    return max(pool, key=lambda p: p.stat().st_mtime)


def translate_to_korean(source_text: str, api_key: str, model: str) -> str:
    client = genai.Client(api_key=api_key)

    prompt = f"""
다음은 Horizon이 생성한 AI·기술 뉴스 데일리 브리핑이다.
원문의 사실, 수치, 날짜, 기업명, 제품명, 고유명사, URL과 출처를 빠짐없이 보존하면서
한국 독자가 읽기 쉬운 자연스러운 한국어 브리핑으로 재작성하라.

작성 원칙:
1. 직역투를 피하고, 전문적이지만 중학생도 이해할 수 있는 자연스러운 문장으로 쓴다.
2. 원문에 없는 사실이나 판단을 추가하지 않는다.
3. 공식 발표·확인된 사실과 Reddit/X/Hacker News 등 커뮤니티 반응을 명확히 구분한다.
4. 제목과 소제목을 한국어로 바꾸되 Markdown 구조는 유지한다.
5. 링크와 URL은 원문 그대로 유지한다.
6. 기술명·모델명·기업명은 통용되는 영문 표기를 유지하고 필요할 때만 한국어 설명을 덧붙인다.
7. 각 뉴스는 가능하면 다음 흐름이 드러나게 다듬는다:
   - 무슨 일이 있었나
   - 왜 중요한가
   - 핵심 세부사항 또는 한계
   - 현직자·커뮤니티 반응
8. 불필요한 수식어, 번역투, 과장 표현을 제거한다.
9. 최종 결과에는 번역 과정에 대한 설명을 붙이지 말고 완성된 Markdown 브리핑만 출력한다.

원문:
---
{source_text}
---
""".strip()

    response = client.models.generate_content(model=model, contents=prompt)
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("Gemini가 빈 번역 결과를 반환했습니다.")
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
        prefix = f"🤖 AI 트렌드 브리핑 ({index}/{len(chunks)})\n\n" if len(chunks) > 1 else "🤖 AI 트렌드 브리핑\n\n"
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
    parser.add_argument("--source", help="번역할 Markdown 파일 경로")
    parser.add_argument("--no-send", action="store_true", help="번역 파일만 생성하고 전송하지 않음")
    args = parser.parse_args()

    try:
        api_key, model = load_settings()
        source = find_latest_source(args.source)
        source_text = source.read_text(encoding="utf-8")

        print(f"📄 번역 원문: {source}")
        print(f"🤖 사용 모델: {model}")

        korean = translate_to_korean(source_text, api_key, model)
        output = output_path_for(source)
        output.write_text(korean + "\n", encoding="utf-8")
        print(f"✅ 한국어 브리핑 저장: {output}")

        if args.no_send:
            return 0

        sent_any = False

        if send_telegram(korean):
            print("✅ 텔레그램 전송 완료")
            sent_any = True
        else:
            print("ℹ️ 텔레그램 설정 없음: 전송 생략")

        date_match = re.search(r"\d{4}[-_]\d{2}[-_]\d{2}", output.stem)
        date_label = date_match.group(0).replace("_", "-") if date_match else output.stem

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
