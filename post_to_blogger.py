# post_to_blogger.py
# GitHub Secrets의 CLIENT_ID / CLIENT_SECRET / BLOGGER_REFRESH_TOKEN로
# OAuth 자격을 구성해 Blogger API(v3)로 최신 생성 HTML을 업로드합니다.

import os, glob, pathlib, datetime as dt
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

CLIENT_ID = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["BLOGGER_REFRESH_TOKEN"]
BLOG_ID = os.environ["BLOGGER_BLOG_ID"]

OUT_DIR = pathlib.Path("output")

def get_latest_html():
    files = sorted(OUT_DIR.glob("*.html"))
    if not files:
        raise SystemExit("❌ 업로드할 HTML이 없습니다. 먼저 main.py가 실행되어야 합니다.")
    return files[-1]

def blogger_service():
    creds = Credentials(
        None,
        refresh_token=REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/blogger"],
    )
    return build("blogger", "v3", credentials=creds)

def main():
    html_path = get_latest_html()
    title = html_path.stem  # 파일명 기반 제목
    html = html_path.read_text(encoding="utf-8")

    service = blogger_service()
    body = {
        "kind": "blogger#post",
        "title": title,
        "content": html,
    }
    post = service.posts().insert(blogId=BLOG_ID, body=body, isDraft=False).execute()
    print(f"✅ Published: https://www.blogger.com/blog/post/edit/{BLOG_ID}/{post['id']}")

if __name__ == "__main__":
    main()
