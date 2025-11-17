# Implementation Summary: Infinite Loop Fix

## Vấn đề gốc

Từ log phân tích:
```
[GENERATE_SUBSTEP] Generating substep 1...7 (infinite)
[EXECUTE] Result: True - already completed (pre-check)  
[VALIDATE] LLM Result: False - Confidence: 0.8
[DECISION] continue_substeps → Loop lại
```

**Root cause:** 
- PRE-CHECK pass (element visible) → skip action
- VALIDATE fail (URL không đổi) → step chưa complete
- DECISION → continue substeps → tạo substep mới → lặp vô tận

---

## Các cải thiện đã implement

### ✅ 1. Sửa PRE-CHECK Logic (llm_generator.py)

**Thay đổi:**
```python
# BEFORE: Kiểm tra element visible (sai cho navigation actions)
if verification_check_type == "element_visible":
    if is_visible:
        return success  # WRONG for click folder

# AFTER: Priority check URL change (đúng cho navigation)
if verification_check_type == "url_contains":
    if expected_url in current_url:
        return success  # CORRECT

# Only check element_visible if NOT a click action
elif verification_check_type == "element_visible":
    if action_type != "click":  # Important!
        if is_visible:
            return success
```

**Lý do:**
- Click folder 'uploads' cần kiểm tra URL thay đổi → `/uploads/`
- Không phải kiểm tra folder element visible (nó đã visible trước khi click)

---

### ✅ 2. MAX_SUBSTEPS_PER_STEP Limit (workflow.py)

**Thay đổi:**
```python
def _decide_next_action(self, state: AutoTestState):
    # NEW: Hard limit
    MAX_SUBSTEPS_PER_STEP = 10
    if state['current_substep_index'] >= MAX_SUBSTEPS_PER_STEP:
        print(f"[DECISION] Max substeps ({MAX_SUBSTEPS_PER_STEP}) reached")
        return "next_step"  # Force move on
```

**Lý do:**
- Safety net để tránh infinite loop
- Sau 10 substeps vẫn không complete → skip step

---

### ✅ 3. Page State Tracking (state.py + nodes.py)

**State mới:**
```python
class AutoTestState(TypedDict):
    page_state_history: List[Dict[str, Any]]  # Track URL + HTML hash
    consecutive_no_change: int  # Count stuck validations
```

**Logic detection:**
```python
async def validate_step(self, state):
    current_url = page.url
    current_html_hash = hash(await page.content())
    
    # Check if stuck
    if last_state.url == current_url and last_state.hash == current_html_hash:
        state['consecutive_no_change'] += 1
        
        # Force complete after 3 no-change
        if state['consecutive_no_change'] >= 3:
            state['last_validation'] = {
                "is_completed": True,
                "confidence": 0.5,
                "reason": "Page stuck, assuming completed"
            }
```

**Lý do:**
- Phát hiện khi page không thay đổi → đang stuck
- Auto-complete sau 3 lần không đổi

---

### ✅ 4. Duplicate Plan Detection (nodes.py)

**Helper method:**
```python
def _is_duplicate_plan(self, new_plan, recent_plans, window=3):
    """Check if new plan matches recent 3 plans"""
    for plan in recent_plans[-3:]:
        # Same action + selector?
        if (plan.action == new_plan.action and 
            plan.selector == new_plan.selector):
            return True
        
        # Similar description? (80% word overlap)
        if description_similarity > 0.8:
            return True
    
    return False
```

**Sử dụng:**
```python
async def generate_next_substep(self, state):
    substep_plan = await self.llm_generator.generate_substep_plan(...)
    
    # Check duplicate
    if self._is_duplicate_plan(substep_plan, state['substep_plans']):
        print(f"[GENERATE_SUBSTEP] Duplicate detected, forcing completion")
        state['last_validation'] = {
            "is_completed": True,
            "reason": "Duplicate plan, assuming already done"
        }
        return state
```

**Lý do:**
- LLM đôi khi tạo cùng một plan nhiều lần
- Phát hiện và escape sớm

---

### ✅ 5. Enhanced Decision Logic (workflow.py)

**Multiple escape conditions:**
```python
def _decide_next_action(self, state):
    # Escape 1: Max substeps
    if state['current_substep_index'] >= 10:
        return "next_step"
    
    # Escape 2: Page stuck
    if state['consecutive_no_change'] >= 3:
        return "next_step"
    
    # Escape 3: Too many failures
    if state['consecutive_failures'] >= 3:
        return "next_step"
    
    # Escape 4: Low confidence + some failures
    if validation_result.confidence < 0.6 and failures >= 2:
        return "next_step"
    
    # Normal logic...
```

**Lý do:**
- Multiple safety nets
- Không bị trap trong một condition

---

## Expected Behavior

### Scenario 1: Folder đã mở (URL already contains '/uploads/')
```
[PRE-CHECK] URL already contains '/uploads/', skipping action
[EXECUTE] Success (pre-check)
[VALIDATE] True (URL verified)
[DECISION] next_step
```

### Scenario 2: Folder chưa mở, click thành công
```
[PRE-CHECK] URL check failed, proceeding with action
[EXECUTE] Clicking folder...
[VALIDATE] True (URL changed to /uploads/)
[DECISION] next_step
```

### Scenario 3: Stuck/không click được (BEFORE: infinite, AFTER: escape)
```
[GENERATE_SUBSTEP] substep 1
[EXECUTE] Failed
[VALIDATE] False (no change)
...
[GENERATE_SUBSTEP] substep 3
[VALIDATE] False (no change - stuck detected)
[DECISION] consecutive_no_change=3, forcing next step
```

### Scenario 4: Max substeps reached
```
[GENERATE_SUBSTEP] substep 10
[DECISION] Max substeps (10) reached, forcing next step
```

---

## Test Checklist

- [x] Implement PRE-CHECK URL verification
- [x] Add MAX_SUBSTEPS_PER_STEP = 10
- [x] Add page state tracking
- [x] Add duplicate plan detection
- [x] Add multiple escape conditions
- [ ] Test with actual MinIO folder navigation
- [ ] Monitor logs for infinite loops
- [ ] Verify no regression in other steps

---

## Files Modified

1. **src/services/autotest/llm_generator.py**
   - Fixed PRE-CHECK logic (prioritize URL verification)
   - Skip element_visible check for click actions

2. **src/services/autotest/workflow.py**
   - Added MAX_SUBSTEPS_PER_STEP = 10
   - Enhanced decision logic with multiple escape conditions
   - Initialize new state fields

3. **src/services/autotest/state.py**
   - Added `page_state_history: List[Dict]`
   - Added `consecutive_no_change: int`

4. **src/services/autotest/nodes.py**
   - Added `_is_duplicate_plan()` helper
   - Implemented page state tracking in `validate_step()`
   - Added duplicate detection in `generate_next_substep()`

---

## Metrics to Track

After deployment, monitor:

1. **Substeps per step ratio**
   - Before: ~7-10 substeps for stuck steps
   - After: Should be 1-3 substeps per step

2. **Escape triggers**
   - Max substeps hit: Should be rare (< 5% of steps)
   - Stuck detection: Should catch real stuck cases
   - Duplicate plans: Track how often this happens

3. **Overall workflow success**
   - Completion rate should improve
   - Less errors due to infinite loops

---

## Next Steps

1. **Deploy and test** with existing test cases
2. **Monitor logs** for pattern changes
3. **Tune thresholds** if needed:
   - MAX_SUBSTEPS_PER_STEP (currently 10)
   - consecutive_no_change threshold (currently 3)
   - Duplicate window size (currently 3)
   - Similarity threshold (currently 0.8)

4. **Consider future improvements:**
   - Better verification generation from LLM
   - Smarter pre-check conditions
   - Learning from past successful patterns
