# 1번째
# main.py
# 1) GDELT에서 최신 뉴스 수집
# 2) OpenAI로 영어 SEO 기사 작성 (쉬운 단어/짧은 문장/<=2000자 plain-text)
# 3) 썸네일 1장 생성 (gpt-image-1)
# 4) 완성 HTML을 output/YYYY-MM-DD-slug.html 저장

import os, json, re, time, pathlib, datetime as dt
import requests
from openai import OpenAI

# ===== 설정 =====
QUERY = "korea economic growth"   # 원하는 검색어(영문 2~3단어 이상 권장)
MAX_RECORDS = 10                  # 기사 개수(1~50)
CHAR_LIMIT = 2000                 # 본문 plain-text 최대 글자수
MODEL_TEXT = "gpt-4o-mini"
MODEL_IMAGE = "gpt-image-1"
OUT_DIR = pathlib.Path("output")
OUT_DIR.mkdir(parents=True, exist_ok=True)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise SystemExit("❌ OPENAI_API_KEY env var missing. Put it in GitHub Secrets.")

client = OpenAI(api_key=OPENAI_API_KEY)
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

# ===== 유틸 =====
def fetch_gdelt(query: str, maxrecords: int = 10):
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": maxrecords,
        "sort": "datedesc",
    }
    for _ in range(4):
        r = requests.get(GDELT_URL, params=params, timeout=25)
        if r.status_code == 200:
            try:
                return r.json()
            except Exception:
                pass
        time.sleep(5)
    return {"articles": []}

def slugify(text: str):
    text = (text or "post").lower()
    text = re.sub(r"[^a-z0-9\- ]+", "", text)
    text = re.sub(r"\s+", "-", text).strip("-")
    return text[:80] or "post"

def strip_html_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html or "")

def plain_len(html: str) -> int:
    return len(strip_html_tags(html))

# ===== 프롬프트 =====
PROMPT_SYSTEM = (
    "You are a financial journalist and SEO editor.\n"
    "Write in **clear American English** with **Grade 7–8 readability**.\n"
    "Prefer short sentences. Avoid jargon and complex words."
)

def build_prompt(articles, query, char_limit):
    bullets = []
    for a in articles[:MAX_RECORDS]:
        t = a.get("title") or ""
        s = (a.get("seendate") or "")[:10]
        u = a.get("url") or ""
        bullets.append(f"- {s} | {t} | {u}")
    joined = "\n".join(bullets) if bullets else "(no articles)";

    user = f"""
Create a Google‑SEO‑optimized blog post in ENGLISH about: **{query}**.

Use the sources list (recent items from GDELT):
{joined}

Return a single JSON with:
- title: SEO H1 (<= 70 chars, include the core keyword naturally)
- meta_description: ~150 chars, enticing, no clickbait
- keywords: 8-12 SEO keywords as a comma-separated string
- outline: 5-8 headings (mix of H2/H3 strings)
- hero_alt: short alt text for the hero image
- image_style: short image style guide (e.g., "flat illustration, warm palette")
- body_html: FULL article body as clean semantic HTML (no inline CSS)
  Rules:
  * Grade 7–8 vocabulary, short sentences, neutral and factual tone
  * Start with a short lede paragraph
  * Follow the outline using <h2>/<h3>, then <p>, <ul>/<ol> as needed
  * Include a 'Key Takeaways' box as a bullet list
  * Add a 'Sources' section with a <ul> of the source URLs above
  * Keep plain-text length **<= {char_limit} characters** (exclude HTML tags)
  * Do not include external scripts or CSS
"""
    return user

# ===== 생성 =====
def generate_article(articles, query, char_limit):
    user = build_prompt(articles, query, char_limit)
    resp = client.chat.completions.create(
        model=MODEL_TEXT,
        messages=[
            {"role": "system", "content": PROMPT_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0.4,
    )
    raw = resp.choices[0].message.content.strip()
    raw = re.sub(r"^```(json)?\s*|\s*```$", "", raw, flags=re.S)
    data = json.loads(raw)

    # 안전장치: 길이가 넘치면 모델에게 한 번 더 축약 요청
    body_html = data.get("body_html", "")
    if plain_len(body_html) > CHAR_LIMIT:
        body_html = shorten_with_model(body_html, CHAR_LIMIT)
        data["body_html"] = body_html
    return data

def shorten_with_model(html_body: str, char_limit: int) -> str:
    msg = (
        f"Rewrite the following HTML article so the **plain-text** length is <= {char_limit} characters. "
        "Keep headings and lists. Use Grade 7–8 vocabulary and short sentences. Return only HTML."
    )
    resp = client.chat.completions.create(
        model=MODEL_TEXT,
        messages=[
            {"role": "system", "content": "You are a concise copy editor."},
            {"role": "user", "content": msg + "\n\n" + html_body},
        ],
        temperature=0.2,
    )
    html2 = resp.choices[0].message.content.strip()
    html2 = re.sub(r"^```(html)?\s*|\s*```$", "", html2, flags=re.S)
    # 최종 길이 확인 후 초과 시 아주 약하게 자르기(안전망)
    text_len = plain_len(html2)
    if text_len > char_limit:
        txt = strip_html_tags(html2)
        txt = txt[:char_limit]
        # 최소 래퍼만 붙여 간단히 감싸줌
        html2 = f"<section><p>{txt}</p></section>"
    return html2

def generate_image(prompt_text: str) -> str:
    p = (
        f"Hero image for a blog post about: {prompt_text}. "
        "No text. minimal, modern, editorial, data theme."
    )
    img = client.images.generate(model=MODEL_IMAGE, prompt=p, size="1024x1024")
    return img.data[0].url

def render_html(meta: dict, hero_url: str) -> str:
    title = meta.get("title", "Article")
    meta_desc = meta.get("meta_description", "")
    keywords = meta.get("keywords", "")
    hero_alt = meta.get("hero_alt", "blog hero")
    body_html = meta.get("body_html", "")

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
    <figure><img src="{hero_url}" alt="{hero_alt}"></figure>
    {body_html}
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

    meta = generate_article(articles, QUERY, CHAR_LIMIT)

    # 이미지 프롬프트는 간결하게: image_style이 있으면 우선 사용
    seed = meta.get("image_style") or meta.get("title") or QUERY
    hero_url = generate_image(seed)

    slug = slugify(meta.get("title", "post"))
    date_str = dt.datetime.utcnow().strftime("%Y-%m-%d")
    out_path = OUT_DIR / f"{date_str}-{slug}.html"

    html_doc = render_html(meta, hero_url)
    out_path.write_text(html_doc, encoding="utf-8")
    print(f"✅ Saved: {out_path} (plain length={plain_len(meta.get('body_html',''))})")

if __name__ == "__main__":
    main()
