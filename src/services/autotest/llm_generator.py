import json
from typing import Any, Dict, List, Optional
import base64
from openai import AsyncOpenAI
from config.settings import LLMSettings
from ._vision_validate import validate_with_vision

class LLMGenerator:
    def __init__(self, llm_settings: LLMSettings = None):
        if llm_settings is None:
            llm_settings = LLMSettings()
        
        self.settings = llm_settings
        
        self.client = AsyncOpenAI(
            api_key=llm_settings.OPENAI_API_KEY
        )
        self.model = "gpt-4o"  # Default model, có thể thêm vào settings nếu cần
        self.temperature = llm_settings.TEMPERATURE
        self.max_tokens = llm_settings.MAX_TOKENS


    async def generate_login_action(
        self,
        login_info: Dict[str, Any],
        page_context: Dict[str, Any],
        login_state: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generate next login action based on current page state
        Supports multi-step login flows (Microsoft, Google, AWS, etc.)
        
        Returns:
            {
                "action_type": "click_login_button" | "enter_email" | "enter_password" | "click_next" | "click_submit" | "wait_for_redirect" | "completed" | "error",
                "target": {"primary_selector": "...", "fallback_selectors": [...]},
                "reason": "...",
                "confidence": 0.0-1.0
            }
        """
        
        from .page_context import format_context_for_llm
        context_text = format_context_for_llm(page_context)
        
        prompt = f"""You are an intelligent login automation agent. Your goal is to successfully log in to the website by analyzing the current page state and determining the most logical next action.

**Available Credentials:**
- Email/Username: {login_info.get('email', 'N/A')}
- Password: [REDACTED]

**What You've Done So Far:**
- Email entered: {login_state.get('email_entered', False)}
- Password entered: {login_state.get('password_entered', False)}
- Current attempt: {login_state.get('attempts', 1)}
- Current URL: {login_state.get('current_url', 'unknown')}

**Current Page State:**
{context_text}

**Your Task:**
Analyze the page context above and determine what action to take next to progress toward successful login. Think like a human user would.

**Available Actions:**
- **click_login_button** - Click any button/link that initiates login or reveals login fields (e.g., "Sign in", "Login", "Staff Login", "Sign in with Microsoft", etc.)
- **enter_email** - Fill in the email/username field
- **enter_password** - Fill in the password field  
- **click_next** - Click a "Next" or "Continue" button (common in multi-step flows)
- **click_submit** - Click the final submit/login/sign-in button
- **wait_for_redirect** - Wait for an OAuth/SSO redirect to complete
- **completed** - Login appears successful (logged in to the application, not just past the login form)
- **error** - Something is wrong (error message visible, stuck, etc.)

**How to Decide:**
1. Look at the visible_elements array - what interactive elements are actually on the page?
2. Consider the page_title and main_heading - what does the page appear to be?
3. Think about what a human would do next in this situation
4. **IMPORTANT**: After clicking submit, there may be additional steps:
   - "Stay signed in?" dialog → click_next (choose Yes/No to proceed)
   - Two-factor authentication → enter additional code
   - Accept terms/permissions → click_next to accept
   - Don't declare "completed" until you're actually IN the application (not still on auth pages)
5. If you're not sure what fields exist, look carefully at the visible_elements details (type, name, id, placeholder, text)
6. Choose the action that makes the most sense given what you see

**Important Notes:**
- Some login pages require clicking a login method button BEFORE email/password fields appear
- Some have all fields visible at once
- Some split email and password into separate steps
- Trust the visible_elements data - if you don't see an email field there, it probably doesn't exist yet
- **CRITICAL - CSS Selectors**: Use ONLY valid CSS selectors:
  * Valid: #id, .class, [name="value"], button[type="submit"], div[role="button"]
  * Valid: tag.class, tag#id, parent > child, element:nth-child(n)
  * **INVALID**: :contains(), :has-text(), :visible - these are NOT standard CSS
  * For text matching, use attributes: [aria-label="text"], [title="text"], or just tag/class/id
  * For buttons with specific text, use the visible_elements data to find unique id/class/role/aria-label
- Include fallback selectors in case the primary one doesn't work (prioritize: id > name > class > role > tag)
- **IGNORE JAVASCRIPT WARNINGS**: If you see text like "JavaScript needs to be enabled" or "You need to enable JavaScript", IGNORE IT if there are other interactive elements (inputs, buttons) visible. This is often just a <noscript> tag that is visible in the text content but hidden in the browser. If you see input fields or buttons, assume the page is working and proceed with the login flow.

**Response Format:**
Return a JSON object with your decision:
{{
    "action_type": "click_login_button|enter_email|enter_password|click_next|click_submit|wait_for_redirect|completed|error",
    "target": {{
        "primary_selector": "most reliable CSS selector for the target element",
        "fallback_selectors": ["alternative selector 1", "alternative selector 2"]
    }},
    "reason": "clear explanation of why you chose this action based on what you observe",
    "confidence": 0.0-1.0
}}

If action_type is wait_for_redirect, completed, or error, target can be null.

**Example Scenarios:**
- Page shows "Stay signed in?" with Yes/No buttons → action: click_next, target: button with "Yes" or "No"
- Page shows "Accept permissions?" → action: click_next, target: Accept button
- Page shows dashboard/app content with user menu → action: completed
- Page still shows login form after submit → action: error (login failed)
"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert at web automation and login flows. You understand various login patterns and can adapt to any login page design. Analyze the page context carefully and make intelligent decisions based on what you observe, just like a human would."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            
            result_text = response.choices[0].message.content
            result = json.loads(result_text)
            
            return result
            
        except Exception as e:
            print(f"Error in login action generation: {e}")
            import traceback
            traceback.print_exc()
            
            return {
                "action_type": "error",
                "target": None,
                "reason": f"LLM error: {str(e)}",
                "confidence": 0.0
            }
    
    async def validate_login_success(
        self,
        page_context: Dict[str, Any],
        initial_url: str,
        current_url: str
    ) -> Dict[str, Any]:
        """
        Validate if login was successful by analyzing page state
        
        Returns:
            {
                "is_logged_in": bool,
                "confidence": 0.0-1.0,
                "reason": "...",
                "evidence": "..."
            }
        """
        
        from .page_context import format_context_for_llm
        context_text = format_context_for_llm(page_context)
        
        prompt = f"""You are validating if a login attempt was successful.

**Initial URL:** {initial_url}
**Current URL:** {current_url}

**Current Page Context:**
{context_text}

**Task:**
Determine if the user is now logged in successfully.

**Success Indicators:**
- URL changed from login page to dashboard/home/app page
- Presence of user menu, profile icon, logout button
- Absence of login form
- Presence of navigation menus
- Welcome message or user name displayed

**Failure Indicators:**
- Still on login page
- Error messages visible
- Login form still present
- URL unchanged or redirected back to login

Return JSON:
{{
    "is_logged_in": true/false,
    "confidence": 0.0-1.0,
    "reason": "Brief explanation",
    "evidence": "Specific elements or URL patterns that indicate success/failure"
}}
"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert at detecting successful login states in web applications."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.2,
                response_format={"type": "json_object"}
            )
            
            result_text = response.choices[0].message.content
            result = json.loads(result_text)
            
            return result
            
        except Exception as e:
            print(f"Error in login validation: {e}")
            return {
                "is_logged_in": False,
                "confidence": 0.0,
                "reason": f"Validation error: {str(e)}",
                "evidence": "N/A"
            }
        

    async def generate_substep_plan(
        self, 
        step: Dict[str, Any], 
        context: Dict[str, Any],
        substep_index: int,
        page_stuck: bool = False,
        previous_plans: List[Dict[str, Any]] = None,
        last_validation: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Sử dụng LLM để generate kế hoạch cho substep tiếp theo
        dựa trên step action, expected result và page context hiện tại
        
        UPDATED: Now uses enhanced context with HTML structure and previous errors
        """
        if previous_plans is None:
            previous_plans = []
            
        # Format context cho prompt
        from .page_context import format_context_for_llm
        context_text = format_context_for_llm(context)
        
        # Extract error patterns from previous failures to help LLM avoid them
        error_learnings = []
        intermediate_progress = []
        
        # Build history context
        history_context = ""
        if previous_plans:
            history_items = []
            for i, plan in enumerate(previous_plans):
                status = "Unknown"
                error_msg = ""
                # Try to find matching result in context['previous_results'] if available
                if context.get('previous_results') and len(context['previous_results']) > i:
                    res = context['previous_results'][i]
                    status = "Success" if res.get('success') else "Failed"
                    if not res.get('success'):
                        error_msg = f" - Error: {res.get('message', '')} {res.get('error', '')}"
                
                history_items.append(f"   - Attempt {i+1}: {plan.get('substep_description')} (Action: {plan.get('action_type')}) -> {status}{error_msg}")
            
            history_context = f"""
📜 HISTORY OF PREVIOUS ATTEMPTS (Read carefully!):
{chr(10).join(history_items)}

CRITICAL INSTRUCTION:
- If previous attempts FAILED, do NOT repeat them exactly. Change the selector, action type, or strategy.
- If previous attempts SUCCEEDED but goal not reached, move to the next logical action.
- If you see repeated failures on the same element, try a different element or approach.
"""

        # Build validation feedback context
        validation_feedback = ""
        if last_validation and not last_validation.get('is_completed'):
            validation_feedback = f"""
❌ PREVIOUS VALIDATION FAILED:
The previous attempt was executed but the step is NOT considered complete.
Reason: {last_validation.get('reason', 'Unknown')}
Evidence: {last_validation.get('evidence', 'N/A')}

ADJUSTMENT NEEDED:
- The previous action might have succeeded technically (no error), but didn't achieve the functional goal.
- You need to try a different action or a follow-up action to satisfy the goal.
"""

        if context.get('previous_results'):
            for result in context['previous_results']:
                # Track intermediate progress
                if result.get('intermediate_progress'):
                    intermediate_progress.append(result['intermediate_progress'])
                
                # Track errors
                if not result['success'] and result.get('error'):
                    error = result['error']
                    if 'not a valid selector' in error or 'SyntaxError' in error:
                        error_learnings.append("❌ AVOID: CSS pseudo-classes like :contains() are NOT valid in Playwright. Use text= or other locators.")
                    elif 'Timeout' in error:
                        # Extract the selector that timed out
                        if 'locator("' in error:
                            failed_selector = error.split('locator("')[1].split('")')[0]
                            error_learnings.append(f"❌ FAILED SELECTOR: {failed_selector} (element not found or not visible)")
        
        error_context = "\n".join(list(set(error_learnings)))  # Remove duplicates
        
        # Build intermediate progress context
        progress_context = ""
        if intermediate_progress:
            unique_progress = list(set(intermediate_progress))
            progress_context = f"""
✅ INTERMEDIATE PROGRESS DETECTED:
{chr(10).join(f"- {p}" for p in unique_progress)}

This means previous actions ARE working, even if final goal not reached yet.
BUILD ON this progress - don't repeat actions that already succeeded in changing page state.
"""
        
        # Add page stuck warning
        stuck_warning = ""
        if page_stuck:
            stuck_warning = """
⚠️ PAGE STATE NOT CHANGING:
The page has not changed in the last few attempts. This could mean:
1. The goal is already achieved (verify completion)
2. We're stuck in a loop (try a completely different approach)
3. The action is impossible with current page state (mark as final)

CRITICAL: Do NOT repeat previous actions. Either verify completion or try a fundamentally different approach.
"""
        
        prompt = f"""You are an expert test automation engineer generating the NEXT immediate action for a web test.

OVERALL GOAL: {step.get('action', '')}
EXPECTED RESULT: {step.get('expected_result', '')}

CURRENT PAGE STATE:
{context_text}

{history_context}
{validation_feedback}
{progress_context}{stuck_warning}
{f'''
⚠️ LEARNED FROM PREVIOUS ERRORS:
{error_context}
''' if error_context else ''}

TASK: Based on the current page state (including HTML structure) and previous actions, determine the NEXT single, atomic action needed to achieve the goal.

**SPECIAL NOTE ON DROPDOWNS:**
If you see in the HTML tree a select element marked with "⚠️CUSTOM_DROPDOWN", this means:
- It's NOT a native HTML select (even though tag is <select>)
- It's a custom component (PrimeNG, Material, etc.)
- You MUST use 'click' action to open it, NOT 'select' action
- After clicking, options will appear in an overlay panel
- Then you need another substep to click the option

Example HTML you might see:
```
🔹<select class="action"> ⚠️CUSTOM_DROPDOWN [Options: Select, DAE, ...]
```
This means: Use action_type="click" to open the dropdown first!

CRITICAL SELECTOR RULES:
1. USE VALID PLAYWRIGHT SELECTORS ONLY:
   ✓ CSS: button[type="submit"], input[name="email"], .class-name, #id
   ✓ Text: text="Click me", text=/regex/
   ✓ Role: role=button, role=link
   ✓ XPath: //button[@type="submit"]
   ✗ NEVER USE: :contains(), :has-text() - These are NOT valid CSS!

2. PREFER ROBUST SELECTORS:
   - Use data-testid, role, aria-label if available
   - Use unique attributes (name, id) when possible
   - For tables: use CSS like tr >> text="folder-name" or getByRole('row')
   - For links: use href, text content, or role=link

3. ANALYZE HTML STRUCTURE:
   - Look at the html_tree in context
   - Identify parent-child relationships
   - Use specific paths to target elements

4. **CRITICAL - FORM ELEMENT INTERACTION**:
   ⚠️ NEVER click on LABELS - click on the actual INPUT/SELECT/BUTTON elements!
   
   **For DROPDOWNS/SELECT elements:**
   - ✗ WRONG: Click on label "Admission category"
   - ✓ CORRECT: Target the interactive dropdown element below/after the label
   
   **CRITICAL - Dropdown Types:**
   A. **Native <select> dropdowns** (rare in modern apps):
      - HTML: <select><option>...</option></select>
      - Action: Use 'select' action with select_option
      
   B. **Custom dropdowns** (PrimeNG, Material, Ant Design, Bootstrap, **AUI** - MOST COMMON):
      - HTML patterns:
        * <div class="p-dropdown"> or class="action" (PrimeNG)
        * <div role="combobox"> (ARIA)
        * <button aria-haspopup="listbox">
        * <div class="select-wrapper">
        * **<aui-comboboxshell> or elements with data-trigger attribute (AUI framework)**
      - Action: Use 'click' action to OPEN dropdown first
      - After opening, options appear in overlay/panel
      - Then click the specific option text
      - **For AUI components**: Look for [data-trigger="..."] attribute, click the element to open overlay
   
   **How to identify custom dropdowns in HTML:**
   - Look for: class="dropdown", "p-dropdown", "mat-select", "ant-select", "select-wrapper"
   - Look for: role="combobox", aria-haspopup="listbox"
   - Look for: <div> or <button> elements styled as dropdowns
   - **Look for: ⚠️AUI_COMPONENT marker or data-trigger attribute (AUI framework)**
   - **Look for: <aui-comboboxshell> tags**
   - If you see <select class="action">, it's likely a CUSTOM dropdown disguised as select
   
   **Correct approach for custom dropdowns:**
   1. First substep: Click the dropdown trigger to open it
   2. Second substep: Click the option in the opened overlay
   
   **For INPUT fields:**
   - ✗ WRONG: Click on "Email" label  
   - ✓ CORRECT: Target the <input> element
   - Use: input[name="email"], input[type="email"], label + input
   
   **For CHECKBOXES/RADIO:**
   - Target the input element: input[type="checkbox"], input[type="radio"]
   
   **For BUTTONS:**
   - Target the actual button element: button, input[type="submit"], [role="button"]
   - Prefer specific attributes: button[type="submit"], button.primary-button
   
   **EXAMPLE - Filling a dropdown labeled "Admission category":**
   1. First analyze HTML structure to find the actual SELECT element
   2. If HTML shows: <label>Admission category</label><select>...</select>
   3. Use selector: "select" or more specific "label:has-text('Admission category') + select"
   4. DON'T target the label itself!

CRITICAL CONSTRAINTS:
- NEVER log out or sign out unless explicitly required by the goal
- NEVER navigate away unless the goal requires it
- NEVER undo previous successful actions
- The system is ALREADY LOGGED IN
- Focus ONLY on achieving the stated goal
- For modal/popup dismissal: verify element_not_visible, not button existence

Respond ONLY with valid JSON in this exact format:
{{
    "substep_description": "Clear, concise description of this action",
    "action_type": "click|fill|select|verify|wait|navigate|press_key",
    "target_element": {{
        "primary_selector": "VALID Playwright CSS/text/role selector",
        "selector_type": "css|xpath|text|role",
        "backup_selectors": ["alternative valid selector 1", "alternative valid selector 2"],
        "element_description": "What this element is"
    }},
    "action_value": "Value to input (for fill/select actions) or null",
    "verification": {{
        "check_type": "element_visible|element_not_visible|text_contains|url_contains|element_count|attribute_value",
        "expected": "What to expect after this action",
        "selector": "Element to verify (if applicable)",
        "attribute": "Attribute name (for attribute_value check, e.g., 'disabled', 'aria-disabled', 'class')"
    }},
    "is_final_substep": true/false,
    "reasoning": "Brief explanation based on HTML structure and goal"
}}

VERIFICATION EXAMPLES:
- Button enabled after fill: {{"check_type": "attribute_value", "selector": "#submit-btn", "attribute": "disabled", "expected": "false"}}
- Element has class: {{"check_type": "attribute_value", "selector": ".item", "attribute": "class", "expected": "active"}}
- Element visible: {{"check_type": "element_visible", "selector": ".modal"}}
- URL changed: {{"check_type": "url_contains", "expected": "/dashboard"}}

**REAL EXAMPLES FOR COMMON SCENARIOS:**

Example 1A - Native <select> dropdown (RARE):
HTML shows:
  📋<label> Admission category *
  🔹<select name="admission_category">
    <option>Select</option>
    <option>DAE</option>
  </select>

CORRECT response:
{{
  "substep_description": "Select 'DAE' from Admission category dropdown",
  "action_type": "select",
  "target_element": {{
    "primary_selector": "select[name='admission_category']",
    "selector_type": "css",
    "backup_selectors": ["select"],
    "element_description": "Admission category native dropdown"
  }},
  "action_value": "DAE",
  "is_final_substep": false,
  "reasoning": "This is a native HTML select element"
}}

Example 1B - Custom dropdown (COMMON - PrimeNG, Material, AUI, etc.):
HTML shows:
  📋<label> Admission category *
  🔹<select class="action" aria-disabled="false">  <!-- This is a CUSTOM dropdown -->
    <option>Select</option>
  </select>
  OR
  <div class="p-dropdown" role="combobox">
    <span>Select</span>
  </div>
  OR
  ⚠️AUI_COMPONENT [trigger=aui_comboboxshell_0]  <!-- AUI framework -->

CORRECT response (Step 1 - Open dropdown):
{{
  "substep_description": "Click Admission category dropdown to open options",
  "action_type": "click",
  "target_element": {{
    "primary_selector": "select.action",
    "selector_type": "css",
    "backup_selectors": ["div[role='combobox']", "div.p-dropdown", "[data-trigger]", "aui-comboboxshell"],
    "element_description": "Admission category dropdown trigger"
  }},
  "action_value": null,
  "verification": {{
    "check_type": "element_visible",
    "selector": "div[role='listbox'], ul.p-dropdown-items, .aui-comboboxshell-popup",
    "expected": "Dropdown options panel opens"
  }},
  "is_final_substep": false,
  "reasoning": "Custom dropdown needs to be clicked to reveal options overlay"
}}

Then Step 2 - Select option:
{{
  "substep_description": "Click 'DAE' option in opened dropdown",
  "action_type": "click",
  "target_element": {{
    "primary_selector": "text=DAE",
    "selector_type": "text",
    "backup_selectors": ["[role='option']:has-text('DAE')", "li:has-text('DAE')"],
    "element_description": "DAE option in dropdown overlay"
  }},
  "action_value": null,
  "is_final_substep": false,
  "reasoning": "Click the option text in the opened overlay panel"
}}

Example 2 - Clicking a button:
HTML shows:
  🔹<button type="submit" class="btn-primary">Create</button>

CORRECT response:
{{
  "substep_description": "Click Create button",
  "action_type": "click",
  "target_element": {{
    "primary_selector": "button[type='submit']",
    "selector_type": "css",
    "backup_selectors": ["button.btn-primary", "text='Create'"],
    "element_description": "Create button"
  }},
  "action_value": null,
  "verification": {{
    "check_type": "element_visible",
    "selector": ".create-dialog",
    "expected": "Create dialog appears"
  }},
  "is_final_substep": false,
  "reasoning": "Target the button element directly"
}}

IMPORTANT:
1. Use the HTML structure to find correct selectors
2. Learn from previous failed selectors - try different approaches
3. Consider page state changes (modals, overlays, dynamic content)
4. Set is_final_substep=true when goal is achieved
5. Use attribute_value check when verifying enabled/disabled state, classes, or other attributes
"""

        try:
            # Call GPT-4 with vision if screenshot available
            messages = [
                {
                    "role": "system",
                    "content": "You are an expert test automation engineer. You generate precise, executable test steps based on current page state."
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt}
                    ]
                }
            ]
            
            # Add screenshot if available
            if context.get('screenshot_base64'):
                messages[1]["content"].append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{context['screenshot_base64']}"
                    }
                })
            
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            
            plan_text = response.choices[0].message.content
            plan = json.loads(plan_text)
            
            return plan
            
        except Exception as e:
            print(f"Error generating substep plan: {e}")
            # Fallback plan
            return {
                "substep_description": f"Execute action: {step.get('action', '')}",
                "action_type": "verify",
                "target_element": {
                    "primary_selector": "body",
                    "selector_type": "css",
                    "backup_selectors": [],
                    "element_description": "Page body"
                },
                "action_value": None,
                "verification": {
                    "check_type": "element_visible",
                    "expected": "Page is loaded",
                    "selector": "body"
                },
                "is_final_substep": True,
                "reasoning": f"Fallback due to error: {str(e)}"
            }
    
    async def generate_playwright_script(
        self,
        substep_plan: Dict[str, Any],
        substep_id: int
    ) -> str:
        """
        Convert substep plan thành Playwright code
        """
        
        # Helper function to escape quotes in selectors for safe f-string usage
        def escape_selector(selector: str) -> str:
            """Escape double quotes in selector to prevent syntax errors in generated Python code"""
            if selector:
                return selector.replace('"', '\\"')
            return selector
        
        action_type = substep_plan['action_type']
        target = substep_plan['target_element']
        value = substep_plan.get('action_value')
        verification = substep_plan.get('verification', {})
        
        # Template header
        script = f'''"""
{substep_plan['substep_description']}
Expected: {verification.get('expected', 'N/A')}
"""

async def execute_substep_{substep_id}(page):
    """Execute substep {substep_id}"""
    try:
        # Wait for page to be ready
        await page.wait_for_load_state('domcontentloaded')
        await page.wait_for_timeout(500)  # Small delay for dynamic content
        
        # PRE-CHECK: If verification condition is already met, skip action
        # IMPROVED: Check expected RESULT, not intermediate conditions
        verification_check_type = "{verification.get('check_type', '')}"
        
        # Priority 1: Check URL-based verification (most reliable for navigation)
        if verification_check_type == "url_contains":
            expected_url = "{verification.get('expected', '')}"
            current_url = page.url
            if expected_url and expected_url in current_url:
                print(f"[PRE-CHECK] URL already contains '{{expected_url}}', skipping action")
                screenshot_path = f"substep_{substep_id}_precheck_success.png"
                await page.screenshot(path=screenshot_path, full_page=False)
                return {{
                    "success": True,
                    "screenshot_path": screenshot_path,
                    "message": "{substep_plan['substep_description']} - already completed (URL verification)",
                    "error": None
                }}
        
        # Priority 2: Check element visibility ONLY if it's the actual goal
        # (not for click actions where element should already be visible)
        elif verification_check_type == "element_visible":
            verify_selector = "{escape_selector(verification.get('selector', target['primary_selector']))}"
            action_type = "{action_type}"
            
            # CRITICAL: Only pre-check element visibility for VERIFY actions
            # For click/fill/other actions, element visibility is a POST-condition, not PRE-condition
            # Example: "Fill input" → verify "Submit button enabled" (button exists before fill!)
            if action_type == "verify":
                try:
                    await page.wait_for_selector(verify_selector, timeout=2000, state='visible')
                    is_visible = await page.locator(verify_selector).is_visible()
                    if is_visible:
                        print(f"[PRE-CHECK] Element already visible, goal achieved")
                        screenshot_path = f"substep_{substep_id}_precheck_success.png"
                        await page.screenshot(path=screenshot_path, full_page=False)
                        return {{
                            "success": True,
                            "screenshot_path": screenshot_path,
                            "message": "{substep_plan['substep_description']} - already completed (element visible)",
                            "error": None
                        }}
                except:
                    pass  # Verification not met, proceed with action
        
        # Priority 3: Check element not visible
        elif verification_check_type == "element_not_visible":
            verify_selector = "{escape_selector(verification.get('selector', ''))}"
            action_type = "{action_type}"
            
            # CRITICAL: Only pre-check for VERIFY actions
            # For click actions (e.g. "Click Close"), if the selector is wrong (not found),
            # we don't want to skip the click! We want to try clicking and fail if needed.
            if action_type == "verify" and verify_selector:
                try:
                    # Check both: element doesn't exist OR exists but not visible
                    element_count = await page.locator(verify_selector).count()
                    if element_count == 0:
                        # Element not in DOM at all
                        print(f"[PRE-CHECK] Element not in DOM, goal achieved")
                        screenshot_path = f"substep_{substep_id}_precheck_success.png"
                        await page.screenshot(path=screenshot_path, full_page=False)
                        return {{
                            "success": True,
                            "screenshot_path": screenshot_path,
                            "message": "{substep_plan['substep_description']} - already completed (element not in DOM)",
                            "error": None
                        }}
                    else:
                        # Element exists, check if it's actually visible to user
                        is_visible = await page.locator(verify_selector).is_visible()
                        if not is_visible:
                            print(f"[PRE-CHECK] Element in DOM but not visible, goal achieved")
                            screenshot_path = f"substep_{substep_id}_precheck_success.png"
                            await page.screenshot(path=screenshot_path, full_page=False)
                            return {{
                                "success": True,
                                "screenshot_path": screenshot_path,
                                "message": "{substep_plan['substep_description']} - already completed (element not visible)",
                                "error": None
                            }}
                except:
                    pass
                    pass
        
        # Priority 4: Check attribute value (e.g., button enabled/disabled)
        # ONLY pre-check for verify actions, not fill/click actions
        elif verification_check_type == "attribute_value":
            verify_selector = "{escape_selector(verification.get('selector', ''))}"
            attribute_name = "{verification.get('attribute', 'disabled')}"
            expected_value = "{verification.get('expected', 'false')}"
            action_type = "{action_type}"
            
            if action_type == "verify" and verify_selector:
                try:
                    await page.wait_for_selector(verify_selector, timeout=2000, state='visible')
                    element = page.locator(verify_selector)
                    
                    if attribute_name == "disabled":
                        is_disabled = await element.is_disabled()
                        expected_enabled = expected_value.lower() in ["false", "no", "enabled"]
                        
                        if (expected_enabled and not is_disabled) or (not expected_enabled and is_disabled):
                            print(f"[PRE-CHECK] Attribute already correct, goal achieved")
                            screenshot_path = f"substep_{substep_id}_precheck_success.png"
                            await page.screenshot(path=screenshot_path, full_page=False)
                            return {{
                                "success": True,
                                "screenshot_path": screenshot_path,
                                "message": "{substep_plan['substep_description']} - already completed (attribute check)",
                                "error": None
                            }}
                except:
                    pass  # Verification not met, proceed with action
        
'''
        
        # Generate action code based on type
        if action_type == 'click':
            # Escape quotes in selectors to prevent syntax errors
            primary_sel = escape_selector(target['primary_selector'])
            backup_sels = [escape_selector(s) for s in target.get('backup_selectors', [])]
            script += f'''        # Click action
        primary_selector = "{primary_sel}"
        backup_selectors = {backup_sels}
        
        # Try primary selector
        try:
            await page.wait_for_selector(primary_selector, timeout=5000, state='visible')
            
            # Scroll element into view before clicking
            element = page.locator(primary_selector)
            await element.scroll_into_view_if_needed()
            await page.wait_for_timeout(500)  # Wait for scroll animation
            
            await page.click(primary_selector, timeout=3000)
        except Exception as e:
            print(f"Primary selector failed: {{e}}")
            # Try backup selectors
            clicked = False
            for backup in backup_selectors:
                try:
                    await page.wait_for_selector(backup, timeout=3000, state='visible')
                    
                    # Scroll element into view
                    element = page.locator(backup)
                    await element.scroll_into_view_if_needed()
                    await page.wait_for_timeout(500)
                    
                    await page.click(backup, timeout=2000)
                    clicked = True
                    break
                except:
                    continue
            if not clicked:
                raise Exception(f"Could not click element: {{primary_selector}}")
        
        await page.wait_for_timeout(1000)  # Wait for action to complete
'''
        
        elif action_type == 'fill':
            script += f'''        # Fill action
        selector = "{escape_selector(target['primary_selector'])}"
        value = """{value}"""
        
        await page.wait_for_selector(selector, timeout=5000, state='visible')
        await page.fill(selector, value)
        await page.wait_for_timeout(500)
'''
        
        elif action_type == 'press_key':
            script += f'''        # Press key action
        await page.keyboard.press("{value}")
        await page.wait_for_timeout(500)
'''
        
        elif action_type == 'select':
            primary_sel = escape_selector(target['primary_selector'])
            backup_sels = [escape_selector(s) for s in target.get('backup_selectors', [])]
            script += f'''        # Select action (handles native <select> and custom dropdowns)
        primary_selector = "{primary_sel}"
        backup_selectors = {backup_sels}
        value = "{value}"
        
        # Try primary selector first
        success = False
        try:
            await page.wait_for_selector(primary_selector, timeout=5000, state='visible')
            element = page.locator(primary_selector)
            
            # Get element info
            tag_name = await element.evaluate("el => el.tagName.toLowerCase()")
            class_name = await element.get_attribute("class") or ""
            aria_haspopup = await element.get_attribute("aria-haspopup") or ""
            
            # Determine if it's a custom dropdown
            is_custom = (
                "action" in class_name or 
                "p-dropdown" in class_name or 
                "mat-select" in class_name or
                "ant-select" in class_name or
                aria_haspopup == "listbox" or
                "custom" in class_name
            )
            
            print(f"[SELECT] Element: {{tag_name}}, Class: {{class_name}}, Custom: {{is_custom}}")
            
            if tag_name == "select" and not is_custom:
                # Native select - try select_option with different strategies
                try:
                    # Try by label first
                    await page.select_option(primary_selector, label=value, timeout=3000)
                    print(f"[SELECT] Selected '{{value}}' by label from native select")
                    success = True
                except:
                    try:
                        # Try by value
                        await page.select_option(primary_selector, value=value, timeout=3000)
                        print(f"[SELECT] Selected '{{value}}' by value from native select")
                        success = True
                    except:
                        # Try by index (fallback)
                        options = await element.evaluate("el => Array.from(el.options).map(o => o.text)")
                        if value in options:
                            index = options.index(value)
                            await page.select_option(primary_selector, index=index, timeout=3000)
                            print(f"[SELECT] Selected '{{value}}' by index from native select")
                            success = True
            
            if not success:
                # Custom dropdown - click to open, then click option
                print(f"[SELECT] Treating as custom dropdown, clicking to open...")
                await element.click(timeout=3000)
                await page.wait_for_timeout(500)  # Wait for dropdown overlay to appear
                
                # Try multiple option selectors
                option_selectors = [
                    f"li:has-text('{{value}}')",
                    f"div[role='option']:has-text('{{value}}')",
                    f"[role='option']:has-text('{{value}}')",
                    f".p-dropdown-item:has-text('{{value}}')",
                    f".mat-option:has-text('{{value}}')",
                    f".ant-select-item:has-text('{{value}}')",
                    f"text={{value}}"
                ]
                
                clicked_option = False
                for opt_sel in option_selectors:
                    try:
                        await page.wait_for_selector(opt_sel, timeout=2000, state='visible')
                        await page.click(opt_sel, timeout=2000)
                        clicked_option = True
                        print(f"[SELECT] Clicked option '{{value}}' using selector: {{opt_sel}}")
                        break
                    except:
                        continue
                
                if clicked_option:
                    success = True
                else:
                    print(f"[SELECT] Could not find option '{{value}}' in dropdown overlay")
                    
        except Exception as e:
            print(f"[SELECT] Primary selector failed: {{e}}")
        
        # Try backup selectors if primary failed
        if not success:
            for backup in backup_selectors:
                try:
                    await page.wait_for_selector(backup, timeout=3000, state='visible')
                    element = page.locator(backup)
                    tag_name = await element.evaluate("el => el.tagName.toLowerCase()")
                    
                    if tag_name == "select":
                        try:
                            await page.select_option(backup, label=value, timeout=2000)
                            success = True
                            print(f"[SELECT] Selected using backup selector (native)")
                            break
                        except:
                            pass
                    
                    # Try as custom dropdown
                    await element.click(timeout=2000)
                    await page.wait_for_timeout(300)
                    await page.click(f"text={{value}}", timeout=2000)
                    success = True
                    print(f"[SELECT] Selected using backup selector (custom)")
                    break
                except:
                    continue
        
        if not success:
            raise Exception(f"Could not select value '{{value}}' in dropdown: {{primary_selector}}")
        
        await page.wait_for_timeout(500)
'''
        
        elif action_type == 'navigate':
            script += f'''        # Navigate action
        url = "{value}"
        await page.goto(url, wait_until='domcontentloaded')
        await page.wait_for_timeout(1000)
'''
        
        elif action_type == 'wait':
            script += f'''        # Wait action
        await page.wait_for_timeout({value or 2000})
'''
        
        elif action_type == 'verify':
            pass  # Verification will be added below
        
        # Add verification
        check_type = verification.get('check_type')
        if check_type == 'element_visible':
            verify_selector = escape_selector(verification.get('selector', target['primary_selector']))
            script += f'''        
        # Verify: element visible
        verify_selector = "{verify_selector}"
        await page.wait_for_selector(verify_selector, timeout=5000, state='visible')
        is_visible = await page.locator(verify_selector).is_visible()
        assert is_visible, f"Element not visible: {{verify_selector}}"
'''
        
        elif check_type == 'text_contains':
            expected_text = verification.get('expected', '')
            verify_selector = escape_selector(verification.get('selector', 'body'))
            script += f'''        
        # Verify: text contains
        verify_selector = "{verify_selector}"
        expected_text = "{expected_text}"
        
        element_text = await page.locator(verify_selector).text_content()
        assert expected_text.lower() in element_text.lower(), f"Text not found. Expected: {{expected_text}}, Got: {{element_text}}"
'''
        
        elif check_type == 'url_contains':
            expected_url = verification.get('expected', '')
            script += f'''        
        # Verify: URL contains
        expected_url = "{expected_url}"
        current_url = page.url
        assert expected_url in current_url, f"URL mismatch. Expected: {{expected_url}}, Got: {{current_url}}"
'''
        
        elif check_type == 'element_count':
            verify_selector = escape_selector(verification.get('selector', target['primary_selector']))
            expected_count = verification.get('expected', '> 0')
            script += f'''        
        # Verify: element count
        verify_selector = "{verify_selector}"
        count = await page.locator(verify_selector).count()
        assert count {expected_count}, f"Element count mismatch: {{count}}"
'''
        
        elif check_type == 'element_not_visible':
            verify_selector = escape_selector(verification.get('selector', target['primary_selector']))
            script += f'''        
        # Verify: element not visible (e.g., popup dismissed)
        verify_selector = "{verify_selector}"
        try:
            # Wait a bit for element to disappear
            await page.wait_for_timeout(500)
            element_count = await page.locator(verify_selector).count()
            if element_count > 0:
                is_visible = await page.locator(verify_selector).is_visible()
                assert not is_visible, f"Element should not be visible: {{verify_selector}}"
        except:
            # Element not found = not visible = good
            pass
'''
        
        elif check_type == 'attribute_value':
            verify_selector = escape_selector(verification.get('selector', target['primary_selector']))
            attribute_name = verification.get('attribute', 'disabled')
            expected_value = verification.get('expected', 'false')
            script += f'''        
        # Verify: attribute value (e.g., button enabled/disabled)
        verify_selector = "{verify_selector}"
        attribute_name = "{attribute_name}"
        expected_value = "{expected_value}"
        
        await page.wait_for_selector(verify_selector, timeout=5000, state='visible')
        element = page.locator(verify_selector)
        
        # For boolean attributes like 'disabled', check if attribute exists
        if attribute_name == "disabled":
            is_disabled = await element.is_disabled()
            if expected_value.lower() in ["false", "no", "enabled"]:
                assert not is_disabled, f"Element should be enabled but is disabled: {{verify_selector}}"
            else:
                assert is_disabled, f"Element should be disabled but is enabled: {{verify_selector}}"
        else:
            # For other attributes, get and compare value
            actual_value = await element.get_attribute(attribute_name)
            assert str(actual_value) == expected_value, f"Attribute '{{attribute_name}}' mismatch. Expected: {{expected_value}}, Got: {{actual_value}}"
'''
        
        # Screenshot and return
        script += f'''        
        # Capture success screenshot
        screenshot_path = f"substep_{substep_id}_success.png"
        await page.screenshot(path=screenshot_path, full_page=False)
        
        from datetime import datetime
        
        return {{
            "success": True,
            "screenshot_path": screenshot_path,
            "message": "{substep_plan['substep_description']} - completed successfully",
            "error": None,
            "timestamp": datetime.now().isoformat()
        }}
        
    except Exception as e:
        # Error handling
        import traceback
        from datetime import datetime
        
        error_details = traceback.format_exc()
        
        screenshot_path = f"substep_{substep_id}_error.png"
        try:
            await page.screenshot(path=screenshot_path, full_page=False)
        except:
            screenshot_path = None
        
        return {{
            "success": False,
            "screenshot_path": screenshot_path,
            "message": "{substep_plan['substep_description']} - failed",
            "error": str(e),
            "error_details": error_details,
            "timestamp": datetime.now().isoformat()
        }}
'''
        
        return script
    
    async def validate_step_completion(
        self,
        step_action: str,
        expected_result: str,
        page_html: str,
        current_url: str,
        substep_description: str = None,
        before_screenshot_url: str = None,
        after_screenshot_url: str = None
    ) -> Dict[str, Any]:
        """
        Sử dụng LLM để đánh giá xem step/substep đã hoàn thành chưa
        dựa trên HTML/DOM thực tế và expected result
        
        ENHANCED: Visual validation using screenshot comparison
        
        Args:
            step_action: Hành động của step (ví dụ: "Click folder 'uploads'")
            expected_result: Kết quả mong đợi của step
            page_html: HTML của page hiện tại (đã được làm sạch)
            current_url: URL hiện tại của page
            substep_description: Mô tả của substep vừa thực hiện (optional)
            before_screenshot_url: Screenshot TRƯỚC khi thực thi (optional)
            after_screenshot_url: Screenshot SAU khi thực thi (optional)
        
        Returns:
            {
                "is_completed": bool,
                "confidence": float (0-1),
                "reason": str,
                "evidence": str
            }
        """
        
        # Truncate HTML if too long (keep important parts)
        max_html_length = 15000
        if len(page_html) > max_html_length:
            # Keep beginning and end
            half = max_html_length // 2
            page_html = page_html[:half] + "\n\n...[TRUNCATED]...\n\n" + page_html[-half:]
        
        # VISUAL VALIDATION: If we have both screenshots, use vision model
        if before_screenshot_url and after_screenshot_url:
            print(f"[LLM_VALIDATE] Using VISUAL validation with screenshots")
            return await validate_with_vision(
                llm_client=self.client,
                step_action=step_action,
                expected_result=expected_result,
                before_screenshot_url=before_screenshot_url,
                after_screenshot_url=after_screenshot_url,
                current_url=current_url,
                substep_description=substep_description
            )
        
        # FALLBACK: Text-based validation with HTML/DOM
        print(f"[LLM_VALIDATE] Using TEXT-based validation (no screenshots available)")
        prompt = f"""You are an expert QA automation engineer evaluating whether a test step has been successfully completed.

STEP GOAL: {step_action}
EXPECTED RESULT: {expected_result}
{f'LAST ACTION PERFORMED: {substep_description}' if substep_description else ''}

CURRENT PAGE STATE:
URL: {current_url}

HTML/DOM CONTENT:
{page_html}

⚠️ WARNING: This validation uses TEXT-ONLY analysis (no visual screenshots).
DOM/HTML text can be MISLEADING because:
- Elements might exist in DOM but be visually hidden (display:none, z-index, etc.)
- Text like "License" might be unrelated UI elements (menu, footer, watermark)
- You CANNOT determine visual state from text alone

TASK: Analyze the current page state and determine if the step goal has been achieved.

EVALUATION CRITERIA:
1. Does the current URL indicate successful navigation? (e.g., if goal is "open folder uploads", URL should contain "uploads")
2. Does the HTML/DOM contain evidence of the expected result?
3. Are there any error messages or indicators of failure?
4. Has the page state changed in a way consistent with the goal?

CRITICAL RULES:
- Be VERY lenient when validating dismissal/close actions (modals, popups, dialogs)
- If goal is to "dismiss/close" something and execution succeeded, assume it's completed
- DOM text presence doesn't mean visual presence - many hidden elements remain in DOM
- URL changes are strong indicators of successful navigation
- Focus on the INTENT of the goal, not exact element matching

Respond ONLY with valid JSON:
{{
    "is_completed": true/false,
    "confidence": 0.0-1.0,
    "reason": "Clear explanation of why completed or not",
    "evidence": "Specific evidence from HTML/URL that supports your decision"
}}

Examples:
- Goal: "Click folder 'uploads'" + URL contains "/uploads/" → is_completed: true
- Goal: "Dismiss modal" + Execution succeeded → is_completed: true (DOM text is unreliable)
- Goal: "Open settings" + URL still same + No settings content in HTML → is_completed: false
"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert QA engineer evaluating test step completion. Be practical and focus on whether the goal was achieved."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.2,
                response_format={"type": "json_object"}
            )
            
            result_text = response.choices[0].message.content
            result = json.loads(result_text)
            
            return result
            
        except Exception as e:
            print(f"Error in LLM validation: {e}")
            # Fallback: assume not completed on error
            return {
                "is_completed": False,
                "confidence": 0.0,
                "reason": f"Validation error: {str(e)}",
                "evidence": "N/A"
            }