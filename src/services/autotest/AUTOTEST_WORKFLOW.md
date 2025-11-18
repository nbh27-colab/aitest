# AutoTest Workflow Architecture

## Tổng quan
AutoTest Workflow sử dụng mô hình LangGraph để tự động hóa kiểm thử end-to-end cho web application. Workflow được thiết kế tổng quát, thích ứng với nhiều loại UI và flow thực tế, sử dụng LLM để sinh action và xác thực kết quả.

---

## Luồng chính của Workflow

1. **INITIALIZE**
   - Nhận vào `test_case_id`, `login_info_id`.
   - Load test case, các step, thông tin login từ DB.
   - Khởi động browser (Playwright), tạo context và page.
   - Khởi tạo các biến trạng thái (`AutoTestState`).

2. **AUTO_LOGIN**
   - Điều hướng đến URL login.
   - Tìm form login (nhiều selector).
   - Điền email/username, password, click submit.
   - Chụp screenshot, xác thực login thành công.
   - Nếu step 1 là login thì đánh dấu đã hoàn thành và skip.
   - Có thể xử lý các flow login phức tạp (chọn phương thức, popup, chuyển hướng).

3. **GET_CONTEXT**
   - Trích xuất trạng thái hiện tại của trang:
     - URL, tiêu đề, heading chính.
     - Các element tương tác (button, input, link).
     - DOM snapshot, screenshot, console log.
     - Lịch sử kết quả các substep trước.

4. **GENERATE_SUBSTEP**
   - Dùng LLM để sinh kế hoạch substep tiếp theo dựa trên mục tiêu step và context thực tế.
   - Kết quả gồm: mô tả substep, loại action (click/fill/verify...), selector, điều kiện xác thực, có phải substep cuối không.
   - Kiểm tra trùng lặp, tránh lặp vô hạn.
   - Lưu substep vào DB, sinh script Playwright.

5. **EXECUTE_SUBSTEP**
   - Thực thi script Playwright vừa sinh.
   - Chụp screenshot, post-verification.
   - Lưu kết quả thực thi, trạng thái pass/fail, lỗi (nếu có), screenshot vào DB/MinIO.
   - Nếu substep fail, workflow vẫn tiếp tục sinh substep mới (nếu chưa đạt điều kiện kết thúc step).

6. **VALIDATE_STEP**
   - Dùng LLM để xác thực kết quả step dựa trên DOM/URL/screenshot.
   - Trả về: đã hoàn thành chưa, confidence, lý do, evidence.
   - Nếu LLM xác thực step đã hoàn thành (is_completed=True, confidence>=0.7), có thể bỏ qua các substep fail trước đó và chuyển sang step tiếp theo.

7. **DECISION_NEXT**
   - Nếu step đã hoàn thành (theo validation) → chuyển step tiếp theo.
   - Nếu chưa, tiếp tục sinh substep mới (tối đa 10 substep/step).
   - Nếu stuck (3 lần không đổi), hoặc fail liên tiếp 5 lần → kết thúc workflow.
   - Nếu đã hết step → kết thúc workflow.

8. **CLEANUP**
   - Đóng page, browser, dọn tài nguyên.
   - Tổng hợp kết quả: số step/substep pass/fail, log chi tiết, trạng thái cuối cùng.

---

## Mô tả chi tiết các Node

### 1. INITIALIZE
**Loại:** Setup node  
**Đầu vào:** `test_case_id`, `login_info_id`  
**Chức năng:**
- Load test case từ database theo `test_case_id`
- Load danh sách steps của test case (theo `step_order`)
- Load thông tin login từ `login_info_id`
- Khởi tạo Playwright browser (chromium, headless=False, slow_mo=500ms)
- Tạo browser context và page mới
- Khởi tạo các biến trạng thái:
  - `current_step_index = 0`
  - `current_substep_index = 0`
  - `completed_steps = []`
  - `login_completed = False`
  - `overall_status = 'running'`
  - `substep_plans = []`
  - `execution_results = []`
  - `generated_scripts = []`

**Đầu ra:** `AutoTestState` đã được khởi tạo đầy đủ

---

### 2. AUTO_LOGIN
**Loại:** Action node  
**Đầu vào:** State với `login_info` đã load  
**Chức năng:**
- Navigate đến `web_url` từ `login_info`
- Chụp screenshot trang login ban đầu, upload lên MinIO
- Sử dụng LLM để phát hiện và thực hiện flow login:
  - **Hỗ trợ simple login:** Email + password cùng trang
  - **Hỗ trợ multi-step login:** Email → Next → Password (như Microsoft, AWS)
  - **Hỗ trợ OAuth/SSO:** Chọn phương thức đăng nhập (Google, Facebook, Microsoft) → chuyển hướng → nhập thông tin
  - **Hỗ trợ popup login:** Tự động nhận diện và thao tác với popup window
- Mỗi bước login được sinh động bởi LLM dựa trên context thực tế (tối đa 10 attempts)
- LLM quyết định action tiếp theo: `enter_email`, `enter_password`, `click_submit`, `wait`, `select_login_method`, v.v.
- Sau khi submit, xác thực login thành công bằng LLM (kiểm tra URL change, element mới, v.v.)
- Lưu từng substep login vào DB (sub_step, generated_script, test_result, screenshot)
- Nếu **Step 1** trong testcase là login → đánh dấu step đó là completed và skip

**Đầu ra:** State với `login_completed = True`, danh sách substep login đã lưu DB

---

### 3. GET_CONTEXT
**Loại:** Extract node  
**Đầu vào:** State với `page` đang hoạt động  
**Chức năng:**
- Trích xuất trạng thái hiện tại của trang:
  - **URL hiện tại:** `page.url`
  - **Tiêu đề trang:** `page.title()`
  - **Heading chính:** `<h1>`, `<h2>` đầu tiên
  - **Các element tương tác:** Buttons, inputs, links, selects (với text, selector, visible state)
  - **DOM snapshot:** Cấu trúc HTML đầy đủ (dạng tree)
  - **Screenshot:** Base64 encoded
  - **Console logs:** Lỗi hoặc thông báo từ browser console
  - **Lịch sử execution_results:** Kết quả các substep trước đó
- Track page state để phát hiện stuck:
  - Lưu hash của HTML (5000 ký tự đầu)
  - Lưu URL hiện tại
  - So sánh với 3 lần trước, nếu không đổi → đánh dấu `page_stuck_detected = True`
- Format context thành dạng dễ đọc cho LLM

**Đầu ra:** State với `page_context` chứa toàn bộ thông tin trang

---

### 4. GENERATE_SUBSTEP
**Loại:** LLM node  
**Đầu vào:** State với `page_context`, `current_step`, `substep_index`  
**Chức năng:**
- Gửi request đến LLM (GPT-4) với:
  - **Step action:** Mô tả bước cần làm
  - **Expected result:** Kết quả mong đợi
  - **Page context:** Trạng thái trang hiện tại
  - **Previous results:** Lịch sử substep trước (để tránh lặp lại)
  - **Error learnings:** Các lỗi đã gặp (để LLM tránh lặp lỗi)
  - **Page stuck warning:** Nếu trang không đổi sau 3 lần
- LLM trả về `SubStepPlan`:
  - `substep_description`: Mô tả hành động cần làm
  - `action_type`: click | fill | select | verify | wait | navigate
  - `target_element`: Selector(s) của element cần thao tác
  - `action_value`: Giá trị cần điền (nếu action là fill)
  - `verification`: Điều kiện kiểm tra sau khi thực hiện
  - `is_final_substep`: Boolean - có phải substep cuối của step không
- **Duplicate detection:** Kiểm tra nếu substep giống với 3 substep gần nhất → force complete step
- **Destructive action prevention:** Nếu LLM sinh action logout/sign out mà goal không yêu cầu → override thành verify
- Lưu substep vào DB (`sub_step` table)
- Sinh Playwright script từ plan bằng LLM
- Lưu script vào DB (`generated_script` table)

**Đầu ra:** State với `substep_plans` được append, `current_substep_id` được set

---

### 5. EXECUTE_SUBSTEP
**Loại:** Execution node  
**Đầu vào:** State với `current_substep_id`, `generated_scripts`  
**Chức năng:**
- Load script Playwright vừa sinh từ DB
- Thực thi script trong runtime Python:
  ```python
  exec(script_content, globals_dict)
  result = await execute_substep_X(page)
  ```
- Các loại action được hỗ trợ:
  - **Click:** `await page.click(selector, timeout=5000)`
  - **Fill:** `await page.fill(selector, value)`
  - **Select:** `await page.select_option(selector, value)`
  - **Navigate:** `await page.goto(url)`
  - **Wait:** `await page.wait_for_selector(selector)` hoặc `wait_for_timeout`
  - **Verify:** Kiểm tra element visible, text content, URL, v.v.
- Chụp screenshot sau khi thực thi, upload lên MinIO
- **Post-verification:** Kiểm tra lại kết quả sau khi thực thi:
  - Nếu verification pass → `success = True`
  - Nếu verification fail → `success = False`, nhưng có thể detect "intermediate progress" (button disabled, modal opened, v.v.)
- Lưu kết quả thực thi vào DB:
  - `test_result`: object_type='sub_step', result=True/False, reason
  - `screenshot`: link đến MinIO
- Update `consecutive_failures` counter nếu fail

**Đầu ra:** State với `execution_results` được append, screenshot đã lưu

---

### 6. VALIDATE_STEP
**Loại:** LLM validation node  
**Đầu vào:** State với `current_step`, `page` HTML/URL  
**Chức năng:**
- Lấy HTML hiện tại của trang, clean (remove scripts, styles, noscript)
- Track page state history để phát hiện stuck:
  - Hash HTML hiện tại
  - So sánh với lần validation trước
  - Nếu giống nhau 3 lần → force complete step (confidence=0.5)
- Gửi request đến LLM với:
  - **Step action:** Mô tả bước cần làm
  - **Expected result:** Kết quả mong đợi
  - **Page HTML:** HTML đã clean
  - **Current URL:** URL hiện tại
  - **Substep description:** Mô tả substep vừa thực hiện (nếu có)
- LLM phân tích và trả về `ValidationResult`:
  - `is_completed`: Boolean - step đã hoàn thành chưa
  - `confidence`: 0.0-1.0 - độ tin cậy của kết quả
  - `reason`: Giải thích tại sao completed/not completed
  - `evidence`: Trích dẫn từ HTML/URL làm bằng chứng
- Lưu kết quả vào `state['last_validation']`
- **Override execution result:** Nếu substep execution fail nhưng LLM validation pass → coi như step đã hoàn thành

**Đầu ra:** State với `last_validation` chứa kết quả từ LLM

---

### 7. DECISION_NEXT (Conditional Edge)
**Loại:** Router/Decision node  
**Đầu vào:** State đầy đủ  
**Chức năng:** Quyết định action tiếp theo dựa trên nhiều điều kiện:

**Kiểm tra theo thứ tự:**
1. **Check overall_status:** Nếu `completed` hoặc `error` → **finish**
2. **Check step index:** Nếu `current_step_index >= len(steps)` → **finish**
3. **Check max substeps:** Nếu `current_substep_index >= 10` → **next_step** (tránh vô hạn)
4. **Check consecutive failures:** Nếu `>= 5` → **finish** (quá nhiều lỗi)
5. **Check LLM validation** (nếu confidence >= 0.7):
   - Nếu `is_completed = True` → **next_step**
   - Nếu `is_completed = False`:
     - Nếu `consecutive_no_change >= 3` (stuck) → **next_step**
     - Nếu `consecutive_failures >= 3` → **next_step** (bỏ qua step này)
     - Nếu `confidence < 0.6` và `failures >= 2` → **next_step**
     - Ngược lại → **continue_substeps**
6. **Fallback - Check execution result:**
   - Nếu substep fail:
     - Nếu `consecutive_failures >= 3` → **next_step**
     - Nếu `is_final_substep = True` → **continue_substeps** (retry)
     - Ngược lại → **continue_substeps**
   - Nếu substep success:
     - Nếu `is_final_substep = True` → **next_step**
     - Ngược lại → **continue_substeps**

**Đầu ra:** Một trong 3 giá trị: `"continue_substeps"`, `"next_step"`, `"finish"`

---

### 8. CONTINUE_SUBSTEPS
**Loại:** Helper node  
**Đầu vào:** State hiện tại  
**Chức năng:**
- Kiểm tra workflow chưa finish
- Increment `current_substep_index += 1`
- **Không reset** gì khác

**Đầu ra:** State với substep index tăng lên → loop về **GET_CONTEXT**

---

### 9. MOVE_TO_NEXT_STEP
**Loại:** Helper node  
**Đầu vào:** State hiện tại  
**Chức năng:**
- Đánh dấu step hiện tại là completed: `completed_steps.append(current_step_index)`
- Increment `current_step_index += 1`
- **Reset trạng thái substep:**
  - `current_substep_index = 0`
  - `substep_plans = []`
  - `consecutive_failures = 0`
  - `current_substep_id = None`
- Skip các step đã completed (nếu có)
- Nếu hết step → set `overall_status = 'completed'`

**Đầu ra:** State với step mới → loop về **GET_CONTEXT**

---

### 10. CLEANUP
**Loại:** Teardown node  
**Đầu vào:** State cuối cùng  
**Chức năng:**
- Đóng Playwright page
- Đóng browser context
- Stop Playwright
- Tính toán `overall_status`:
  - Nếu tất cả steps completed → `'passed'`
  - Nếu có step failed → `'failed'`
  - Nếu có error → `'error'`
- Log summary statistics:
  - Số step completed/total
  - Số substep passed/failed/total
  - Thời gian thực thi
- Set `end_time`

**Đầu ra:** State cuối cùng với trạng thái tổng hợp

---

## Mô tả chi tiết các Edge (Kết nối giữa các Node)

### 1. INITIALIZE → AUTO_LOGIN
**Loại:** Unconditional edge (luôn chuyển)  
**Điều kiện:** Sau khi khởi tạo thành công  
**Mục đích:** Bắt đầu quy trình đăng nhập tự động

---

### 2. AUTO_LOGIN → GET_CONTEXT
**Loại:** Unconditional edge  
**Điều kiện:** Sau khi login hoàn tất (hoặc skip nếu step 1 là login)  
**Mục đích:** Lấy context trang sau khi đăng nhập để bắt đầu thực hiện các step

---

### 3. GET_CONTEXT → GENERATE_SUBSTEP
**Loại:** Unconditional edge  
**Điều kiện:** Sau khi lấy context thành công  
**Mục đích:** Sinh substep tiếp theo dựa trên context vừa lấy

---

### 4. GENERATE_SUBSTEP → EXECUTE_SUBSTEP
**Loại:** Unconditional edge  
**Điều kiện:** Sau khi sinh substep và script thành công  
**Mục đích:** Thực thi script vừa sinh

---

### 5. EXECUTE_SUBSTEP → VALIDATE_STEP
**Loại:** Unconditional edge  
**Điều kiện:** Sau khi thực thi substep (dù pass hay fail)  
**Mục đích:** Xác thực kết quả step bằng LLM

---

### 6. VALIDATE_STEP → DECISION_NEXT (Conditional Edges)
**Loại:** Conditional edge - quyết định routing dựa trên logic  
**Các nhánh:**

#### a. DECISION_NEXT → CONTINUE_SUBSTEPS
**Điều kiện:**
- Step chưa hoàn thành (theo LLM validation)
- Chưa vượt quá max substeps (10)
- Chưa quá nhiều failures (<5)
- Chưa stuck (page đổi được)

**Mục đích:** Sinh thêm substep mới để tiếp tục thực hiện step hiện tại

#### b. DECISION_NEXT → MOVE_TO_NEXT_STEP
**Điều kiện:**
- Step đã hoàn thành (theo LLM validation với confidence >= 0.7), HOẶC
- Đã vượt quá max substeps (10), HOẶC
- Quá nhiều failures (>=3), HOẶC
- Page stuck (không đổi sau 3 lần), HOẶC
- Substep cuối và thực thi thành công

**Mục đích:** Chuyển sang step tiếp theo

#### c. DECISION_NEXT → CLEANUP (finish)
**Điều kiện:**
- `overall_status` đã là 'completed' hoặc 'error', HOẶC
- Đã hết step (`current_step_index >= len(steps)`), HOẶC
- Quá nhiều consecutive failures (>=5)

**Mục đích:** Kết thúc workflow, dọn dẹp tài nguyên

---

### 7. CONTINUE_SUBSTEPS → GET_CONTEXT
**Loại:** Unconditional edge  
**Điều kiện:** Sau khi tăng substep index  
**Mục đích:** Lặp lại vòng context → generate → execute → validate cho substep mới

---

### 8. MOVE_TO_NEXT_STEP → GET_CONTEXT
**Loại:** Unconditional edge  
**Điều kiện:** Sau khi chuyển sang step mới  
**Mục đích:** Lặp lại vòng context → generate → execute → validate cho step mới

---

### 9. CLEANUP → END
**Loại:** Unconditional edge  
**Điều kiện:** Sau khi cleanup hoàn tất  
**Mục đích:** Kết thúc workflow, trả kết quả về API

---

## Sơ đồ luồng chi tiết

```
START
  ↓
┌─────────────┐
│ INITIALIZE  │ (Load data, khởi tạo browser)
└──────┬──────┘
       ↓
┌─────────────┐
│ AUTO_LOGIN  │ (Đăng nhập tự động, LLM-driven)
└──────┬──────┘
       ↓
       ┌──────────────────────────────────────────────┐
       │              MAIN WORKFLOW LOOP              │
       │  (Lặp cho từng step, mỗi step có nhiều       │
       │   substep cho đến khi đạt điều kiện)         │
       │                                              │
       │  ┌─────────────┐                            │
       │  │ GET_CONTEXT │ (Lấy trạng thái trang)     │
       │  └──────┬──────┘                            │
       │         ↓                                    │
       │  ┌──────────────────┐                       │
       │  │ GENERATE_SUBSTEP │ (LLM sinh action)     │
       │  └────────┬─────────┘                       │
       │           ↓                                  │
       │  ┌──────────────────┐                       │
       │  │ EXECUTE_SUBSTEP  │ (Thực thi Playwright) │
       │  └────────┬─────────┘                       │
       │           ↓                                  │
       │  ┌──────────────┐                           │
       │  │ VALIDATE_STEP│ (LLM xác thực kết quả)   │
       │  └──────┬───────┘                           │
       │         ↓                                    │
       │  ┌──────────────┐                           │
       │  │ DECISION_NEXT│ (Quyết định tiếp theo)    │
       │  └──┬───┬───┬───┘                           │
       │     │   │   │                                │
       │     │   │   └─────────────────────┐          │
       │     │   │                         ↓          │
       │     │   │                    ┌─────────┐     │
       │     │   │                    │ CLEANUP │─────┼──> END
       │     │   │                    └─────────┘     │
       │     │   │                                    │
       │     │   └──────────────┐                     │
       │     │                  ↓                     │
       │     │           ┌────────────────┐           │
       │     │           │ MOVE_TO_NEXT   │           │
       │     │           │     STEP       │           │
       │     │           └───────┬────────┘           │
       │     │                   │                    │
       │     └──────┐            │                    │
       │            ↓            │                    │
       │     ┌──────────────┐   │                    │
       │     │  CONTINUE    │   │                    │
       │     │  SUBSTEPS    │   │                    │
       │     └──────┬───────┘   │                    │
       │            │            │                    │
       │            └────────────┘                    │
       │                  │                           │
       │                  └──> Loop back to           │
       │                       GET_CONTEXT            │
       └──────────────────────────────────────────────┘
```

---

## Workflow Control Flow (Luồng điều khiển)

### Vòng lặp chính
Workflow có 2 vòng lặp chính:

#### 1. Vòng lặp Step (Outer Loop)
```
for each step in steps:
    while not step_completed:
        → GET_CONTEXT
        → GENERATE_SUBSTEP
        → EXECUTE_SUBSTEP
        → VALIDATE_STEP
        → DECISION_NEXT
        
        if step_completed (validated by LLM):
            → MOVE_TO_NEXT_STEP
            break
        else:
            → CONTINUE_SUBSTEPS
            continue
```

#### 2. Vòng lặp Substep (Inner Loop)
```
for each substep (max 10):
    → Generate action dựa trên context hiện tại
    → Execute action
    → Validate kết quả
    
    if validation confirms step completed:
        break inner loop
    if too many failures or stuck:
        break inner loop
    else:
        continue to next substep
```

### Điều kiện thoát vòng lặp

**Thoát Inner Loop (chuyển step):**
- LLM validation xác nhận step completed (confidence >= 0.7)
- Đã thực hiện 10 substeps (max limit)
- Consecutive failures >= 3
- Page stuck (không đổi 3 lần)
- Substep cuối thành công

**Thoát Outer Loop (kết thúc workflow):**
- Đã hoàn thành tất cả steps
- Overall status = 'error' hoặc 'completed'
- Consecutive failures >= 5 (workflow error)

---

## Đặc điểm nổi bật

### 1. Sequential Generation (Sinh tuần tự)
- Substeps được sinh **từng cái một**, không pre-plan toàn bộ
- Mỗi substep nhìn thấy trạng thái **thực tế** của page sau action trước
- LLM quyết định action tiếp theo dựa trên context hiện tại, không dựa vào kế hoạch cũ

### 2. Context Awareness (Nhận biết ngữ cảnh)
- Trước mỗi substep, extract fresh page context
- Include: URL, DOM elements, screenshot, previous results, errors
- LLM có đầy đủ thông tin để đưa ra quyết định chính xác

### 3. Adaptive Execution (Thực thi thích ứng)
- Handle dynamic UI (modals, redirects, dropdowns, popups)
- Retry với backup selectors nếu selector chính fail
- Adjust theo những gì thực sự xảy ra trên trang

### 4. Validation-Based Completion (Hoàn thành dựa trên validation)
- Không chỉ dựa vào execution success/fail
- Sử dụng LLM validation để check DOM/URL
- Có thể override execution result nếu LLM confident
- Step có thể pass dù có substep fail, nếu trạng thái cuối cùng đạt yêu cầu
- Prevent stuck states (duplicate plans, no page changes)

### 5. Loop Prevention (Ngăn chặn vòng lặp vô hạn)
- Max 10 substeps per step
- Duplicate plan detection (window=3)
- Stuck detection (3 lần page không đổi)
- Consecutive failures limit (5)
- Destructive action prevention (không logout nếu goal không yêu cầu)
- Ghi nhận trạng thái pass/fail từng substep, tổng hợp lại cho step

### 6. Error Recovery (Tự phục hồi lỗi)
- Substep fail không dừng workflow ngay
- Tiếp tục sinh substep mới để thử cách khác
- LLM học từ lỗi trước (error learnings) để tránh lặp lại
- Intermediate progress detection (nhận biến tiến trình trung gian)

### 7. Data Persistence (Lưu trữ dữ liệu)
- Lưu đầy đủ từng substep vào DB
- Upload screenshot lên MinIO
- Ghi log chi tiết từng bước
- Dễ dàng trace lại toàn bộ quá trình thực thi

---

## Ví dụ thực tế

### Ví dụ 1: Login với Multi-Step Flow

**Testcase:** Đăng nhập vào Microsoft Azure Portal

**Flow thực tế:**
1. Navigate đến login page
2. Trang hiện email input → LLM sinh action "fill email"
3. Click "Next" button → Trang chuyển sang password input
4. LLM sinh action "fill password"
5. Click "Sign in" → Trang chuyển sang dashboard
6. LLM validation xác nhận login thành công

**Kết quả:** 
- 6 substeps được sinh động
- Mỗi substep thích ứng với UI hiện tại
- Không cần hard-code flow

---

### Ví dụ 2: Create Bucket với Error Recovery

**Testcase:** Tạo bucket mới tên 'test1' trong MinIO

**Flow thực tế:**
1. Click "Create Bucket" button → Modal mở ra nhưng chưa điền tên
2. **Substep 1 fail:** Click submit trước khi điền tên → Error "Invalid bucket name"
3. **LLM nhận biết lỗi**, sinh substep mới: "Fill bucket name input with 'test1'"
4. **Substep 2 success:** Điền tên thành công
5. **Substep 3:** Click "Create Bucket" button → Bucket được tạo
6. **LLM validation:** Xác nhận bucket 'test1' xuất hiện trong danh sách

**Kết quả:**
- Step pass dù có 1 substep fail
- Workflow tự phục hồi và hoàn thành mục tiêu
- 2/3 substeps pass, 1 fail → Overall: passed

---

### Ví dụ 3: Stuck Detection

**Testcase:** Click "Refresh" button

**Flow thực tế:**
1. Click "Refresh" button → Page refresh nhưng nội dung không đổi
2. LLM validation: "Page state unchanged"
3. Thử lại lần 2 → Vẫn không đổi
4. Thử lại lần 3 → Vẫn không đổi
5. **Stuck detected** → Force complete step với confidence=0.5

**Kết quả:**
- Tránh vòng lặp vô hạn
- Step được đánh dấu completed dù không có thay đổi rõ ràng
- Workflow tiếp tục step tiếp theo

---

## Giới hạn và Cải tiến

### Giới hạn hiện tại
1. **Phụ thuộc LLM:** Chất lượng phụ thuộc vào khả năng hiểu context của LLM
2. **Testcase phức tạp:** Các flow có nhiều logic rẽ nhánh, điều kiện phức tạp có thể cần prompt chuyên biệt
3. **Popup/iframe:** Cần đảm bảo Playwright code nhận diện đúng context (hiện tại đã hỗ trợ cơ bản)
4. **Performance:** Mỗi LLM call mất 1-3s, workflow phức tạp có thể mất vài phút

### Cải tiến đề xuất
1. **Fine-tune LLM:** Train model riêng cho domain cụ thể
2. **Caching:** Cache các substep pattern phổ biến
3. **Parallel execution:** Chạy nhiều testcase song song
4. **Visual validation:** Sử dụng GPT-4 Vision để validate bằng screenshot
5. **Smart retry:** Học từ lịch sử để quyết định retry strategy tốt hơn

---

## Sơ đồ tổng quát (Tóm tắt)

```
INITIALIZE → AUTO_LOGIN → [GET_CONTEXT → GENERATE_SUBSTEP → EXECUTE_SUBSTEP → VALIDATE_STEP → DECISION_NEXT] (lặp lại cho từng step) → CLEANUP
```

- Trong mỗi step, vòng lặp substep sẽ tiếp tục cho đến khi đạt điều kiện hoàn thành hoặc vượt quá giới hạn lặp/fail/stuck.
- Sau mỗi step, workflow tự động chuyển sang step tiếp theo cho đến khi hoàn thành toàn bộ testcase.
- Toàn bộ quá trình được ghi log chi tiết, lưu screenshot, và có thể trace lại từng bước.
