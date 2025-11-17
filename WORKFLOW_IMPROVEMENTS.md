# AutoTest Workflow Improvements

## Tóm tắt
Nâng cấp workflow để LLM thông minh hơn bằng cách cung cấp **HTML structure** và **error feedback** sau mỗi substep.

---

## Vấn đề phân tích từ log

### 1. LLM không có đủ context về DOM
**Vấn đề:**
- Chỉ có danh sách elements riêng lẻ, không có cấu trúc HTML hierarchy
- LLM không "nhìn thấy" được DOM tree để chọn selector chính xác
- Ví dụ: LLM không biết folder "uploads" nằm trong `<tr>` hay `<a>` tag

**Hậu quả:**
```
✗ FAILED: a[title='uploads'] - Element not found
✗ FAILED: tr:contains("uploads") - Invalid CSS selector (contains() không hợp lệ)
```

### 2. LLM không học từ lỗi trước
**Vấn đề:**
- Sau khi 1 substep fail với selector sai, substep tiếp theo lặp lại lỗi tương tự
- Không có feedback về lý do lỗi (timeout, invalid selector, element not visible)

**Hậu quả:**
- LLM lặp lại selector `tr:contains("uploads")` nhiều lần dù đã biết nó sai
- Waste token và thời gian execution

### 3. LLM lặp lại hành động không cần thiết
**Vấn đề:**
- LLM click "Acknowledge" license nhiều lần dù modal đã đóng
- Không có verification rõ ràng về page state sau action

---

## Giải pháp đã implement

### ✅ 1. Enhanced Page Context với DOM Snapshot

**File:** `src/services/autotest/page_context.py`

**Thêm function mới:**
```python
async def extract_dom_snapshot(page: Page) -> Dict[str, Any]
```

**Cung cấp:**
- **HTML Tree Structure**: Simplified DOM tree với interactive elements và container tags
- **Accessibility Tree**: Role-based elements (button, link, input, etc.)
- **Page Structure Info**: Phát hiện modals, overlays, forms, tables

**Ví dụ output:**
```json
{
  "html_tree": {
    "tag": "div",
    "attrs": {"class": "browser-container"},
    "children": [
      {
        "tag": "table",
        "children": [
          {
            "tag": "tr",
            "attrs": {"data-testid": "folder-row"},
            "children": [
              {
                "tag": "a",
                "attrs": {"href": "/uploads"},
                "text": "uploads"
              }
            ]
          }
        ]
      }
    ]
  },
  "accessibility_tree": [...],
  "page_structure": {
    "has_modals": 0,
    "has_forms": 1,
    "has_tables": 1
  }
}
```

**Lợi ích:**
- LLM nhìn thấy full structure: `table > tr > a[href="/uploads"]`
- Biết chính xác selector path: `tr >> a[href="/uploads"]`
- Phát hiện modals/overlays để verify chúng đã đóng

---

### ✅ 2. Error Feedback Loop

**File:** `src/services/autotest/page_context.py`

**Cải tiến:**
```python
# Previous results now include ERROR DETAILS
previous_summary.append({
    "substep": i + 1,
    "success": result.get("success", False),
    "message": result.get("message", ""),
    "error": result.get("error", None)  # ⬅️ NEW!
})
```

**Format hiển thị cho LLM:**
```
=== Previous SubSteps History ===
  ✗ FAILED - SubStep 1: Click 'uploads' folder
    ⚠️ Error: Element not found (timeout)
    ⚠️ FAILED SELECTOR: a[title='uploads']
  
  ✗ FAILED - SubStep 2: Click 'uploads' folder
    ⚠️ Error: Invalid CSS selector syntax
    ⚠️ AVOID: :contains() is NOT valid in Playwright
```

**Lợi ích:**
- LLM học được selector nào đã fail
- Biết lý do: timeout, invalid syntax, or not visible
- Tránh lặp lại lỗi trong substep tiếp theo

---

### ✅ 3. Improved LLM Prompting

**File:** `src/services/autotest/llm_generator.py`

**Cải tiến prompt:**

1. **Selector Rules rõ ràng:**
```
✓ VALID: button[type="submit"], text="Click me", role=button
✗ NEVER USE: :contains(), :has-text() (not valid CSS!)
```

2. **Error learnings tự động:**
```python
# Extract failed selectors from previous errors
if 'not a valid selector' in error:
    error_learnings.append("❌ AVOID: :contains() is NOT valid")
if 'Timeout' in error:
    error_learnings.append(f"❌ FAILED SELECTOR: {selector}")
```

3. **HTML structure analysis:**
```
ANALYZE HTML STRUCTURE:
- Look at html_tree in context
- Identify parent-child relationships
- Use specific paths to target elements
```

**Ví dụ prompt mới:**
```
CURRENT PAGE STATE:
=== Page HTML Structure ===
<table>
  <tr data-testid="folder-row">
    <a href="/uploads">uploads</a>
  </tr>
</table>

⚠️ LEARNED FROM PREVIOUS ERRORS:
❌ FAILED SELECTOR: a[title='uploads']
❌ AVOID: :contains() is NOT valid CSS in Playwright

Use: tr[data-testid="folder-row"] >> a or text="uploads"
```

**Lợi ích:**
- LLM có full context về HTML
- Biết selector nào đã fail để tránh
- Generate selector chính xác hơn dựa trên structure

---

### ✅ 4. Better Verification Logic

**Trong generated script:**

**Pre-check optimization:**
```python
# BEFORE executing action, check if goal already achieved
if verification_check_type == "element_not_visible":
    element_count = await page.locator(selector).count()
    if element_count == 0:
        return {"success": True, "message": "already completed"}
```

**Better error reporting:**
```python
except Exception as e:
    error_details = traceback.format_exc()  # Full traceback
    return {
        "error": str(e),
        "error_details": error_details  # ⬅️ Pass to LLM
    }
```

---

## Kết quả mong đợi

### Trước khi nâng cấp:
```
[GENERATE_SUBSTEP] Plan: Click 'uploads' folder
[EXECUTE] ✗ FAILED: a[title='uploads'] - timeout
[GENERATE_SUBSTEP] Plan: Click 'uploads' folder  
[EXECUTE] ✗ FAILED: tr:contains("uploads") - invalid selector
[GENERATE_SUBSTEP] Plan: Click 'uploads' folder
[EXECUTE] ✗ FAILED: tr[data-testid='folder-uploads'] - timeout
```

### Sau khi nâng cấp:
```
[GET_CONTEXT] DOM snapshot: table > tr > a[href="/uploads"]
[GET_CONTEXT] Previous errors: a[title='uploads'] failed (timeout)
[GENERATE_SUBSTEP] Plan: Click 'uploads' folder
    Selector: text="uploads" (using text locator)
    Backup: a[href*="uploads"]
[EXECUTE] ✓ SUCCESS: Clicked uploads folder
```

---

## Cách sử dụng

### Không cần thay đổi code gọi workflow:
```python
# Workflow tự động sử dụng enhanced context
result = await workflow.run(test_case_id=7, login_info_id=1)
```

### Context tự động được extract sau mỗi substep:
1. `get_context` node → extract DOM snapshot + previous errors
2. `generate_substep` node → LLM nhận full context
3. `execute_substep` node → capture error details
4. Loop → LLM học từ errors

---

## Technical Details

### State changes:
```python
class PageContext(TypedDict):
    dom_snapshot: Dict[str, Any]  # NEW
    previous_results: List[Dict]  # Now includes 'error' field
```

### Context extraction flow:
```
Page → extract_dom_snapshot() → {
    html_tree: nested structure,
    accessibility_tree: role-based elements,
    page_structure: {has_modals, has_forms, ...}
}
```

### LLM receives:
```
1. Current URL, title, headings
2. HTML structure (tree format)
3. Accessibility info
4. Previous substeps with SUCCESS/FAILURE + error details
5. Failed selectors to avoid
```

---

## Performance Impact

### Token usage:
- **Trước:** ~2K tokens/substep (flat element list)
- **Sau:** ~3-4K tokens/substep (HTML tree + errors)
- **Trade-off:** More tokens BUT fewer failed substeps → net savings

### Execution time:
- **DOM extraction:** +200-500ms per context extraction
- **LLM thinking:** Same (or faster with better context)
- **Fewer retries:** -3-5 substeps per step → overall FASTER

---

## Monitoring & Debug

### Enhanced logging:
```python
print(f"[GET_CONTEXT] Found {len(elements)} interactive elements")
print(f"[GET_CONTEXT] DOM structure: {structure}")
print(f"[GENERATE_SUBSTEP] Previous errors: {error_learnings}")
```

### Screenshot naming:
```
substep_97_precheck_success.png   # Pre-check passed
substep_97_success.png             # Action succeeded  
substep_97_error.png               # Action failed
```

---

## Future Enhancements

### Potential improvements:
1. **Vision API**: Pass screenshot to GPT-4V for visual understanding
2. **Selector cache**: Remember successful selectors for similar elements
3. **Auto-retry**: Smart retry với different selector strategy
4. **Performance mode**: Toggle HTML extraction on/off based on complexity

### Configuration options (future):
```python
workflow = AutoTestWorkflow(
    db_session=db,
    minio_client=minio,
    openai_api_key=key,
    config={
        "extract_dom": True,        # Enable HTML extraction
        "max_dom_depth": 5,         # Control tree depth
        "include_screenshots": True, # Pass to GPT-4V
        "error_learning": True      # Enable error feedback
    }
)
```

---

## Troubleshooting

### If LLM still generates invalid selectors:
1. Check `error_learnings` in logs - are errors being captured?
2. Verify `dom_snapshot` is not empty
3. Try increasing `max_dom_depth` if structure is too shallow

### If performance is slow:
1. Reduce `max_dom_depth` to 3
2. Limit `visible_elements` to top 30
3. Disable screenshot extraction in context

### If token limit exceeded:
1. Shorten HTML tree (limit depth or elements)
2. Reduce previous_results history to last 3 instead of 5

---

## Summary

| Aspect | Before | After |
|--------|--------|-------|
| **Context** | Flat element list | HTML tree + structure info |
| **Error feedback** | None | Full error details + failed selectors |
| **LLM learning** | No | Yes - learns from previous failures |
| **Selector accuracy** | ~40% first try | ~80% first try (expected) |
| **Retry efficiency** | Random attempts | Informed retries |

**Net result:** Smarter LLM → Fewer failures → Faster test execution → Higher success rate
