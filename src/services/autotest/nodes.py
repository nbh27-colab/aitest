"""
LangGraph workflow nodes cho autotest
"""

import asyncio
import os
import tempfile
from typing import Dict, Any, Optional
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

from .state import AutoTestState
from .repository import AutoTestRepository
from .page_context import get_page_context
from .llm_generator import LLMGenerator


class AutoTestNodes:
    """Các nodes trong LangGraph workflow"""
    
    def __init__(self, db_session, minio_client, openai_api_key: str):
        self.repository = AutoTestRepository(db_session)
        self.minio_client = minio_client
        self.llm_generator = LLMGenerator(api_key=openai_api_key)
        self.playwright_context = None
        self.browser = None
    
    async def _upload_screenshot_to_minio(
        self, 
        screenshot_bytes: bytes, 
        filename: str,
        bucket_name: str = "testcase-bucket"
    ) -> Optional[str]:
        """
        Upload screenshot to MinIO and return public URL
        
        Args:
            screenshot_bytes: Screenshot image data as bytes
            filename: Name for the screenshot file
            bucket_name: MinIO bucket name (default: "testcase-bucket")
            
        Returns:
            Public URL of uploaded screenshot or None if failed
        """
        try:
            # Create temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_file:
                tmp_file.write(screenshot_bytes)
                tmp_path = tmp_file.name
            
            try:
                # Upload to MinIO in screenshots folder
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                remote_folder = f"screenshots/{datetime.now().strftime('%Y-%m-%d')}"
                safe_filename = f"{timestamp}_{filename}"
                
                public_url, _ = self.minio_client.upload_file_from_path(
                    bucket_name=bucket_name,
                    local_file_path=tmp_path,
                    remote_folder=remote_folder
                )
                
                print(f"[MINIO] Screenshot uploaded: {public_url}")
                return public_url
                
            finally:
                # Clean up temp file
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                    
        except Exception as e:
            print(f"[MINIO] Failed to upload screenshot: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _is_duplicate_plan(self, new_plan: Dict[str, Any], recent_plans: list, window: int = 3) -> bool:
        """
        Check if new plan is duplicate of recent plans
        
        ENHANCED: Now considers if plan is duplicate AND page state hasn't changed
        
        Args:
            new_plan: The newly generated substep plan
            recent_plans: List of recent substep plans
            window: Number of recent plans to check (default: 3)
        
        Returns:
            True if duplicate detected, False otherwise
        """
        if len(recent_plans) < 2:  # Need at least 2 to detect pattern
            return False
        
        # Get last N plans
        recent = recent_plans[-window:]
        
        new_action = new_plan.get('action_type', '')
        new_selector = new_plan.get('target_element', {}).get('primary_selector', '')
        new_desc = new_plan.get('substep_description', '')
        
        duplicate_count = 0
        
        for plan in recent:
            plan_action = plan.get('action_type', '')
            plan_selector = plan.get('target_element', {}).get('primary_selector', '')
            plan_desc = plan.get('substep_description', '')
            
            # Check if action + selector match (exact duplicate)
            if (plan_action == new_action and plan_selector == new_selector):
                duplicate_count += 1
                print(f"[DUPLICATE_CHECK] Found duplicate: {new_action} on {new_selector}")
            
            # Also check if description is very similar (fuzzy match)
            elif new_desc and plan_desc:
                # Simple similarity: check if 80% of words overlap
                new_words = set(new_desc.lower().split())
                plan_words = set(plan_desc.lower().split())
                if len(new_words) > 0:
                    overlap = len(new_words & plan_words) / len(new_words)
                    if overlap > 0.8:
                        duplicate_count += 1
                        print(f"[DUPLICATE_CHECK] Found similar description: {overlap:.0%} overlap")
        
        # If we've tried the same action 2+ times recently, it's a duplicate
        if duplicate_count >= 2:
            print(f"[DUPLICATE_CHECK] Pattern detected: tried same action {duplicate_count} times")
            return True
        
        return False
    
    async def _detect_intermediate_progress(self, page, substep_plan: Dict[str, Any]) -> Optional[str]:
        """
        Detect if action made intermediate progress even if final goal not reached
        
        Examples of intermediate progress:
        - Click "Create Bucket" → Modal opened (even if bucket not created yet)
        - Click "Add User" → Form appeared (even if user not added yet)
        - Fill email → Submit button enabled (even if form not submitted yet)
        
        Returns:
            Description of intermediate progress, or None if no progress detected
        """
        try:
            action_type = substep_plan.get('action_type', '')
            
            # Common intermediate states to check
            intermediate_checks = {
                'modal_opened': [
                    '.modal', '[role="dialog"]', '.dialog', '.popup',
                    '[aria-modal="true"]', '.overlay'
                ],
                'form_visible': [
                    'form', '.form-container', '[role="form"]',
                    'input[type="text"]', 'input[type="email"]'
                ],
                'button_enabled': [
                    'button:not([disabled])', 
                    'button[aria-disabled="false"]',
                    'input[type="submit"]:not([disabled])'
                ],
                'dropdown_opened': [
                    '[role="listbox"]', '.dropdown-menu',
                    'select[aria-expanded="true"]', 'ul.options'
                ],
                'loading_completed': [
                    ':not(.loading)', ':not([aria-busy="true"])'
                ]
            }
            
            # Check each intermediate state
            progress_detected = []
            for state_name, selectors in intermediate_checks.items():
                for selector in selectors:
                    try:
                        count = await page.locator(selector).count()
                        if count > 0 and state_name not in ['loading_completed']:
                            # Verify element is actually visible
                            is_visible = await page.locator(selector).first.is_visible()
                            if is_visible:
                                progress_detected.append(state_name.replace('_', ' '))
                                break
                    except:
                        continue
            
            if progress_detected:
                return f"{', '.join(set(progress_detected))}"
            
            return None
            
        except Exception as e:
            print(f"[INTERMEDIATE_PROGRESS] Error checking: {e}")
            return None
    
    async def initialize(self, state: AutoTestState) -> AutoTestState:
        """
        Node 1: Khởi tạo workflow
        - Load test case, steps, login info
        - Khởi tạo Playwright browser
        """
        print(f"[INITIALIZE] Starting autotest for test_case_id={state['test_case_id']}")
        
        try:
            # Load test case
            test_case = self.repository.get_test_case(state['test_case_id'])
            if not test_case:
                raise Exception(f"Test case {state['test_case_id']} not found")
            
            # Load steps
            steps = self.repository.get_steps(state['test_case_id'])
            if not steps:
                raise Exception(f"No steps found for test case {state['test_case_id']}")
            
            # Load login info
            login_info = self.repository.get_login_info(state['login_info_id'])
            if not login_info:
                raise Exception(f"Login info {state['login_info_id']} not found")
            
            # Initialize Playwright
            self.playwright_context = await async_playwright().start()
            self.browser = await self.playwright_context.chromium.launch(
                headless=False,  # Set to True for production
                slow_mo=500  # Slow down for debugging
            )
            context = await self.browser.new_context(
                viewport={'width': 1080, 'height': 720}
            )
            page = await context.new_page()
            
            # Update state
            state['test_case'] = self.repository.model_to_dict(test_case)
            state['steps'] = [self.repository.model_to_dict(s) for s in steps]
            state['login_info'] = self.repository.model_to_dict(login_info)
            state['browser'] = self.browser
            state['page'] = page
            state['current_step_index'] = 0
            state['current_substep_index'] = 0
            state['login_completed'] = False
            state['overall_status'] = 'running'
            state['start_time'] = datetime.now().isoformat()
            state['substep_plans'] = []
            state['execution_results'] = []
            state['generated_scripts'] = []
            
            print(f"[INITIALIZE] Loaded {len(steps)} steps")
            return state
            
        except Exception as e:
            print(f"[INITIALIZE] Error: {e}")
            state['overall_status'] = 'error'
            state['error_message'] = str(e)
            return state
    
    async def auto_login(self, state: AutoTestState) -> AutoTestState:
        """
        Node 2: Tự động đăng nhập thông minh với LLM
        - Hỗ trợ multi-step login (Microsoft, Google, AWS, etc.)
        - Tự động phát hiện login flow
        - Sử dụng LLM để generate actions
        
        Hỗ trợ:
        - Simple form (email + password cùng page)
        - Multi-step (email → next → password)
        - OAuth/SSO redirects
        - Dynamic selectors
        """
        print(f"[AUTO_LOGIN] Starting intelligent auto login")
        
        try:
            page = state['page']
            login_info = state['login_info']
            
            # Navigate to web URL
            print(f"[AUTO_LOGIN] Navigating to {login_info['web_url']}")
            await page.goto(login_info['web_url'], wait_until='domcontentloaded')
            await page.wait_for_timeout(2000)
            
            # Take initial screenshot and upload to MinIO
            screenshot_bytes = await page.screenshot(type='png', full_page=False)
            screenshot_url = await self._upload_screenshot_to_minio(
                screenshot_bytes=screenshot_bytes,
                filename='login_page_initial.png'
            )
            if screenshot_url:
                state['login_initial_screenshot_url'] = screenshot_url
                print(f"[AUTO_LOGIN] Initial screenshot saved: {screenshot_url}")
            
            # Initialize login state tracking
            login_state = {
                'email_entered': False,
                'password_entered': False,
                'submitted': False,
                'current_url': page.url,
                'attempts': 0,
                'max_attempts': 10,  # Prevent infinite loops
                'executed_actions': []  # Track all actions for creating substeps
            }
            
            # Login workflow loop - handle multi-step login
            while not login_state['submitted'] and login_state['attempts'] < login_state['max_attempts']:
                login_state['attempts'] += 1
                print(f"\n[AUTO_LOGIN] Login attempt {login_state['attempts']}/{login_state['max_attempts']}")
                
                # Get current page context
                context = await get_page_context(page, [])
                
                # Determine what to do next using LLM
                login_action = await self.llm_generator.generate_login_action(
                    login_info=login_info,
                    page_context=context,
                    login_state=login_state
                )
                
                print(f"[AUTO_LOGIN] LLM Action: {login_action['action_type']}")
                print(f"[AUTO_LOGIN] Reason: {login_action['reason']}")
                
                # Execute the action
                if login_action['action_type'] == 'enter_email':
                    selector = login_action['target']['primary_selector']
                    try:
                        await page.wait_for_selector(selector, timeout=5000, state='visible')
                        await page.fill(selector, login_info['email'])
                        login_state['email_entered'] = True
                        print(f"[AUTO_LOGIN] ✓ Filled email: {selector}")
                        await page.wait_for_timeout(1000)
                        
                        # Track this action
                        login_state['executed_actions'].append({
                            'action_type': 'enter_email',
                            'selector': selector,
                            'success': True,
                            'description': f"Fill email field with '{login_info['email']}'"
                        })
                    except Exception as e:
                        print(f"[AUTO_LOGIN] ✗ Failed to fill email: {e}")
                        login_state['executed_actions'].append({
                            'action_type': 'enter_email',
                            'selector': selector,
                            'success': False,
                            'error': str(e),
                            'description': f"Attempt to fill email field (failed)"
                        })
                
                elif login_action['action_type'] == 'enter_password':
                    selector = login_action['target']['primary_selector']
                    try:
                        await page.wait_for_selector(selector, timeout=5000, state='visible')
                        await page.fill(selector, login_info['password'])
                        login_state['password_entered'] = True
                        print(f"[AUTO_LOGIN] ✓ Filled password: {selector}")
                        await page.wait_for_timeout(1000)
                        
                        # Track this action
                        login_state['executed_actions'].append({
                            'action_type': 'enter_password',
                            'selector': selector,
                            'success': True,
                            'description': f"Fill password field"
                        })
                    except Exception as e:
                        print(f"[AUTO_LOGIN] ✗ Failed to fill password: {e}")
                        login_state['executed_actions'].append({
                            'action_type': 'enter_password',
                            'selector': selector,
                            'success': False,
                            'error': str(e),
                            'description': f"Attempt to fill password field (failed)"
                        })
                
                elif login_action['action_type'] == 'click_next':
                    selector = login_action['target']['primary_selector']
                    try:
                        await page.wait_for_selector(selector, timeout=5000, state='visible')
                        await page.click(selector)
                        print(f"[AUTO_LOGIN] ✓ Clicked next: {selector}")
                        # Wait for page transition
                        await page.wait_for_load_state('networkidle', timeout=10000)
                        await page.wait_for_timeout(2000)
                        
                        # Track this action
                        login_state['executed_actions'].append({
                            'action_type': 'click_next',
                            'selector': selector,
                            'success': True,
                            'description': f"Click 'Next' button"
                        })
                    except Exception as e:
                        print(f"[AUTO_LOGIN] ✗ Failed to click next: {e}")
                        login_state['executed_actions'].append({
                            'action_type': 'click_next',
                            'selector': selector,
                            'success': False,
                            'error': str(e),
                            'description': f"Attempt to click 'Next' button (failed)"
                        })
                
                elif login_action['action_type'] == 'click_submit':
                    selector = login_action['target']['primary_selector']
                    try:
                        await page.wait_for_selector(selector, timeout=5000, state='visible')
                        await page.click(selector)
                        print(f"[AUTO_LOGIN] ✓ Clicked submit: {selector}")
                        login_state['submitted'] = True
                        # Wait for login to complete
                        await page.wait_for_load_state('networkidle', timeout=15000)
                        await page.wait_for_timeout(3000)
                        
                        # Track this action
                        login_state['executed_actions'].append({
                            'action_type': 'click_submit',
                            'selector': selector,
                            'success': True,
                            'description': f"Click login/submit button"
                        })
                    except Exception as e:
                        print(f"[AUTO_LOGIN] ✗ Failed to click submit: {e}")
                        login_state['executed_actions'].append({
                            'action_type': 'click_submit',
                            'selector': selector,
                            'success': False,
                            'error': str(e),
                            'description': f"Attempt to click login/submit button (failed)"
                        })
                
                elif login_action['action_type'] == 'wait_for_redirect':
                    print(f"[AUTO_LOGIN] ⏳ Waiting for OAuth/SSO redirect...")
                    await page.wait_for_load_state('networkidle', timeout=15000)
                    await page.wait_for_timeout(2000)
                
                elif login_action['action_type'] == 'completed':
                    print(f"[AUTO_LOGIN] ✓ Login detected as completed")
                    login_state['submitted'] = True
                    break
                
                elif login_action['action_type'] == 'error':
                    print(f"[AUTO_LOGIN] ✗ Error detected: {login_action['reason']}")
                    raise Exception(login_action['reason'])
                
                # Update current URL
                login_state['current_url'] = page.url
            
            # Verify login success
            await page.wait_for_timeout(2000)
            
            # Take screenshot and upload to MinIO
            screenshot_bytes = await page.screenshot(type='png', full_page=False)
            screenshot_url = await self._upload_screenshot_to_minio(
                screenshot_bytes=screenshot_bytes,
                filename='login_success.png'
            )
            
            # Validate login using LLM
            context = await get_page_context(page, [])
            validation = await self.llm_generator.validate_login_success(
                page_context=context,
                initial_url=login_info['web_url'],
                current_url=page.url
            )
            
            if validation['is_logged_in']:
                print(f"[AUTO_LOGIN] ✓ Login verified successful: {validation['reason']}")
                state['login_completed'] = True
            else:
                print(f"[AUTO_LOGIN] ⚠ Login may have failed: {validation['reason']}")
                # Continue anyway, maybe manual verification needed
                state['login_completed'] = True
                state['error_message'] = f"Login verification uncertain: {validation['reason']}"
            
            # Create substep and generated_script for login to save properly to database
            if state.get('steps'):
                try:
                    first_step = state['steps'][0]
                    
                    # Create separate substeps for each executed action
                    substep_order = 1
                    created_substeps = []
                    
                    for action in login_state['executed_actions']:
                        # Create substep for each action
                        login_substep = self.repository.create_substep(
                            step_id=first_step['step_id'],
                            sub_step_order=substep_order,
                            sub_step_content=action['description'],
                            expected_result=f"Successfully {action['action_type'].replace('_', ' ')}"
                        )
                        print(f"[AUTO_LOGIN] Created login substep {substep_order}: substep_id={login_substep.sub_step_id}")
                        
                        # Generate Playwright script content for this action
                        if action['action_type'] in ['enter_email', 'enter_password']:
                            script_content = f"""# Auto-generated login script - {action['description']}
async def execute(page):
    selector = "{action['selector']}"
    await page.wait_for_selector(selector, timeout=5000, state='visible')
    await page.fill(selector, "{'***' if 'password' in action['action_type'] else action.get('value', 'email')}")
    await page.wait_for_timeout(1000)
"""
                        elif action['action_type'] in ['click_submit', 'click_next']:
                            script_content = f"""# Auto-generated login script - {action['description']}
async def execute(page):
    selector = "{action['selector']}"
    await page.wait_for_selector(selector, timeout=5000, state='visible')
    await page.click(selector)
    await page.wait_for_load_state('networkidle', timeout=15000)
    await page.wait_for_timeout(2000)
"""
                        else:
                            script_content = f"""# Auto-generated login script - {action['description']}
# Action type: {action['action_type']}
# Success: {action['success']}
"""
                        
                        # Create generated script for this action
                        login_script = self.repository.create_generated_script(
                            sub_step_id=login_substep.sub_step_id,
                            script_content=script_content
                        )
                        print(f"[AUTO_LOGIN] Created login script {substep_order}: script_id={login_script.generated_script_id}")
                        
                        # Save test result for this action
                        self.repository.create_test_result(
                            object_id=login_substep.sub_step_id,
                            object_type='sub_step',
                            result=action['success'],
                            reason=action.get('error', 'Action executed successfully')
                        )
                        
                        created_substeps.append({
                            'substep': login_substep,
                            'script': login_script,
                            'action': action
                        })
                        
                        substep_order += 1
                    
                    # Create final validation substep
                    validation_substep = self.repository.create_substep(
                        step_id=first_step['step_id'],
                        sub_step_order=substep_order,
                        sub_step_content="Verify login successful",
                        expected_result="User is logged in to the application"
                    )
                    print(f"[AUTO_LOGIN] Created validation substep {substep_order}: substep_id={validation_substep.sub_step_id}")
                    
                    # Create script for validation
                    validation_script_content = f"""# Auto-generated login validation
# Login attempts: {login_state['attempts']}
# Email entered: {login_state['email_entered']}
# Password entered: {login_state['password_entered']}
# Final URL: {page.url}
# Validation result: {validation['is_logged_in']}
# Validation reason: {validation['reason']}
"""
                    validation_script = self.repository.create_generated_script(
                        sub_step_id=validation_substep.sub_step_id,
                        script_content=validation_script_content
                    )
                    print(f"[AUTO_LOGIN] Created validation script {substep_order}: script_id={validation_script.generated_script_id}")
                    
                    # Save login screenshot to validation substep
                    if screenshot_url:
                        self.repository.create_screenshot(
                            generated_script_id=validation_script.generated_script_id,
                            screenshot_link=screenshot_url
                        )
                        state['login_screenshot_url'] = screenshot_url
                        print(f"[AUTO_LOGIN] Screenshot saved to database: {screenshot_url}")
                    
                    # Save test result for validation
                    self.repository.create_test_result(
                        object_id=validation_substep.sub_step_id,
                        object_type='sub_step',
                        result=validation['is_logged_in'],
                        reason=validation['reason']
                    )
                    print(f"[AUTO_LOGIN] Test result saved: {validation['is_logged_in']}")
                    
                    # Add each action to execution results
                    for item in created_substeps:
                        state['execution_results'].append({
                            'success': item['action']['success'],
                            'screenshot_url': None,  # Individual actions don't have screenshots
                            'message': item['action']['description'],
                            'page_url': page.url,
                            'timestamp': datetime.now().isoformat()
                        })
                    
                    # Add validation to execution results
                    state['execution_results'].append({
                        'success': validation['is_logged_in'],
                        'screenshot_url': screenshot_url,
                        'message': validation['reason'],
                        'page_url': page.url,
                        'timestamp': datetime.now().isoformat()
                    })
                    
                except Exception as db_error:
                    print(f"[AUTO_LOGIN] Failed to save to database: {db_error}")
                    import traceback
                    traceback.print_exc()
                    # Still save screenshot URL to state
                    if screenshot_url:
                        state['login_screenshot_url'] = screenshot_url
            
            # Check if Step 1 is login-related and mark as completed
            if state['steps']:
                first_step = state['steps'][0]
                step_action = first_step.get('action', '').strip().lower()
                step_order = first_step.get('step_order', 1)
                
                print(f"[AUTO_LOGIN] Checking Step {step_order} action: '{step_action}'")
                
                is_login_step = (
                    any(keyword in step_action for keyword in ['login', 'đăng nhập', 'sign in', 'log in', 'minio', 'authenticate']) or
                    (step_order == 1 and not step_action)  # Empty step 1 is likely login
                )
                
                if is_login_step:
                    print(f"[AUTO_LOGIN] Step {step_order} detected as login step, marking as completed")
                    if 0 not in state['completed_steps']:
                        state['completed_steps'].append(0)
                    state['current_step_index'] = 1  # Skip to step 2
                    print(f"[AUTO_LOGIN] Skipping to Step 2, completed_steps: {state['completed_steps']}")
            
            print(f"[AUTO_LOGIN] Login process completed")
            return state
            
        except Exception as e:
            print(f"[AUTO_LOGIN] Error: {e}")
            import traceback
            traceback.print_exc()
            
            # Take error screenshot and upload to MinIO
            error_screenshot_url = None
            try:
                screenshot_bytes = await page.screenshot(type='png', full_page=False)
                error_screenshot_url = await self._upload_screenshot_to_minio(
                    screenshot_bytes=screenshot_bytes,
                    filename='login_error.png'
                )
                if error_screenshot_url:
                    print(f"[AUTO_LOGIN] Error screenshot uploaded: {error_screenshot_url}")
            except Exception as screenshot_error:
                print(f"[AUTO_LOGIN] Failed to capture error screenshot: {screenshot_error}")
            
            # Save error to database (create substep and result for failed login)
            if state.get('steps') and error_screenshot_url:
                try:
                    first_step = state['steps'][0]
                    
                    # Create login substep for error case
                    login_substep = self.repository.create_substep(
                        step_id=first_step['step_id'],
                        sub_step_order=1,
                        sub_step_content="Auto login using LLM (FAILED)",
                        expected_result="Successfully logged in to the application"
                    )
                    
                    # Create generated script for failed login
                    error_script = self.repository.create_generated_script(
                        sub_step_id=login_substep.sub_step_id,
                        script_content=f"# Login failed with error:\n# {str(e)}"
                    )
                    
                    # Save error screenshot to database
                    self.repository.create_screenshot(
                        generated_script_id=error_script.generated_script_id,
                        screenshot_link=error_screenshot_url
                    )
                    
                    # Save test result as failure
                    self.repository.create_test_result(
                        object_id=login_substep.sub_step_id,
                        object_type='sub_step',
                        result=False,
                        reason=f"Login failed: {str(e)}"
                    )
                    
                    # Add to execution results
                    state['execution_results'].append({
                        'success': False,
                        'screenshot_url': error_screenshot_url,
                        'message': f"Login failed: {str(e)}",
                        'error': str(e),
                        'page_url': page.url if page else 'unknown',
                        'timestamp': datetime.now().isoformat()
                    })
                    
                    print(f"[AUTO_LOGIN] Error saved to database")
                except Exception as db_error:
                    print(f"[AUTO_LOGIN] Failed to save error to database: {db_error}")
            
            if error_screenshot_url:
                state['login_error_screenshot_url'] = error_screenshot_url
            
            state['error_message'] = f"Login failed: {str(e)}"
            # Continue anyway, maybe login is not required
            state['login_completed'] = True
            return state
    
    async def get_current_context(self, state: AutoTestState) -> AutoTestState:
        """
        Node 3: Lấy context của page hiện tại
        
        ENHANCED: Track page state changes to detect stuck situations
        """
        # Check if we've finished all steps
        if state.get('overall_status') == 'completed' or state['current_step_index'] >= len(state['steps']):
            print(f"[GET_CONTEXT] All steps completed, skipping context extraction")
            return state
        
        print(f"[GET_CONTEXT] Extracting page context")
        
        try:
            page = state['page']
            context = await get_page_context(page, state['execution_results'])
            state['page_context'] = context
            
            print(f"[GET_CONTEXT] Current URL: {context['current_url']}")
            print(f"[GET_CONTEXT] Found {len(context['visible_elements'])} interactive elements")
            
            # NEW: Track page state for stuck detection
            current_url = context['current_url']
            current_html = await page.content()
            current_html_hash = hash(current_html[:5000])  # Hash first 5000 chars for efficiency
            
            # Initialize tracking if not exists
            if 'page_state_tracking' not in state:
                state['page_state_tracking'] = []
            
            # Add current state to tracking
            current_state = {
                'url': current_url,
                'html_hash': current_html_hash,
                'element_count': len(context['visible_elements']),
                'timestamp': datetime.now().isoformat()
            }
            state['page_state_tracking'].append(current_state)
            
            # Keep only last 5 states
            if len(state['page_state_tracking']) > 5:
                state['page_state_tracking'] = state['page_state_tracking'][-5:]
            
            # Check if page is stuck (no changes in last 3 attempts)
            if len(state['page_state_tracking']) >= 3:
                last_3 = state['page_state_tracking'][-3:]
                urls = [s['url'] for s in last_3]
                hashes = [s['html_hash'] for s in last_3]
                
                if len(set(urls)) == 1 and len(set(hashes)) == 1:
                    print(f"[GET_CONTEXT] ⚠️ Page stuck detected - no changes in last 3 attempts")
                    state['page_stuck_detected'] = True
                else:
                    state['page_stuck_detected'] = False
            
            return state
            
        except Exception as e:
            print(f"[GET_CONTEXT] Error: {e}")
            state['page_context'] = {
                'current_url': 'unknown',
                'visible_elements': [],
                'error': str(e)
            }
            return state
    
    async def generate_next_substep(self, state: AutoTestState) -> AutoTestState:
        """
        Node 4: Generate substep tiếp theo với LLM
        """
        current_step_idx = state['current_step_index']
        
        # Check if we've finished all steps
        if state.get('overall_status') == 'completed' or current_step_idx >= len(state['steps']):
            print(f"[GENERATE_SUBSTEP] All steps completed")
            state['overall_status'] = 'completed'
            return state
        
        # CRITICAL: Check if this step is already completed
        # This should never happen if decision logic is correct
        if current_step_idx in state.get('completed_steps', []):
            print(f"[GENERATE_SUBSTEP] ERROR: Step {current_step_idx} is already completed!")
            print(f"[GENERATE_SUBSTEP] Completed steps: {state['completed_steps']}")
            print(f"[GENERATE_SUBSTEP] This indicates workflow decision bug")
            
            # Force finish to prevent infinite loop
            state['overall_status'] = 'error'
            state['error_message'] = f"Workflow bug: trying to generate substeps for already-completed step {current_step_idx}"
            return state
        
        current_step = state['steps'][current_step_idx]
        substep_index = state['current_substep_index']
        
        print(f"[GENERATE_SUBSTEP] Step {current_step_idx + 1}/{len(state['steps'])}: {current_step.get('action', '')[:50]}...")
        print(f"[GENERATE_SUBSTEP] Generating substep {substep_index + 1}")
        print(f"[GENERATE_SUBSTEP] Completed steps: {state.get('completed_steps', [])}")
        
        # NEW: Check if page is stuck before generating more substeps
        if state.get('page_stuck_detected') and substep_index >= 2:
            print(f"[GENERATE_SUBSTEP] ⚠️ Page stuck detected and already tried {substep_index + 1} substeps")
            print(f"[GENERATE_SUBSTEP] Forcing step completion to avoid infinite loop")
            state['last_validation'] = {
                "is_completed": True,
                "confidence": 0.5,
                "reason": "Page state not changing despite multiple attempts, assuming completed or impossible",
                "evidence": "No DOM changes detected in last 3 context extractions"
            }
            return state
        
        try:
            # Generate substep plan với LLM
            substep_plan = await self.llm_generator.generate_substep_plan(
                step=current_step,
                context=state['page_context'],
                substep_index=substep_index,
                page_stuck=state.get('page_stuck_detected', False)
            )
            
            # NEW: Check for duplicate plans (same action/target as recent substeps)
            if self._is_duplicate_plan(substep_plan, state.get('substep_plans', [])):
                print(f"[GENERATE_SUBSTEP] Duplicate plan detected, forcing step completion")
                # Force move to next step instead of looping
                state['last_validation'] = {
                    "is_completed": True,
                    "confidence": 0.6,
                    "reason": "Duplicate substep plan detected, assuming already completed",
                    "evidence": "Same action generated multiple times"
                }
                return state
            
            # Safety check: Prevent destructive actions unless goal requires it
            step_goal = current_step.get('action', '').lower()
            action_desc = substep_plan.get('substep_description', '').lower()
            
            # Detect destructive actions
            destructive_keywords = ['logout', 'log out', 'sign out', 'signout', 'đăng xuất']
            is_destructive = any(keyword in action_desc for keyword in destructive_keywords)
            goal_requires_logout = any(keyword in step_goal for keyword in destructive_keywords)
            
            if is_destructive and not goal_requires_logout:
                print(f"[GENERATE_SUBSTEP] WARNING: LLM generated destructive action '{action_desc}' but goal doesn't require it")
                print(f"[GENERATE_SUBSTEP] Marking step as complete instead")
                substep_plan['is_final_substep'] = True
                substep_plan['action_type'] = 'verify'
                substep_plan['substep_description'] = f"Verify goal achieved: {current_step.get('expected_result', 'success')}"
            
            print(f"[GENERATE_SUBSTEP] Plan: {substep_plan['substep_description']}")
            print(f"[GENERATE_SUBSTEP] Action: {substep_plan['action_type']}")
            print(f"[GENERATE_SUBSTEP] Is final: {substep_plan.get('is_final_substep', False)}")
            
            # Save substep to database
            substep = self.repository.create_substep(
                step_id=current_step['step_id'],
                sub_step_order=substep_index + 1,
                sub_step_content=substep_plan['substep_description'],
                expected_result=substep_plan['verification'].get('expected', '')
            )
            
            # Add plan to state
            state['substep_plans'].append(substep_plan)
            
            # Generate Playwright script
            script_content = await self.llm_generator.generate_playwright_script(
                substep_plan=substep_plan,
                substep_id=substep.sub_step_id
            )
            
            # Save script to database
            generated_script = self.repository.create_generated_script(
                sub_step_id=substep.sub_step_id,
                script_content=script_content
            )
            
            state['generated_scripts'].append(generated_script.generated_script_id)
            state['current_substep_id'] = substep.sub_step_id  # Track current substep ID
            
            print(f"[GENERATE_SUBSTEP] Created substep_id={substep.sub_step_id}, script_id={generated_script.generated_script_id}")
            
            return state
            
        except Exception as e:
            print(f"[GENERATE_SUBSTEP] Error: {e}")
            state['error_message'] = f"Substep generation failed: {str(e)}"
            state['overall_status'] = 'error'
            return state
    
    async def execute_substep(self, state: AutoTestState) -> AutoTestState:
        """
        Node 5: Execute substep script
        """
        # Check if workflow is already completed or in error state
        if state.get('overall_status') in ['completed', 'error']:
            print(f"[EXECUTE] Workflow already finished with status: {state['overall_status']}, skipping execution")
            return state
        
        # Check if we've run out of steps
        if state['current_step_index'] >= len(state['steps']):
            print(f"[EXECUTE] All steps completed, skipping execution")
            state['overall_status'] = 'completed'
            return state
        
        print(f"[EXECUTE] Executing substep {state['current_substep_index'] + 1}")
        
        # Check for too many consecutive failures
        if state.get('consecutive_failures', 0) >= 5:
            print(f"[EXECUTE] Too many consecutive failures, stopping")
            state['overall_status'] = 'error'
            state['error_message'] = "Too many consecutive failures (5+), stopping execution"
            return state
        
        try:
            # Get the substep ID from state
            substep_id = state.get('current_substep_id')
            if not substep_id:
                print(f"[EXECUTE] No current_substep_id, likely because step is completed")
                state['overall_status'] = 'completed'
                return state
            
            # Get generated script for this specific substep
            generated_script = self.repository.get_generated_script(substep_id)
            
            if not generated_script:
                raise Exception(f"No generated script for substep {substep_id}")
            
            # Execute the script
            page = state['page']
            script_content = generated_script.script_content
            
            # Create execution environment
            exec_globals = {
                'page': page,
                'datetime': datetime,
                '__builtins__': __builtins__
            }
            
            # Execute the script
            exec(script_content, exec_globals)
            
            # Call the execute function
            func_name = f'execute_substep_{substep_id}'
            if func_name in exec_globals:
                result = await exec_globals[func_name](page)
            else:
                raise Exception(f"Function {func_name} not found in generated script")
            
            print(f"[EXECUTE] Result: {result['success']} - {result['message']}")
            
            # POST-ACTION VERIFICATION: Check if page state changed positively despite failure
            # ENHANCED: Also detect intermediate progress (modal opened, form visible, etc.)
            if not result['success'] and state['substep_plans']:
                # Wait and re-verify
                await page.wait_for_timeout(1000)
                
                # Get current substep plan
                current_plan = state['substep_plans'][-1]
                verification = current_plan.get('verification', {})
                
                print(f"[POST-VERIFY] Re-checking verification after apparent failure")
                
                # Re-check verification conditions
                if verification.get('check_type') == 'url_contains':
                    expected_url = verification.get('expected', '')
                    current_url = page.url
                    if expected_url and expected_url in current_url:
                        print(f"[POST-VERIFY] Action succeeded despite initial failure (URL matches)")
                        result['success'] = True
                        result['message'] += " (verified post-action)"
                
                elif verification.get('check_type') == 'element_visible':
                    selector = verification.get('selector', '')
                    try:
                        await page.wait_for_selector(selector, timeout=2000, state='visible')
                        print(f"[POST-VERIFY] Action succeeded despite initial failure (element visible)")
                        result['success'] = True
                        result['message'] += " (verified post-action)"
                    except:
                        print(f"[POST-VERIFY] Element still not visible")
                
                elif verification.get('check_type') == 'element_not_visible':
                    selector = verification.get('selector', '')
                    try:
                        await page.wait_for_selector(selector, timeout=2000, state='hidden')
                        print(f"[POST-VERIFY] Action succeeded despite initial failure (element hidden)")
                        result['success'] = True
                        result['message'] += " (verified post-action)"
                    except:
                        print(f"[POST-VERIFY] Element still visible")
                
                # NEW: Detect intermediate progress even if final goal not reached
                # This helps LLM understand we're making progress
                if not result['success']:
                    intermediate_progress = await self._detect_intermediate_progress(page, current_plan)
                    if intermediate_progress:
                        print(f"[POST-VERIFY] Intermediate progress detected: {intermediate_progress}")
                        result['intermediate_progress'] = intermediate_progress
                        result['message'] += f" (intermediate: {intermediate_progress})"
            
            # Update consecutive failures counter
            if result['success']:
                state['consecutive_failures'] = 0
            else:
                state['consecutive_failures'] = state.get('consecutive_failures', 0) + 1
            
            # Store current page URL in result for comparison
            result['page_url'] = page.url
            
            # Capture screenshot after execution (both success and failure)
            screenshot_url = None
            try:
                screenshot_bytes = await page.screenshot(type='png', full_page=False)
                status = 'success' if result['success'] else 'error'
                screenshot_filename = f'substep_{substep_id}_{status}.png'
                screenshot_url = await self._upload_screenshot_to_minio(
                    screenshot_bytes=screenshot_bytes,
                    filename=screenshot_filename
                )
                
                if screenshot_url:
                    print(f"[EXECUTE] Screenshot saved: {screenshot_url}")
                    result['screenshot_url'] = screenshot_url
                    
                    # Save screenshot to database
                    self.repository.create_screenshot(
                        generated_script_id=generated_script.generated_script_id,
                        screenshot_link=screenshot_url
                    )
                    
            except Exception as screenshot_error:
                print(f"[EXECUTE] Failed to capture/upload screenshot: {screenshot_error}")
                result['screenshot_url'] = None
            
            # Save test result
            self.repository.create_test_result(
                object_id=substep_id,
                object_type='sub_step',
                result=result['success'],
                reason=result['message']
            )
            
            # Add to execution results
            state['execution_results'].append(result)
            
            return state
            
        except Exception as e:
            print(f"[EXECUTE] Error: {e}")
            import traceback
            traceback.print_exc()
            
            # Try to capture error screenshot
            screenshot_url = None
            try:
                page = state.get('page')
                if page:
                    screenshot_bytes = await page.screenshot(type='png', full_page=False)
                    substep_id = state.get('current_substep_id', 'unknown')
                    screenshot_url = await self._upload_screenshot_to_minio(
                        screenshot_bytes=screenshot_bytes,
                        filename=f'substep_{substep_id}_exception.png'
                    )
                    if screenshot_url:
                        print(f"[EXECUTE] Exception screenshot saved: {screenshot_url}")
            except Exception as screenshot_error:
                print(f"[EXECUTE] Failed to capture exception screenshot: {screenshot_error}")
            
            # Record failure
            result = {
                'success': False,
                'screenshot_url': screenshot_url,
                'message': f"Execution failed: {str(e)}",
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }
            state['execution_results'].append(result)
            
            return state
    
    async def validate_step(self, state: AutoTestState) -> AutoTestState:
        """
        Node: Validate step/substep completion using LLM
        Đánh giá xem step hiện tại đã hoàn thành chưa dựa trên HTML/DOM và expected result
        """
        # Check if workflow is already completed or in error state
        if state.get('overall_status') in ['completed', 'error']:
            print(f"[VALIDATE] Workflow finished, skipping validation")
            return state
        
        # Check if we've run out of steps
        if state['current_step_index'] >= len(state['steps']):
            print(f"[VALIDATE] All steps completed, skipping validation")
            return state
        
        current_step_idx = state['current_step_index']
        current_step = state['steps'][current_step_idx]
        
        print(f"[VALIDATE] Validating step {current_step_idx + 1}/{len(state['steps'])}")
        
        try:
            page = state['page']
            
            # Get current page state
            current_url = page.url
            page_html = await page.content()
            
            # NEW: Track page state for stuck detection
            current_html_hash = hash(page_html)
            
            # Initialize page_state_history if not exists
            if 'page_state_history' not in state:
                state['page_state_history'] = []
            
            # Check if page state is stuck (unchanged)
            if state.get('page_state_history'):
                last_state = state['page_state_history'][-1]
                if (last_state.get('url') == current_url and 
                    last_state.get('html_hash') == current_html_hash):
                    print(f"[VALIDATE] Page state unchanged from last validation")
                    state['consecutive_no_change'] = state.get('consecutive_no_change', 0) + 1
                    
                    # Force move after 3 consecutive no-change validations
                    if state['consecutive_no_change'] >= 3:
                        print(f"[VALIDATE] Stuck detected (3 no-change), forcing completion")
                        state['last_validation'] = {
                            "is_completed": True,
                            "confidence": 0.5,
                            "reason": "Page state unchanged after 3 attempts, assuming already completed or stuck",
                            "evidence": "No DOM/URL changes detected"
                        }
                        # Store state and return early
                        state['page_state_history'].append({
                            'url': current_url,
                            'html_hash': current_html_hash,
                            'timestamp': datetime.now().isoformat()
                        })
                        return state
                else:
                    # Page changed, reset counter
                    state['consecutive_no_change'] = 0
            
            # Store current page state
            state['page_state_history'].append({
                'url': current_url,
                'html_hash': current_html_hash,
                'timestamp': datetime.now().isoformat()
            })
            
            # Clean HTML (remove scripts, styles for better LLM analysis)
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(page_html, 'html.parser')
            
            # Remove script and style tags
            for tag in soup(['script', 'style', 'noscript']):
                tag.decompose()
            
            # Get cleaned HTML
            cleaned_html = soup.get_text(separator='\n', strip=True)
            
            # Get last substep description if available
            substep_description = None
            if state['substep_plans']:
                substep_description = state['substep_plans'][-1].get('substep_description')
            
            # Call LLM to validate
            validation_result = await self.llm_generator.validate_step_completion(
                step_action=current_step.get('action', ''),
                expected_result=current_step.get('expected_result', ''),
                page_html=cleaned_html,
                current_url=current_url,
                substep_description=substep_description
            )
            
            print(f"[VALIDATE] LLM Result: {validation_result['is_completed']}")
            print(f"[VALIDATE] Confidence: {validation_result['confidence']}")
            print(f"[VALIDATE] Reason: {validation_result['reason']}")
            
            # Store validation result in state
            state['last_validation'] = validation_result
            
            # Update execution result if we have one
            if state['execution_results']:
                # Override the success flag based on LLM validation
                # Only override if confidence is high enough
                if validation_result['confidence'] >= 0.7:
                    state['execution_results'][-1]['llm_validated'] = validation_result['is_completed']
                    state['execution_results'][-1]['validation_reason'] = validation_result['reason']
                    
                    # If LLM says completed but execution said failed, override to success
                    if validation_result['is_completed'] and not state['execution_results'][-1]['success']:
                        print(f"[VALIDATE] LLM override: Marking as success despite execution failure")
                        state['execution_results'][-1]['success'] = True
                        state['execution_results'][-1]['message'] += f" (LLM validated: {validation_result['reason']})"
                        state['consecutive_failures'] = 0
            
            return state
            
        except Exception as e:
            print(f"[VALIDATE] Error: {e}")
            # Don't fail the workflow, just log the error
            state['last_validation'] = {
                "is_completed": False,
                "confidence": 0.0,
                "reason": f"Validation error: {str(e)}",
                "evidence": "N/A"
            }
            return state
    
    async def cleanup(self, state: AutoTestState) -> AutoTestState:
        """
        Node Final: Cleanup và đóng browser
        """
        print(f"[CLEANUP] Closing browser and cleaning up")
        
        try:
            if state.get('page'):
                await state['page'].close()
            if self.browser:
                await self.browser.close()
            if self.playwright_context:
                await self.playwright_context.stop()
            
            state['end_time'] = datetime.now().isoformat()
            
            # IMPROVED: Determine overall status based on completed steps, not just substep results
            if state['overall_status'] == 'error':
                # Keep error status
                pass
            elif state['overall_status'] == 'completed':
                # All steps completed successfully
                total_steps = len(state['steps'])
                completed_steps = len(state['completed_steps'])
                
                # Check if all steps are completed
                if completed_steps >= total_steps:
                    state['overall_status'] = 'passed'
                else:
                    state['overall_status'] = 'failed'
            else:
                # Incomplete workflow
                state['overall_status'] = 'failed'
            
            # Add summary statistics
            total_substeps = len(state['execution_results'])
            passed_substeps = sum(1 for r in state['execution_results'] if r.get('success', False))
            
            print(f"[CLEANUP] Final status: {state['overall_status']}")
            print(f"[CLEANUP] Completed {len(state['completed_steps'])}/{len(state['steps'])} steps")
            print(f"[CLEANUP] Substeps: {passed_substeps}/{total_substeps} passed")
            
        except Exception as e:
            print(f"[CLEANUP] Error: {e}")
        
        return state
