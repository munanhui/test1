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
from multiprocessing import BoundedSemaphore

MAX_CONCURRENT_BROWSERS = 3  # ✅ 동시 실행 최대 3개 제한
browser_semaphore = BoundedSemaphore(MAX_CONCURRENT_BROWSERS)

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
    """
    blog_list_url = f"https://blog.naver.com/PostList.naver?blogId={blog_id}"
    driver.get(blog_list_url)
    time.sleep(5)

    wait = WebDriverWait(driver, 15)

    # '블로그' 탭 클릭
    try:
        blog_tab_xpath = "//a[contains(@class, '_param(false|blog|)')]"
        blog_tab = wait.until(EC.element_to_be_clickable((By.XPATH, blog_tab_xpath)))
        driver.execute_script("arguments[0].scrollIntoView(true);", blog_tab)
        blog_tab.click()
        time.sleep(3)
    except Exception as e:
        print("[INFO] 블로그 탭이 없거나 클릭 실패:", e)

    # (선택) mainFrame 전환
    try:
        wait.until(EC.frame_to_be_available_and_switch_to_it((By.ID, "mainFrame")))
        print("[INFO] mainFrame으로 전환 완료")
    except TimeoutException:
        print("[INFO] mainFrame이 없는 블로그일 수 있음.")

    # 카테고리 열림 상태 확인 & 전체보기 클릭
    open_whole_category(driver)

    # "전체글 보기" 버튼 클릭
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

    # ✅ 게시글을 저장할 리스트 (이거 꼭 필요함!)
    results = []

    # 예: post_limit=15 → 3페이지, post_limit=10 → 2페이지
    pages_needed = (post_limit + 4) // 5  # 5로 나눈 뒤 올림 처리

    for page_num in range(1, pages_needed + 1):
        # 게시글 목록 테이블 로딩 대기
        try:
            time.sleep(2)
            table = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.blog2_list.blog2_categorylist")))
        except Exception as e:
            print("테이블 읽기 오류:", e)
            break

        # 게시글 정보 추출
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
                print("[ERROR] 날짜 파싱 오류:", ve, "date_text:", date_text)
                continue
            except Exception as e:
                print("게시글 파싱 에러:", e)
                continue

        # 이미 원하는 개수를 다 모았으면 종료
        if len(results) >= post_limit:
            break

        # 다음 페이지 버튼 클릭
        try:
            next_selector = f"a.page.pcol2._goPageTop._param\\({page_num+1}\\)"
            next_page_link = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, next_selector)))
            driver.execute_script("arguments[0].scrollIntoView(true);", next_page_link)  
            next_page_link.click()
            time.sleep(5)  
        except TimeoutException:
            print("[INFO] 다음 페이지 버튼을 찾지 못했습니다. (마지막 페이지 가능)")
            break
        except Exception as e:
            print("[ERROR] 다음 페이지 이동 오류:", e)
            break

    return results  # ✅ 결과 반환 (이게 없으면 크롤링한 데이터가 없음!)



@app.route("/", methods=["GET", "POST"])
def index():
    blog_ids = load_blog_ids()
    id_to_alias = {blog["id"]: blog["alias"] for blog in blog_ids if "id" in blog and "alias" in blog}

    # ✅ `action`을 미리 None으로 초기화 (오류 방지)
    action = None  

    if request.method == "POST":
        action = request.form.get("action")  # ✅ action 값 가져오기

        # 1) 블로그 추가 로직
        if action == "add_blog":
            new_blog_id = request.form.get("new_blog_id", "").strip()
            new_blog_alias = request.form.get("new_blog_alias", "").strip()

            if new_blog_id and new_blog_alias:
                # 중복 체크
                duplicate = any(b["id"] == new_blog_id or b["alias"] == new_blog_alias for b in blog_ids)

                if not duplicate:
                    blog_ids.append({"id": new_blog_id, "alias": new_blog_alias})
                    save_blog_ids(blog_ids)
                    id_to_alias[new_blog_id] = new_blog_alias
                    print("[INFO] 새로운 블로그 추가 완료:", new_blog_id, new_blog_alias)
                else:
                    print("[INFO] 중복된 블로그입니다. 추가하지 않습니다.")

        # ✅ 크롤링 로직
        elif action == "crawl":
            selected_blog_ids = request.form.getlist("selected_blog_ids")
            post_count = request.form.get("post_count", "10")  # 기본값은 10건

            try:
                post_limit = int(post_count)
            except ValueError:
                post_limit = 10

            if selected_blog_ids:
                service = Service(ChromeDriverManager(driver_version="133").install())
                options = Options()

                # ✅ WebDriver 충돌 방지 및 안정성 향상
                temp_user_dir = tempfile.mkdtemp()  # ✅ 세션 충돌 방지 (고유한 사용자 디렉토리 생성)
                random_port = random.randint(9222, 9999)  # ✅ 포트 충돌 방지

                options.add_argument("--headless=new")  # ✅ 최신 헤드리스 모드 사용 (더 안정적)
                options.add_argument("--no-sandbox")  # ✅ 샌드박스 비활성화 (권한 문제 방지)
                options.add_argument("--disable-dev-shm-usage")  # ✅ /dev/shm 사용 방지 (메모리 부족 방지)
                options.add_argument("--disable-gpu")  # ✅ GPU 비활성화 (서버에서 필요 없음)
                options.add_argument("--disable-software-rasterizer")  # ✅ 소프트웨어 가속 방지
                options.add_argument("--disable-features=VizDisplayCompositor")  # ✅ 불필요한 UI 렌더링 방지
                options.add_argument("--disable-background-networking")  # ✅ 불필요한 네트워크 사용 방지
                options.add_argument("--disable-crash-reporter")  # ✅ 크래시 리포터 비활성화 (안정성 향상)
                options.add_argument("--disable-extensions")  # ✅ 확장 프로그램 비활성화
                options.add_argument("--disable-sync")  # ✅ 동기화 비활성화
                options.add_argument("--disable-logging")  # ✅ 로깅 최소화
                options.add_argument("--disable-default-apps")  # ✅ 기본 앱 비활성화
                options.add_argument("--disable-blink-features=AutomationControlled")  # ✅ Bot 감지 방지
                options.add_argument("--disable-popup-blocking")  # ✅ 팝업 차단 해제 (일부 블로그에서 필요)
                options.add_argument("--disable-client-side-phishing-detection")  # ✅ 피싱 감지 기능 비활성화
                options.add_argument("--disable-background-timer-throttling")  # ✅ 백그라운드 타이머 제한 비활성화
                options.add_argument("--disable-backgrounding-occluded-windows")  # ✅ 창이 가려져도 백그라운드로 실행
                options.add_argument("--disable-ipc-flooding-protection")  # ✅ IPC 보호 해제 (응답 속도 향상)
                options.add_argument("--disable-site-isolation-trials")  # ✅ 사이트 격리 비활성화 (메모리 절약)
                options.add_argument("--disable-renderer-backgrounding")  # ✅ 렌더링 백그라운드 제한 해제
                options.add_argument("--disk-cache-size=0")  # ✅ 디스크 캐시 사용 안 함 (메모리 절약)
                options.add_argument("--media-cache-size=0")  # ✅ 미디어 캐시 사용 안 함 (메모리 절약)
                options.add_argument("--mute-audio")  # ✅ 오디오 비활성화 (불필요한 리소스 사용 방지)

                options.add_argument(f"--user-data-dir={temp_user_dir}")  # ✅ 세션 충돌 방지
                options.add_argument(f"--remote-debugging-port={random_port}")  # ✅ 포트 충돌 방지

                # ✅ WebDriver 실행 (옵션 적용 후 실행해야 함)
                driver = webdriver.Chrome(service=service, options=options)

                # ✅ 오래된 세션 자동 종료 (10분 후 실행)
                def auto_quit(driver):
                    time.sleep(600)  # 10분 후 자동 종료
                    try:
                        driver.quit()
                        print("[INFO] 오래된 Chrome 세션 자동 종료됨.")
                    except:
                        pass

                import threading
                threading.Thread(target=auto_quit, args=(driver,), daemon=True).start()

                # ✅ 엑셀 파일 생성
                wb = Workbook()
                ws = wb.active
                ws.append(["블로그명", "작성일", "제목", "링크"])

                for blog_id in selected_blog_ids:
                    posts = get_blog_posts(driver, blog_id, post_limit)
                    for post_date, title, url in posts:
                        formatted_date = post_date.strftime("*(%m.%d)")
                        alias = id_to_alias.get(blog_id, blog_id)
                        ws.append([alias, formatted_date, title, url])

                driver.quit()

                temp_filename = tempfile.mktemp(suffix=".xlsx")
                wb.save(temp_filename)
                return send_file(temp_filename, as_attachment=True)

        # ✅ 항상 실행될 수 있도록 `if` 블록 바깥에 위치
        return render_template("index.html", blog_ids=blog_ids)






@app.route("/hello")
def hello():
    return "abc"

if __name__ == "__main__":
    os.makedirs(DATA_FOLDER, exist_ok=True)
    if not os.path.exists(BLOG_IDS_FILE):
        save_blog_ids([])
    app.run(debug=True, host='0.0.0.0', port=5001)



