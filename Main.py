# main.py
# 1) GDELT 최신 뉴스 수집
# 2) OpenAI로 영어 SEO 기사 작성(쉬운 영어 + 1,800~2,200자)
# 3) 썸네일 생성(gpt-image-1)
# 4) 완성 HTML을 output/YYYY-MM-DD-slug.html 로 저장

import os, json, re, time, pathlib, datetime as dt
import requests
from openai import OpenAI

# ===== 설정 =====
QUERY = "korea economic growth"     # 영문 2~3단어 이상 권장 (예: "us stock market")
MAX_RECORDS = 10                    # 기사 개수(1~50)
MODEL_TEXT = "gpt-4o-mini"
MODEL_IMAGE = "gpt-image-1"

OUT_DIR = pathlib.Path("output")
OUT_DIR.mkdir(parents=True, exist_ok=True)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise SystemExit("❌ OPENAI_API_KEY 환경변수가 필요합니다. (GitHub Actions에서는 Secrets로 넣습니다)")

client = OpenAI(api_key=OPENAI_API_KEY)

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

TARGET_MIN = 1800   # 본문(plain text) 최소 길이
TARGET_MAX = 2200   # 본문(plain text) 최대 길이

# ===== 유틸 =====
def fetch_gdelt(query: str, maxrecords: int = 10):
    """GDELT에서 JSON 기사 목록 가져오기 (간단 재시도 포함)"""
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": maxrecords,
        "sort": "datedesc",
    }
    for _ in range(3):
        try:
            r = requests.get(GDELT_URL, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(6)
        except Exception:
            time.sleep(3)
    return {"articles": []}

def slugify(text: str):
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\- ]+", "", text)
    text = re.sub(r"\s+", "-", text).strip("-")
    return text[:80] if text else "post"

def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()

# ===== 프롬프트 =====
PROMPT_SYSTEM = """
You are an SEO-savvy blog writer. Use plain, simple English (around U.S. grade 7–8).
Avoid jargon, rare words, and long sentences. Prefer short sentences.
Write factually and neutrally, with helpful context. No hype.
"""

def build_prompt(articles, query):
    # 기사 목록을 요약된 열(날짜|제목|URL)로 전달
    bullets = []
    for a in articles[:MAX_RECORDS]:
        t = a.get("title") or ""
        s = (a.get("seendate") or a.get("date") or "")[:10]
        u = a.get("url") or ""
        bullets.append(f"- {s} | {t} | {u}")
    joined = "\n".join(bullets) if bullets else "(no articles)"

    user = f"""
Generate an ENGLISH blog article optimized for Google SEO using **plain English** (grade 7–8).
**Body length target** (excluding HTML tags): 1,800–2,200 characters. Do not exceed this range.

Topic (seed keyword): {query}

Recent sources (from GDELT):
{joined}

Return a single JSON object with these fields:
- title: SEO-optimized H1 (<= 65 chars) including the core keyword naturally
- meta_description: one-sentence meta (~150 chars, no period at end if possible)
- keywords: 8–12 SEO keywords, comma-separated
- hero_alt: short alt text for the hero image
- image_style: short image style description (e.g., "flat illustration, warm palette")
- body_html: VALID HTML for the article body. Rules:
  * Start with <p class="lede"> one-sentence hook.
  * Use 3–6 sections with <h2>/<h3> headings. Keep sentences short.
  * Include a small bullet list for "Key Takeaways".
  * End with a "Sources" section listing the URLs from above in <ul><li><a> format.
  * No inline CSS, no external scripts.
  * Neutral, factual tone. No invented data.

Return ONLY the JSON (no commentary).
"""
    return PROMPT_SYSTEM, user

# ===== 생성 함수 =====
def generate_article(articles, query):
    sys, user = build_prompt(articles, query)
    resp = client.chat.completions.create(
        model=MODEL_TEXT,
        messages=[{"role": "system", "content": sys},
                  {"role": "user", "content": user}],
        temperature=0.3,
    )
    raw = resp.choices[0].message.content.strip()
    raw = re.sub(r"^```(json)?\s*|\s*```$", "", raw, flags=re.S)  # code fence 제거
    data = json.loads(raw)

    # 길이 보정: 본문이 범위를 벗어나면 자동 축약/확장
    body_html = data.get("body_html", "")
    body_html = adjust_length_with_model(body_html)
    data["body_html"] = body_html
    return data

def adjust_length_with_model(html_text: str) -> str:
    plain = strip_html(html_text)
    n = len(plain)
    if TARGET_MIN <= n <= TARGET_MAX:
        return html_text  # 이미 범위 내

    direction = "shrink" if n > TARGET_MAX else "expand"
    goal = (TARGET_MIN + TARGET_MAX) // 2
    prompt = f"""
I will give you HTML for a blog post body. Please {direction} the body text so its plain-text length
is close to {goal} characters. Keep **plain English (grade 7–8)**, short sentences, and the same HTML structure.
Return HTML only, no explanations.

CURRENT HTML:
{html_text}
"""
    resp = client.chat.completions.create(
        model=MODEL_TEXT,
        messages=[{"role":"system","content":"You carefully edit HTML while preserving structure."},
                  {"role":"user","content":prompt}],
        temperature=0.2,
    )
    new_html = resp.choices[0].message.content.strip()
    # 최종 안전 체크: 너무 길면 문단 끝에서 조금 잘라내기
    final_plain = strip_html(new_html)
    if len(final_plain) > TARGET_MAX:
        # 문장 단위로 줄이기
        parts = re.split(r"(?<=[\.!?])\s+", final_plain)
        while len(" ".join(parts)) > TARGET_MAX and len(parts) > 1:
            parts.pop()
        # 구조 보존을 위해 간단 치환(완벽하진 않지만 길이 안전장치)
        shortened = " ".join(parts)
        # 문장 트림을 HTML에 반영하기 어렵기 때문에, 과도한 초과에서만 원본 반환 방지
        if len(shortened) >= TARGET_MIN:
            # 아주 단순한 치환: 기존 본문에서 텍스트만 치환(정교한 매핑은 모델 재호출이 안전)
            new_html = f"<section><p>{shortened}</p></section>"
    return new_html

def generate_image(prompt_text: str):
    # 썸네일 프롬프트
    p = f"Blog hero image about: {prompt_text}. No text or watermark. Minimal, modern, editorial, data-themed, 16:9."
    img = client.images.generate(model=MODEL_IMAGE, prompt=p, size="1024x1024")
    return img.data[0].url

def render_html(meta, hero_url):
    title = meta["title"]
    meta_desc = meta["meta_description"]
    keywords = meta.get("keywords", "")
    hero_alt = meta.get("hero_alt", "blog hero")
    body = meta["body_html"]

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <meta name="description" content="{meta_desc}">
  <meta name="keywords" content="{keywords}">
</head>
<body>
  <article>
    <h1>{title}</h1>
    <figure>
      <img src="{hero_url}" alt="{hero_alt}">
    </figure>
    {body}
  </article>
</body>
</html>"""
    return html

# ===== 메인 =====
def main():
    data = fetch_gdelt(QUERY, MAX_RECORDS)
    articles = data.get("articles", [])
    if not articles:
        print("No articles fetched from GDELT. Exiting.")
        return

    meta = generate_article(articles, QUERY)
    # 이미지 프롬프트는 간결하게: image_style이 있으면 우선 사용
    img_seed = meta.get("image_style") or meta.get("title") or QUERY
    hero_url = generate_image(img_seed)

    slug = slugify(meta["title"])
    date_str = dt.datetime.utcnow().strftime("%Y-%m-%d")
    out_path = OUT_DIR / f"{date_str}-{slug}.html"

    html_doc = render_html(meta, hero_url)
    out_path.write_text(html_doc, encoding="utf-8")
    print(f"✅ Saved: {out_path}")

if __name__ == "__main__":
    main()
