import os
import json
import time
import re
import tempfile
import logging
from selenium.webdriver.remote.remote_connection import LOGGER
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
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.common.exceptions import NoSuchElementException
from openpyxl import Workbook

app = Flask(__name__)

# 데이터 저장 폴더와 파일 경로
DATA_FOLDER = "data"
BLOG_IDS_FILE = os.path.join(DATA_FOLDER, "blog_ids.json")
LOGGER.setLevel(logging.DEBUG)
logging.basicConfig(level=logging.DEBUG)

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

#카테고리 열고닫고 전체보기 클릭
def open_whole_category(driver):
    """
    1) 카테고리 목록이 접혀 있으면 펼친다 (display: none → display: block).
    2) '전체보기'(id='category0') 링크를 클릭한다.
    """
    wait = WebDriverWait(driver, 15)

    # (1) 카테고리 목록 래퍼 확인
    try:
        category_wrap = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "div#categoryListWrap")  # 실제 래퍼 셀렉터 확인 필요
        ))
        style_attr = category_wrap.get_attribute("style")  # 예: "display: none;" or "display: block;"
        if "display: none" in style_attr:
            print("[INFO] 카테고리가 접혀 있으므로 펼칩니다.")
            toggle_btn = wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "button#category-list-i")  # 실제 토글 버튼 셀렉터 확인
            ))
            driver.execute_script("arguments[0].scrollIntoView(true);", toggle_btn)
            toggle_btn.click()
            time.sleep(2)
        else:
            print("[INFO] 카테고리가 이미 열려 있음.")
    except Exception as e:
        print("[INFO] 카테고리 래퍼를 찾지 못하거나 이미 펼쳐져 있을 수 있음:", e)

    # (2) '전체보기' 링크 (id='category0') 클릭
    try:
        whole_link = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a#category0")))
        driver.execute_script("arguments[0].scrollIntoView(true);", whole_link)
        whole_link.click()
        time.sleep(2)
    except Exception as e:
        print("[ERROR] '전체보기' 링크 클릭 실패:", e)

# 블로그 크롤링 함수
# 지정한 post_limit 만큼 게시물을 수집
def get_blog_posts(driver, blog_id, post_limit):
    """
    페이지네이션을 통해 blog_id의 게시글을 최대 post_limit개까지 수집.
    (1페이지=5개로 가정)
    """
    blog_list_url = f"https://blog.naver.com/PostList.naver?blogId={blog_id}"
    driver.get(blog_list_url)
    time.sleep(5)
    
    wait = WebDriverWait(driver, 15)

    # 1) '블로그' 탭 클릭 (프롤로그가 기본인 경우 대비)
    # XPath: class 속성에 '_param(false|blog|)'를 포함하는 <a> 태그 찾기
    try:
        blog_tab_xpath = "//a[contains(@class, '_param(false|blog|)')]"
        blog_tab = wait.until(EC.element_to_be_clickable((By.XPATH, blog_tab_xpath)))
        driver.execute_script("arguments[0].scrollIntoView(true);", blog_tab)
        blog_tab.click()
        time.sleep(3)
    except Exception as e:
        print("[INFO] 블로그 탭이 없거나 클릭 실패. 이미 블로그 페이지일 수 있음:", e)

    # 2) (선택) mainFrame 전환 - 구 에디터 블로그가 mainFrame을 쓰는 경우
    try:
        wait.until(EC.frame_to_be_available_and_switch_to_it((By.ID, "mainFrame")))
    except TimeoutException:
        print("[INFO] mainFrame이 없는 블로그일 수 있음.")

        # 2-1) 카테고리 열림 상태 확인 & 전체보기 클릭
    open_whole_category(driver)

    # 3) "전체글 보기" 버튼 클릭
    try:
        btn_all = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn_openlist")))
        driver.execute_script("arguments[0].scrollIntoView(true);", btn_all)
        toggle_text_elem = btn_all.find_element(By.CSS_SELECTOR, "span#toplistSpanBlind")
        toggle_text = toggle_text_elem.text.strip()

        if "목록열기" in toggle_text:
            btn_all.click()
            time.sleep(3)
        else:
            print("[INFO] 이미 목록이 열려 있음. (목록닫기 상태) 클릭 생략")
    except Exception as e:
        print("오픈 리스트 버튼 에러:", e)

    results = []
    # 예: post_limit=15 -> 3페이지, post_limit=10 -> 2페이지
    pages_needed = (post_limit + 4) // 5  # 5로 나눈 뒤 올림 처리

    for page_num in range(1, pages_needed + 1):
        # 2) 게시글 목록 테이블 로딩 대기
        try:
            time.sleep(2)
            table = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.blog2_list.blog2_categorylist")))
        except Exception as e:
            print("테이블 읽기 오류:", e)
            break

        # 3) 게시글 정보 추출
        post_elements = table.find_elements(By.CSS_SELECTOR, "tbody tr")
        for post in post_elements:
            if len(results) >= post_limit:
                break
            try:
                title_elem = post.find_element(By.CSS_SELECTOR, "td.title a")
            except NoSuchElementException:
                print("[INFO] 게시글이 아닌 행입니다. 스킵합니다.")
                continue
            
            try:
                title = title_elem.text.strip()
                url = title_elem.get_attribute("href").split("&category")[0]

                date_elem = post.find_element(By.CSS_SELECTOR, "td.date span.date")
                date_text = date_elem.text.strip()
                if not date_text:
                     print("[INFO] 날짜가 비어있어 게시글 스킵:", title)
                     continue   

                post_date = parse_relative_date(date_text) if "전" in date_text else parse_absolute_date(date_text)

                if post_date:
                    results.append((post_date, title, url))

            except ValueError as ve:
        # strptime 실패 등 날짜 포맷 오류 처리
                print("[ERROR] 날짜 파싱 오류:", ve, "date_text:", date_text)
                continue
            except Exception as e:
                print("게시글 파싱 에러:", e)
                continue

        # 이미 원하는 개수를 다 모았으면 종료
        if len(results) >= post_limit:
            break

        # 4) 다음 페이지 버튼 클릭
        #    (예: a.page.pcol2._goPageTop._param(2))
        try:
            next_selector = f"a.page.pcol2._goPageTop._param\\({page_num+1}\\)"
            next_page_link = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, next_selector)))
            driver.execute_script("arguments[0].scrollIntoView(true);", next_page_link)  # 스크롤로 화면에 보이도록
            next_page_link.click()
            time.sleep(5)  # 페이지 이동 후 로딩 대기
        except TimeoutException:
            print("[INFO] 다음 페이지 버튼을 찾지 못했습니다. (마지막 페이지 가능)")
            break
        except Exception as e:
            print("[ERROR] 다음 페이지 이동 오류:", e)
            break

    return results


@app.route("/", methods=["GET", "POST"])
def index():
    blog_ids = load_blog_ids()
    # 블로그 ID와 별명을 매핑 (딕셔너리 형태)
    id_to_alias = {blog["id"]: blog["alias"] for blog in blog_ids if "id" in blog and "alias" in blog}

    if request.method == "POST":
        action = request.form.get("action")  # 어떤 버튼이 눌렸는지 구분 (add_blog or crawl)

        #  1) 블로그 추가 로직
        if action == "add_blog":
            new_blog_id = request.form.get("new_blog_id", "").strip()
            new_blog_alias = request.form.get("new_blog_alias", "").strip()
            
            if new_blog_id and new_blog_alias:
                # 중복 체크
                duplicate = False
                for b in blog_ids:
                    if b["id"] == new_blog_id or b["alias"] == new_blog_alias:
                        duplicate = True
                        break
                
                if duplicate:
                    print("[INFO] 중복된 블로그입니다. 추가하지 않습니다.")
                else:
                    blog_ids.append({"id": new_blog_id, "alias": new_blog_alias})
                    save_blog_ids(blog_ids)
                    id_to_alias[new_blog_id] = new_blog_alias
                    print("[INFO] 새로운 블로그 추가 완료:", new_blog_id, new_blog_alias)

        #  2) 크롤링 로직
        if action == "crawl":
            selected_blog_ids = request.form.getlist("selected_blog_ids")
            post_count = request.form.get("post_count", "10")  # 기본값은 10건
            try:
                post_limit = int(post_count)
            except ValueError:
                post_limit = 10

            # 선택된 블로그가 있다면 크롤링 시작
            if selected_blog_ids:
                service = Service(ChromeDriverManager(driver_version="133").install())
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


@app.route("/hello")
def hello():
    return "abc"

if __name__ == "__main__":
    os.makedirs(DATA_FOLDER, exist_ok=True)
    if not os.path.exists(BLOG_IDS_FILE):
        save_blog_ids([])
    app.run(debug=True, host='0.0.0.0', port=5001)


