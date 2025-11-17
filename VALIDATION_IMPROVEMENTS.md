# Validation Improvements - LLM-Based Step Validation

## Vấn đề trước đây

**Step 4/5 trong log** cho thấy vấn đề với verification cứng nhắc:
- Browser đã click thành công vào folder `uploads` (URL thay đổi từ `/testcase-bucket` → `/testcase-bucket/uploads%2F`)
- Nhưng verification vẫn báo **FAILED** vì:
  - Verification kiểm tra `element_visible` cho folder "uploads"
  - Sau khi đã vào trong folder, element đó không còn hiển thị theo cách cũ
  - Hệ thống retry nhiều lần mặc dù đã thành công

**Root Cause:**
- Verification logic cứng nhắc chỉ dựa vào selector matching
- Không hiểu context của page state
- Không nhận biết được URL change = success

## Giải pháp mới: LLM Validation Node

### 1. Architecture Changes

**Thêm node `validate_step` vào workflow:**
```
execute_substep → validate_step → decision → next_step/continue_substeps
```

**Flow mới:**
1. Execute substep (như cũ)
2. **NEW: Validate với LLM** - Phân tích HTML/DOM + expected result
3. Decision dựa trên LLM validation thay vì verification cứng nhắc

### 2. Implementation Details

#### 2.1 LLMGenerator.validate_step_completion()

**Input:**
- `step_action`: Hành động cần thực hiện (e.g., "Click folder 'uploads'")
- `expected_result`: Kết quả mong đợi
- `page_html`: HTML của page hiện tại (đã cleaned)
- `current_url`: URL hiện tại
- `substep_description`: Mô tả substep vừa thực hiện

**Output:**
```python
{
    "is_completed": bool,      # Step đã hoàn thành chưa?
    "confidence": float,        # Độ tin cậy (0-1)
    "reason": str,             # Giải thích
    "evidence": str            # Bằng chứng từ HTML/URL
}
```

**LLM Prompt Strategy:**
- Phân tích URL change (strong indicator)
- Kiểm tra HTML content
- Focus on INTENT, không phải exact matching
- Lenient: Nếu có evidence rõ ràng → mark completed

**Example:**
```
Goal: "Click folder 'uploads'"
URL changed to: /testcase-bucket/uploads%2F
→ is_completed: true (URL contains expected path)
```

#### 2.2 AutoTestNodes.validate_step()

**Nhiệm vụ:**
1. Extract page HTML và clean (remove script/style tags)
2. Call LLM validation
3. **Override execution result** nếu:
   - LLM confidence >= 0.7
   - LLM says completed nhưng execution failed
4. Update `last_validation` trong state

**HTML Cleaning:**
```python
from bs4 import BeautifulSoup
soup = BeautifulSoup(page_html, 'html.parser')
# Remove scripts, styles
for tag in soup(['script', 'style', 'noscript']):
    tag.decompose()
cleaned_html = soup.get_text(separator='\n', strip=True)
```

#### 2.3 Workflow Decision Logic Update

**Priority:**
1. **LLM Validation** (if confidence >= 0.7)
   - If completed → `next_step`
   - If not completed → `continue_substeps` (unless too many failures)

2. **Fallback to Execution Result** (if LLM not available)
   - Same logic as before

**Code:**
```python
validation_result = state.get('last_validation')
if validation_result and validation_result.get('confidence', 0) >= 0.7:
    if validation_result.get('is_completed'):
        return "next_step"  # LLM confirmed completion
    else:
        return "continue_substeps"  # LLM says not done yet
```

### 3. State Changes

**New fields in AutoTestState:**
```python
last_validation: Optional[ValidationResult]  # Last LLM validation result
```

**New fields in ExecutionResult:**
```python
llm_validated: Optional[bool]        # Override by LLM
validation_reason: Optional[str]      # LLM explanation
```

### 4. Dependencies

**Added to requirements.txt:**
```
beautifulsoup4  # For HTML cleaning
```

## Lợi ích

### ✅ Intelligent Validation
- LLM hiểu context, không chỉ match selector
- Nhận biết URL change = successful navigation
- Giảm false failures

### ✅ Adaptive
- LLM có thể xử lý unexpected page states
- Không cần update code cho mỗi edge case

### ✅ Explainable
- LLM trả về `reason` và `evidence`
- Dễ debug khi có vấn đề

### ✅ Confidence-based
- Chỉ override khi confidence >= 0.7
- Fallback to execution result nếu không chắc chắn

## Example Scenario

**Step 4: "Click folder 'uploads'"**

**Before (Cứng nhắc):**
```
Substep 1: Click uploads → URL changes ✓
Verification: Check element_visible("uploads") → FAIL ✗
→ Retry 5 times → All fail
```

**After (LLM Validation):**
```
Substep 1: Click uploads → URL changes ✓
LLM Validation:
  - URL: /testcase-bucket/uploads%2F ✓
  - Evidence: "URL contains 'uploads', navigation successful"
  - is_completed: true
  - confidence: 0.95
→ Move to next step ✓
```

## Testing

**Install new dependency:**
```bash
pip install beautifulsoup4
```

**Run test:**
- Same test case (ID: 7)
- Should see new logs: `[VALIDATE]`
- Step 4 should pass on first try (URL change detected)

## Future Enhancements

1. **Visual Validation**: Add screenshot to LLM prompt (GPT-4 Vision)
2. **Learning**: Store validation patterns to improve future tests
3. **Confidence Tuning**: Adjust threshold based on test criticality
4. **Multi-step Validation**: Validate multiple steps together for complex flows

## Files Modified

1. `src/services/autotest/llm_generator.py`
   - Added `validate_step_completion()` method

2. `src/services/autotest/nodes.py`
   - Added `validate_step()` node

3. `src/services/autotest/workflow.py`
   - Added `validate_step` node to graph
   - Updated edges: `execute_substep → validate_step → decision`
   - Updated `_decide_next_action()` to prioritize LLM validation

4. `src/services/autotest/state.py`
   - Added `ValidationResult` TypedDict
   - Added `last_validation` to AutoTestState
   - Added `llm_validated`, `validation_reason` to ExecutionResult

5. `requirements.txt`
   - Added `beautifulsoup4`
