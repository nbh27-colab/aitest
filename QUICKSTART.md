# 🚀 Quick Start Guide

## Bước 1: Cài đặt dependencies

```bash
# Cài đặt Python packages
pip install -r requirements.txt

# Cài đặt Playwright browsers
playwright install chromium
```

## Bước 2: Setup môi trường

```bash
# Tạo file .env hoặc export
export OPENAI_API_KEY="sk-your-api-key-here"
```

## Bước 3: Chuẩn bị dữ liệu test

### 3.1. Tạo Login Info

```sql
INSERT INTO public.login_info (email, password, web_url, name)
VALUES (
    'test@example.com',
    'password123',
    'https://demo.playwright.dev/todomvc',  -- Example demo site
    'Demo Site Login'
);
-- Lấy login_info_id vừa tạo
```

### 3.2. Tạo Test Case

```sql
INSERT INTO public.test_cases (case_sheet_id, title)
VALUES (1, 'Test todo application');
-- Lấy test_case_id
```

### 3.3. Tạo Steps

```sql
-- Step 1: Thêm todo
INSERT INTO qa_test.step (test_case_id, step_order, action, expected_result, project_id)
VALUES (
    1,  -- test_case_id
    1,  -- step_order
    'Thêm một todo mới với text "Buy groceries"',
    'Todo được thêm vào danh sách',
    NULL
);

-- Step 2: Đánh dấu hoàn thành
INSERT INTO qa_test.step (test_case_id, step_order, action, expected_result, project_id)
VALUES (
    1,
    2,
    'Đánh dấu todo "Buy groceries" là hoàn thành',
    'Todo có dấu tick và bị gạch ngang',
    NULL
);

-- Step 3: Xóa todo
INSERT INTO qa_test.step (test_case_id, step_order, action, expected_result, project_id)
VALUES (
    1,
    3,
    'Xóa todo "Buy groceries"',
    'Todo không còn trong danh sách',
    NULL
);
```

## Bước 4: Chạy AutoTest

### Option 1: Via API

```bash
# Start server
uvicorn src.api.main:app --reload

# Call API
curl -X POST "http://localhost:8000/api/autotest/run" \
  -H "Content-Type: application/json" \
  -d '{
    "test_case_id": 1,
    "login_info_id": 1
  }'
```

### Option 2: Via Python Script

```bash
python src/services/autotest/example.py
```

Sửa file `example.py` trước:
```python
test_case_id = 1  # Your test case ID
login_info_id = 1  # Your login info ID
```

## Bước 5: Xem kết quả

### 5.1. Check console output

Workflow sẽ in ra:
```
[INITIALIZE] Starting autotest for test_case_id=1
[INITIALIZE] Loaded 3 steps
[AUTO_LOGIN] Starting auto login
[AUTO_LOGIN] Navigating to https://...
[GET_CONTEXT] Extracting page context
[GET_CONTEXT] Found 15 interactive elements
[GENERATE_SUBSTEP] Generating substep 1 for step 1
[GENERATE_SUBSTEP] Plan: Click vào input field để nhập todo
[EXECUTE] Executing substep 1
[EXECUTE] Result: True - Click completed
...
```

### 5.2. Check database

```sql
-- Xem các substeps được generate
SELECT 
    ss.sub_step_id,
    ss.sub_step_order,
    ss.sub_step_content,
    ss.expected_result
FROM qa_test.sub_step ss
JOIN qa_test.step s ON ss.step_id = s.step_id
WHERE s.test_case_id = 1
ORDER BY s.step_order, ss.sub_step_order;

-- Xem generated scripts
SELECT 
    gs.generated_script_id,
    ss.sub_step_content,
    substring(gs.script_content, 1, 200) as script_preview
FROM qa_test.generated_script gs
JOIN qa_test.sub_step ss ON gs.sub_step_id = ss.sub_step_id
ORDER BY gs.created_at DESC;

-- Xem test results
SELECT 
    tr.result_id,
    tr.object_id,
    tr.object_type,
    tr.result,
    tr.reason,
    tr.created_at
FROM qa_test.test_result tr
ORDER BY tr.created_at DESC
LIMIT 20;
```

### 5.3. Check screenshots

```bash
ls -la *.png
```

Screenshots sẽ có tên dạng:
- `substep_101_success.png`
- `substep_102_success.png`
- `substep_103_error.png` (nếu có lỗi)

## Bước 6: Debug (nếu cần)

### Enable browser visibility

Sửa `src/services/autotest/nodes.py`:

```python
self.browser = await self.playwright_context.chromium.launch(
    headless=False,  # Sẽ thấy browser mở ra
    slow_mo=1000     # Chậm lại để dễ quan sát
)
```

### Check generated script chi tiết

```sql
SELECT script_content 
FROM qa_test.generated_script 
WHERE sub_step_id = 101;
```

### View execution errors

```sql
SELECT * 
FROM qa_test.test_result 
WHERE result = false
ORDER BY created_at DESC;
```

## 📊 Ví dụ kết quả mong đợi

**Input:**
- Test Case: "Test todo application"
- 3 Steps (manual)

**Output (Auto-generated):**
- ~8-12 SubSteps được LLM generate
- 8-12 Playwright scripts
- 8-12 Screenshots
- 8-12 Test Results

**Workflow Flow:**
```
Step 1: "Thêm todo"
  → SubStep 1.1: Tìm input field
  → SubStep 1.2: Click vào input
  → SubStep 1.3: Nhập text "Buy groceries"
  → SubStep 1.4: Press Enter
  → SubStep 1.5: Verify todo xuất hiện

Step 2: "Đánh dấu hoàn thành"
  → SubStep 2.1: Tìm todo "Buy groceries"
  → SubStep 2.2: Click vào checkbox
  → SubStep 2.3: Verify todo có class "completed"

Step 3: "Xóa todo"
  → SubStep 3.1: Hover vào todo
  → SubStep 3.2: Click nút delete
  → SubStep 3.3: Verify todo không còn
```

## 🎯 Tips

1. **Bắt đầu với site đơn giản**: Demo sites như TodoMVC, form examples
2. **Steps rõ ràng**: Viết action càng chi tiết càng tốt
3. **Expected results cụ thể**: Giúp LLM verify chính xác hơn
4. **Check screenshots**: Quan trọng để debug
5. **Monitor token usage**: GPT-4 Vision tốn nhiều tokens

## ❗ Common Issues

### Issue 1: "OpenAI API key not found"
```bash
export OPENAI_API_KEY="sk-..."
```

### Issue 2: "Test case not found"
Check test_case_id có đúng không:
```sql
SELECT * FROM public.test_cases WHERE test_case_id = 1;
```

### Issue 3: "Login failed"
- Check web_url có đúng không
- Check login form selectors
- Xem screenshot `login_success.png`

### Issue 4: Substep generation lỗi
- Check OpenAI API key valid
- Check có đủ credits không
- Xem console logs chi tiết

## 🎓 Next Steps

1. Test với nhiều test cases khác nhau
2. Fine-tune LLM prompts trong `llm_generator.py`
3. Implement MinIO upload cho screenshots
4. Add more sophisticated login logic
5. Support file uploads, drag-drop, etc.

Happy Testing! 🚀
