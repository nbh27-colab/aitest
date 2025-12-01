"""
Visual validation method for LLMGenerator
This file contains the vision-based validation logic
"""

from typing import Dict, Any
import json


async def validate_with_vision(
    llm_client,
    step_action: str,
    expected_result: str,
    before_screenshot_url: str,
    after_screenshot_url: str,
    current_url: str,
    substep_description: str = None
) -> Dict[str, Any]:
    """
    VISUAL validation using GPT-4 Vision to compare before/after screenshots
    
    This is MORE RELIABLE than text-based validation because:
    - It sees actual visual changes (modals closing, content appearing, etc.)
    - Not confused by DOM elements that exist but are hidden
    - Can detect UI state changes that don't reflect in DOM text
    """
    
    prompt = f"""You are an expert QA automation engineer performing VISUAL validation of a test step.

🎯 STEP GOAL: {step_action}
📋 EXPECTED RESULT: {expected_result}
{f'🔧 ACTION PERFORMED: {substep_description}' if substep_description else ''}
🌐 CURRENT URL: {current_url}

📸 VISUAL EVIDENCE:
I will show you TWO screenshots:
1. BEFORE screenshot - The page state BEFORE performing the action
2. AFTER screenshot - The page state AFTER performing the action

Your task is to compare these two screenshots and determine if the step goal was achieved.

🔍 WHAT TO LOOK FOR:

**For DISMISSAL/CLOSE actions (modals, popups, dialogs, banners):**
- ✅ Completed: Modal/popup/dialog is VISIBLE in BEFORE but NOT visible in AFTER
- ✅ Completed: Overlay/backdrop disappeared
- ✅ Completed: Content behind the modal is now fully visible
- ❌ Not completed: Modal still visible in both screenshots

**For OPEN actions (dropdowns, menus, modals):**
- ✅ Completed: Element NOT visible in BEFORE but IS visible in AFTER
- ✅ Completed: Dropdown menu expanded, modal appeared, etc.
- ❌ Not completed: No visible change or element still hidden

**For CLICK/NAVIGATION actions:**
- ✅ Completed: Page content changed significantly
- ✅ Completed: Different page or section loaded
- ✅ Completed: URL changed (if goal was navigation)
- ❌ Not completed: Page looks identical

**For FORM INPUT actions:**
- ✅ Completed: Text appeared in input field
- ✅ Completed: Selection changed in dropdown
- ❌ Not completed: Field still empty

⚠️ CRITICAL RULES:
1. **Trust your eyes, not assumptions**: Visual evidence is the ground truth
2. **Ignore unrelated UI elements**: Focus ONLY on elements related to the step goal
3. **Be practical**: Small visual differences that achieve the goal = completed
4. **Consider context**: A license watermark in footer ≠ license modal in center

🎯 CONFIDENCE LEVELS:
- 1.0 = Very obvious visual change matching the goal perfectly
- 0.8-0.9 = Clear visual change, very likely the goal was achieved
- 0.5-0.7 = Some visual change, possibly related to goal
- 0.3-0.4 = Unclear or minimal change
- 0.0-0.2 = No visible change or opposite of expected

📤 RESPONSE FORMAT (JSON only):
{{
    "is_completed": true/false,
    "confidence": 0.0-1.0,
    "reason": "Clear explanation based on VISUAL differences you observed",
    "evidence": "Specific visual elements that appeared/disappeared/changed"
}}

Example responses:
- "A large modal dialog centered on the page in BEFORE screenshot has completely disappeared in AFTER screenshot, revealing the content underneath" → is_completed: true, confidence: 1.0
- "Both screenshots show identical page content with no visible changes" → is_completed: false, confidence: 0.9
- "A dropdown menu that was closed in BEFORE is now expanded in AFTER" → is_completed: true, confidence: 1.0
"""

    try:
        # Prepare image messages for vision model
        messages = [
            {
                "role": "system",
                "content": "You are an expert QA engineer with excellent visual analysis skills. You compare screenshots to validate test step completion."
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt
                    },
                    {
                        "type": "text",
                        "text": "📸 BEFORE Screenshot (page state BEFORE action):"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": before_screenshot_url,
                            "detail": "high"
                        }
                    },
                    {
                        "type": "text",
                        "text": "📸 AFTER Screenshot (page state AFTER action):"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": after_screenshot_url,
                            "detail": "high"
                        }
                    }
                ]
            }
        ]
        
        response = await llm_client.chat.completions.create(
            model="gpt-4o",  # Vision model
            messages=messages,
            temperature=0.1,  # Low temperature for consistent analysis
            max_tokens=500,
            response_format={"type": "json_object"}
        )
        
        result_text = response.choices[0].message.content
        result = json.loads(result_text)
        
        print(f"[VISUAL_VALIDATE] Result: {result['is_completed']}, Confidence: {result['confidence']}")
        print(f"[VISUAL_VALIDATE] Reason: {result['reason']}")
        
        return result
        
    except Exception as e:
        print(f"[VISUAL_VALIDATE] Error in visual validation: {e}")
        import traceback
        traceback.print_exc()
        
        # Fallback: assume not completed on error
        return {
            "is_completed": False,
            "confidence": 0.0,
            "reason": f"Visual validation error: {str(e)}",
            "evidence": "Vision API failed"
        }
