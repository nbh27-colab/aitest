"""
LLM generator cho substeps và scripts
"""

import json
from typing import Dict, Any, Optional
import base64
from openai import AsyncOpenAI


class LLMGenerator:
    """Generate substeps và Playwright scripts sử dụng LLM"""
    
    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model
    
    async def generate_substep_plan(
        self, 
        step: Dict[str, Any], 
        context: Dict[str, Any],
        substep_index: int,
        page_stuck: bool = False
    ) -> Dict[str, Any]:
        """
        Sử dụng LLM để generate kế hoạch cho substep tiếp theo
        dựa trên step action, expected result và page context hiện tại
        
        UPDATED: Now uses enhanced context with HTML structure and previous errors
        """
        
        # Format context cho prompt
        from .page_context import format_context_for_llm
        context_text = format_context_for_llm(context)
        
        # Extract error patterns from previous failures to help LLM avoid them
        error_learnings = []
        intermediate_progress = []
        
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

{progress_context}{stuck_warning}
{f'''
⚠️ LEARNED FROM PREVIOUS ERRORS:
{error_context}
''' if error_context else ''}

TASK: Based on the current page state (including HTML structure) and previous actions, determine the NEXT single, atomic action needed to achieve the goal.

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
            verify_selector = "{verification.get('selector', target['primary_selector'])}"
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
            verify_selector = "{verification.get('selector', '')}"
            if verify_selector:
                try:
                    element_count = await page.locator(verify_selector).count()
                    if element_count == 0:
                        print(f"[PRE-CHECK] Element already not visible, goal achieved")
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
        
        # Priority 4: Check attribute value (e.g., button enabled/disabled)
        # ONLY pre-check for verify actions, not fill/click actions
        elif verification_check_type == "attribute_value":
            verify_selector = "{verification.get('selector', '')}"
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
            script += f'''        # Click action
        primary_selector = "{target['primary_selector']}"
        backup_selectors = {target['backup_selectors']}
        
        # Try primary selector
        try:
            await page.wait_for_selector(primary_selector, timeout=5000, state='visible')
            await page.click(primary_selector, timeout=3000)
        except Exception as e:
            print(f"Primary selector failed: {{e}}")
            # Try backup selectors
            clicked = False
            for backup in backup_selectors:
                try:
                    await page.wait_for_selector(backup, timeout=3000, state='visible')
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
        selector = "{target['primary_selector']}"
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
            script += f'''        # Select action
        selector = "{target['primary_selector']}"
        value = "{value}"
        
        await page.wait_for_selector(selector, timeout=5000, state='visible')
        await page.select_option(selector, value)
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
            verify_selector = verification.get('selector', target['primary_selector'])
            script += f'''        
        # Verify: element visible
        verify_selector = "{verify_selector}"
        await page.wait_for_selector(verify_selector, timeout=5000, state='visible')
        is_visible = await page.locator(verify_selector).is_visible()
        assert is_visible, f"Element not visible: {{verify_selector}}"
'''
        
        elif check_type == 'text_contains':
            expected_text = verification.get('expected', '')
            verify_selector = verification.get('selector', 'body')
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
            verify_selector = verification.get('selector', target['primary_selector'])
            expected_count = verification.get('expected', '> 0')
            script += f'''        
        # Verify: element count
        verify_selector = "{verify_selector}"
        count = await page.locator(verify_selector).count()
        assert count {expected_count}, f"Element count mismatch: {{count}}"
'''
        
        elif check_type == 'element_not_visible':
            verify_selector = verification.get('selector', target['primary_selector'])
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
            verify_selector = verification.get('selector', target['primary_selector'])
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
        substep_description: str = None
    ) -> Dict[str, Any]:
        """
        Sử dụng LLM để đánh giá xem step/substep đã hoàn thành chưa
        dựa trên HTML/DOM thực tế và expected result
        
        Args:
            step_action: Hành động của step (ví dụ: "Click folder 'uploads'")
            expected_result: Kết quả mong đợi của step
            page_html: HTML của page hiện tại (đã được làm sạch)
            current_url: URL hiện tại của page
            substep_description: Mô tả của substep vừa thực hiện (optional)
        
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
        
        prompt = f"""You are an expert QA automation engineer evaluating whether a test step has been successfully completed.

STEP GOAL: {step_action}
EXPECTED RESULT: {expected_result}
{f'LAST ACTION PERFORMED: {substep_description}' if substep_description else ''}

CURRENT PAGE STATE:
URL: {current_url}

HTML/DOM CONTENT:
{page_html}

TASK: Analyze the current page state and determine if the step goal has been achieved.

EVALUATION CRITERIA:
1. Does the current URL indicate successful navigation? (e.g., if goal is "open folder uploads", URL should contain "uploads")
2. Does the HTML/DOM contain evidence of the expected result?
3. Are there any error messages or indicators of failure?
4. Has the page state changed in a way consistent with the goal?

CRITICAL RULES:
- Be lenient: If there's clear evidence the goal was achieved, mark as completed even if details differ
- URL changes are strong indicators of successful navigation
- Focus on the INTENT of the goal, not exact element matching
- If you see the expected content/state, it's completed

Respond ONLY with valid JSON:
{{
    "is_completed": true/false,
    "confidence": 0.0-1.0,
    "reason": "Clear explanation of why completed or not",
    "evidence": "Specific evidence from HTML/URL that supports your decision"
}}

Examples:
- Goal: "Click folder 'uploads'" + URL contains "/uploads/" → is_completed: true
- Goal: "Dismiss modal" + Modal element not found in HTML → is_completed: true
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
                "action_type": "enter_email" | "enter_password" | "click_next" | "click_submit" | "wait_for_redirect" | "completed" | "error",
                "target": {"primary_selector": "...", "fallback_selectors": [...]},
                "reason": "...",
                "confidence": 0.0-1.0
            }
        """
        
        from .page_context import format_context_for_llm
        context_text = format_context_for_llm(page_context)
        
        prompt = f"""You are an intelligent login automation agent. Analyze the current page and determine the next action to complete login.

**Login Credentials:**
- Email/Username: {login_info.get('email', 'N/A')}
- Password: [REDACTED]

**Current Login State:**
- Email entered: {login_state.get('email_entered', False)}
- Password entered: {login_state.get('password_entered', False)}
- Current URL: {login_state.get('current_url', 'unknown')}
- Attempt: {login_state.get('attempts', 1)}

**Current Page Context:**
{context_text}

**Task:**
Determine the NEXT ACTION to complete the login process. This could be:
1. **enter_email** - If email/username field is visible and not yet filled
2. **enter_password** - If password field is visible and not yet filled
3. **click_next** - If there's a "Next" or "Continue" button (common in multi-step login like Microsoft)
4. **click_submit** - If both credentials are entered and submit button is visible
5. **wait_for_redirect** - If page is redirecting (OAuth/SSO flow)
6. **completed** - If login appears successful (logged in page detected)
7. **error** - If error message detected or stuck

**Important:**
- Support multi-step flows: email → next → password → submit
- Detect OAuth/SSO redirects
- Use actual selectors from the page context
- Provide fallback selectors
- Be smart about detecting success (URL changes, user menu visible, etc.)

Return JSON:
{{
    "action_type": "enter_email|enter_password|click_next|click_submit|wait_for_redirect|completed|error",
    "target": {{
        "primary_selector": "CSS selector for target element",
        "fallback_selectors": ["alternative selector 1", "alternative selector 2"]
    }},
    "reason": "Brief explanation of why this action",
    "confidence": 0.0-1.0
}}

For wait_for_redirect/completed/error, target can be null.
"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert at web automation and login flows. You understand multi-step authentication, OAuth, SSO, and various login patterns."
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

