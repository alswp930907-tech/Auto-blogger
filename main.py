# main.py
# - OpenAI로 영어 SEO 기사 작성 (미국 주식, 쉬운 영어, ~2000자)
# - 히어로 이미지는 무료 placeholder URL 사용(저작권 안전)
# - output/ 에 HTML 저장 + Blogger API로 바로 게시

import os, json, time, pathlib, datetime as dt, re, random
import requests
from openai import OpenAI

# ========= 환경변수(Secrets) =========
OPENAI_API_KEY      = os.environ.get("OPENAI_API_KEY")
CLIENT_ID           = os.environ.get("CLIENT_ID")
CLIENT_SECRET       = os.environ.get("CLIENT_SECRET")
REFRESH_TOKEN       = os.environ.get("BLOGGER_REFRESH_TOKEN")
BLOG_ID             = os.environ.get("BLOGGER_BLOG_ID")

def require_env(name, val):
    if not val:
        raise SystemExit(f"❌ Missing environment variable: {name}")
for k, v in {
    "OPENAI_API_KEY": OPENAI_API_KEY,
    "CLIENT_ID": CLIENT_ID,
    "CLIENT_SECRET": CLIENT_SECRET,
    "BLOGGER_REFRESH_TOKEN": REFRESH_TOKEN,
    "BLOGGER_BLOG_ID": BLOG_ID,
}.items():
    require_env(k, v)

# ========= 설정 =========
MODEL_TEXT  = "gpt-4o-mini"     # 텍스트 전용
OUT_DIR = pathlib.Path("output"); OUT_DIR.mkdir(exist_ok=True)
TARGET_MIN, TARGET_MAX = 1500, 2000  # 본문(평문) 길이 목표(문자 수)

client = OpenAI(api_key=OPENAI_API_KEY)

# ========= 유틸 =========
def slugify(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"[^a-z0-9\- ]+", "", t)
    t = re.sub(r"\s+", "-", t).strip("-")
    return t[:80] or "post"

def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")

def placeholder_image_url(seed: str | int) -> str:
    # 저작권 부담 없는 placeholder(랜덤). OG 권장 비율(1200x630).
    # seed는 날짜/제목 기반으로 다양화
    return f"https://picsum.photos/seed/{seed}/1200/630"

# ========= OpenAI: 기사 생성 =========
def build_prompt():
    system = (
        "You are an SEO-savvy U.S. stocks blogger. "
        "Write in plain, simple American English (grade 7–8). "
        "Be factual, neutral, and helpful. No real-time prices."
    )
    user = f"""
Write a Google-SEO-optimized blog post in ENGLISH about the U.S. stock market.

Authoring rules (must follow):
- Topic must be U.S. equities (100%).
- Start with 4–6 short lines that summarize the core ideas (Key Highlights).
- Body length target (plain text, excluding tags): {TARGET_MIN}–{TARGET_MAX} characters.
- Avoid hype and jargon; keep sentences short.
- No invented data or precise live quotes; keep it educational and evergreen.
- Include 1 hero image placeholder location in the HTML (<figure> with <img> but keep src as {{HERO_URL}}).
- Use semantic HTML only, no inline CSS.

Output JSON with:
{{
  "title": "H1 (<= 65 chars, include a core keyword)",
  "meta_description": "one sentence ~150 chars",
  "keywords": "8–12 comma-separated SEO keywords",
  "body_html": "<section>...</section> (HTML only, includes headings and lists)"
}}

Body structure guidelines:
- <p class="lede"> one-sentence hook
- A 'Key Highlights' box as a short <ul> (4–6 bullets)
- 3–6 sections with <h2>/<h3> and short paragraphs
- A simple conclusion section

Return JSON only.
"""
    return system, user

def generate_article():
    sys, usr = build_prompt()
    resp = client.chat.completions.create(
        model=MODEL_TEXT,
        messages=[{"role":"system","content":sys},
                  {"role":"user","content":usr}],
        temperature=0.4,
    )
    raw = resp.choices[0].message.content.strip()
    # 코드펜스 제거
    raw = re.sub(r"^```(json)?\s*|\s*```$", "", raw, flags=re.S)
    data = json.loads(raw)

    # 길이 보정(너무 길거나 짧으면 살짝 조정)
    plain_len = len(strip_html(data.get("body_html","")))
    if plain_len < TARGET_MIN or plain_len > TARGET_MAX:
        goal = (TARGET_MIN + TARGET_MAX)//2
        fix = client.chat.completions.create(
            model=MODEL_TEXT,
            messages=[
                {"role":"system","content":"You carefully edit HTML while preserving structure and semantics."},
                {"role":"user","content":f"Revise this HTML to ~{goal} characters (plain text). Keep headings and lists.\n\n{data.get('body_html','')}"},
            ],
            temperature=0.2,
        )
        fixed_html = fix.choices[0].message.content.strip()
        data["body_html"] = fixed_html

    return data

# ========= Blogger API 호출 =========
def get_access_token() -> str:
    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise SystemExit(f"❌ Token refresh failed: {r.status_code} {r.text}")
    return r.json()["access_token"]

def post_to_blogger(title: str, html: str) -> dict:
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
    }
    body = {
        "kind": "blogger#post",
        "blog": {"id": BLOG_ID},
        "title": title,
        "content": html,
        # 필요 시 라벨 사용:
        # "labels": ["US Stocks","AutoPost"],
    }
    url = f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts/"
    r = requests.post(url, headers=headers, data=json.dumps(body).encode("utf-8"), timeout=60)
    if r.status_code not in (200, 201):
        raise SystemExit(f"❌ Blogger post failed: {r.status_code} {r.text}")
    return r.json()

# ========= HTML 렌더 =========
def render_full_html(meta: dict, hero_url: str) -> str:
    title = meta["title"].strip()
    meta_desc = meta["meta_description"].strip()
    keywords = meta.get("keywords","").strip()
    body = meta["body_html"]

    # body 안의 {{HERO_URL}} 치환(프롬프트에서 figure 자리 마련)
    body = body.replace("{{HERO_URL}}", hero_url)

    return f"""<!doctype html>
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
    <figure><img src="{hero_url}" alt="U.S. stock market hero image"></figure>
    {body}
  </article>
</body>
</html>"""

# ========= 메인 =========
def main():
    # 1) 글 생성
    meta = generate_article()

    # 2) 무료 이미지 URL(저작권 안전)
    seed = f"{dt.datetime.utcnow():%Y%m%d}-{random.randint(1000,9999)}"
    hero_url = placeholder_image_url(seed)

    # 3) HTML 저장
    slug = slugify(meta["title"])
    date_str = dt.datetime.utcnow().strftime("%Y-%m-%d")
    html_doc = render_full_html(meta, hero_url)

    out_path = OUT_DIR / f"{date_str}-{slug}.html"
    out_path.write_text(html_doc, encoding="utf-8")
    print(f"✅ Saved: {out_path}")

    # 4) 블로그에 게시(공개)
    res = post_to_blogger(meta["title"], html_doc)
    post_url = res.get("url") or res.get("selfLink") or "(no url)"
    print("✅ Published:", post_url)

if __name__ == "__main__":
    main()
