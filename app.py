import os
import json
import time
import re
import tempfile
from datetime import datetime, timedelta
from flask import Flask, request, send_file, render_template, jsonify
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from openpyxl import Workbook

app = Flask(__name__)

# 데이터 저장 폴더와 파일 경로
DATA_FOLDER = "data"
BLOG_IDS_FILE = os.path.join(DATA_FOLDER, "blog_ids.json")

# 블로그 ID 목록 불러오기
def load_blog_ids():
    if os.path.exists(BLOG_IDS_FILE):
        with open(BLOG_IDS_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    return []

# 블로그 ID 저장하기
def save_blog_ids(blog_ids):
    with open(BLOG_IDS_FILE, "w", encoding="utf-8") as file:
        json.dump(blog_ids, file, ensure_ascii=False, indent=4)

# 상대 날짜 파싱 함수 (예: "3시간 전")
def parse_relative_date(relative_str):
    now = datetime.now()
    match = re.search(r'(\d+)\s*(분|시간|일)\s*전', relative_str)
    if match:
        num = int(match.group(1))
        unit = match.group(2)
        if unit == "분":
            return now - timedelta(minutes=num)
        elif unit == "시간":
            return now - timedelta(hours=num)
        elif unit == "일":
            return now - timedelta(days=num)
    return None

# 절대 날짜 파싱 함수 (예: "2025.01.01")
def parse_absolute_date(date_str):
    fixed = re.sub(r'\s+', '', date_str).rstrip('.')
    return datetime.strptime(fixed, "%Y.%m.%d")

# 블로그 크롤링 함수
# 지정한 post_limit 만큼 게시물을 수집
def get_blog_posts(driver, blog_id, post_limit):
    blog_list_url = f"https://blog.naver.com/PostList.naver?blogId={blog_id}"
    driver.get(blog_list_url)
    time.sleep(5)
    
    wait = WebDriverWait(driver, 15)
    try:
        btn_all = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn_openlist")))
        btn_all.click()
        time.sleep(3)
    except:
        pass

    # 드롭다운 메뉴 선택 예시 (실제 CSS 선택자나 요소 이름은 페이지 구조에 따라 달라집니다)
    try:
        dropdown = Select(wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "select.some_dropdown_selector"))))
        dropdown.select_by_value(str(post_limit))  # 값이 "15"인 옵션 선택
        time.sleep(3)  # 옵션 적용 대기
    except Exception as e:
        print("드롭다운 선택 오류:", e)

    try:
        table = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.blog2_list.blog2_categorylist")))
    except:
        return []

    post_elements = table.find_elements(By.CSS_SELECTOR, "tbody tr")
    results = []
    
    for post in post_elements:
        if len(results) >= post_limit:
            break
        try:
            title_elem = post.find_element(By.CSS_SELECTOR, "td.title a")
            title = title_elem.text.strip()
            url = title_elem.get_attribute("href").split("&category")[0]

            date_elem = post.find_element(By.CSS_SELECTOR, "td.date span.date")
            date_text = date_elem.text.strip()
            post_date = parse_relative_date(date_text) if "전" in date_text else parse_absolute_date(date_text)

            if post_date:
                results.append((post_date, title, url))
        except:
            continue
    
    return results

@app.route("/", methods=["GET", "POST"])
def index():
    blog_ids = load_blog_ids()
    # 블로그 ID와 별명을 매핑 (딕셔너리 형태)
    id_to_alias = {blog["id"]: blog["alias"] for blog in blog_ids if "id" in blog and "alias" in blog}

    if request.method == "POST":
        selected_blog_ids = request.form.getlist("selected_blog_ids")
        new_blog_id = request.form.get("new_blog_id")
        new_blog_alias = request.form.get("new_blog_alias")
        post_count = request.form.get("post_count", "10")  # 기본값은 10건
        try:
            post_limit = int(post_count)
        except ValueError:
            post_limit = 10

        # 새로운 블로그 추가 처리
        if new_blog_id and new_blog_alias:
            blog_ids.append({"id": new_blog_id, "alias": new_blog_alias})
            save_blog_ids(blog_ids)
            id_to_alias[new_blog_id] = new_blog_alias

        # 선택된 블로그가 있다면 크롤링 시작
        if selected_blog_ids:
            service = Service(ChromeDriverManager().install())
            options = Options()
            options.add_argument("--headless")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")

            driver = webdriver.Chrome(service=service, options=options)

            wb = Workbook()
            ws = wb.active
            ws.append(["블로그명", "작성일", "제목", "링크"])

            for blog_id in selected_blog_ids:
                posts = get_blog_posts(driver, blog_id, post_limit)
                for post_date, title, url in posts:
                    # 날짜를 *(MM.DD) 형식으로 변환
                    formatted_date = post_date.strftime("*(%m.%d)")
                    alias = id_to_alias.get(blog_id, blog_id)
                    ws.append([alias, formatted_date, title, url])

            driver.quit()

            temp_filename = tempfile.mktemp(suffix=".xlsx")
            wb.save(temp_filename)
            return send_file(temp_filename, as_attachment=True)

    return render_template("index.html", blog_ids=blog_ids)

if __name__ == "__main__":
    os.makedirs(DATA_FOLDER, exist_ok=True)
    if not os.path.exists(BLOG_IDS_FILE):
        save_blog_ids([])
    app.run(debug=True, port=5001)



