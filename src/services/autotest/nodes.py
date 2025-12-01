"""
LangGraph workflow nodes cho autotest
"""

import asyncio
import os
import sys
import tempfile
import threading
from typing import Dict, Any, Optional
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright
from concurrent.futures import ThreadPoolExecutor
import queue

# Fix for Windows Playwright subprocess issue
if sys.platform == 'win32':
    # Use nest_asyncio to allow nested event loops on Windows
    try:
        import nest_asyncio
        nest_asyncio.apply()
        print("[PLAYWRIGHT_FIX] Applied nest_asyncio for Windows compatibility")
    except ImportError:
        print("[PLAYWRIGHT_FIX] Warning: nest_asyncio not installed. Install with: pip install nest-asyncio")

from .states import AutoTestState
from .repository import AutoTestRepository
from .page_context import get_page_context
from .llm_generator import LLMGenerator
from config.settings import LLMSettings


class PlaywrightThreadWrapper:
    """
    Wrapper to run sync Playwright in a thread pool executor
    This allows calling sync Playwright from async FastAPI context
    """
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        self._playwright_ctx = None
    
    async def start(self):
        """Start Playwright browser in thread executor"""
        loop = asyncio.get_event_loop()
        
        def _start_browser():
            # This runs in a separate thread with its own event loop
            if sys.platform == 'win32':
                # Create ProactorEventLoop for this thread
                thread_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(thread_loop)
            
            self._playwright_ctx = sync_playwright().start()
            self.playwright = self._playwright_ctx
            self.browser = self.playwright.chromium.launch(
                headless=False,
                slow_mo=500
            )
            self.context = self.browser.new_context(
                viewport={'width': 1080, 'height': 720}
            )
            self.page = self.context.new_page()
            return self.page
        
        # Run in thread pool
        self.page = await loop.run_in_executor(self.executor, _start_browser)
        
        # Wrap page to provide async interface
        wrapped_page = AsyncPageWrapper(self.page, self.executor)
        return wrapped_page
    
    async def stop(self):
        """Stop Playwright and close browser"""
        loop = asyncio.get_event_loop()
        
        def _stop_browser():
            if self.browser:
                self.browser.close()
            if self._playwright_ctx:
                self._playwright_ctx.stop()
        
        await loop.run_in_executor(self.executor, _stop_browser)
        self.executor.shutdown(wait=True)


class AsyncPageWrapper:
    """
    Wraps sync Playwright Page to provide async interface via executor
    All page methods are automatically executed in thread pool
    """
    def __init__(self, sync_page, executor):
        self._page = sync_page
        self._executor = executor
        self._loop = asyncio.get_event_loop()
    
    def __getattr__(self, name):
        """Intercept all attribute access and wrap methods"""
        attr = getattr(self._page, name)
        
        # Special handling for locator - return wrapped locator
        if name == 'locator':
            def locator_wrapper(*args, **kwargs):
                sync_locator = attr(*args, **kwargs)
                return AsyncLocatorWrapper(sync_locator, self._executor)
            return locator_wrapper
        
        # If it's a method, wrap it to run in executor
        if callable(attr):
            async def async_wrapper(*args, **kwargs):
                return await self._loop.run_in_executor(
                    self._executor,
                    lambda: attr(*args, **kwargs)
                )
            return async_wrapper
        else:
            # For properties, return directly
            return attr
    
    @property
    def url(self):
        """Direct property access"""
        return self._page.url


class AsyncLocatorWrapper:
    """Wraps Playwright Locator to provide async interface"""
    def __init__(self, sync_locator, executor):
        self._locator = sync_locator
        self._executor = executor
        self._loop = asyncio.get_event_loop()
    
    def __getattr__(self, name):
        """Intercept all attribute access"""
        attr = getattr(self._locator, name)
        
        # Special handling for first/last/nth which return locators
        if name in ['first', 'last', 'nth']:
            if callable(attr):
                def wrapper(*args, **kwargs):
                    sync_result = attr(*args, **kwargs)
                    return AsyncLocatorWrapper(sync_result, self._executor)
                return wrapper
            else:
                # first/last as properties
                return AsyncLocatorWrapper(attr, self._executor)
        
        # Wrap methods
        if callable(attr):
            async def async_wrapper(*args, **kwargs):
                return await self._loop.run_in_executor(
                    self._executor,
                    lambda: attr(*args, **kwargs)
                )
            return async_wrapper
        else:
            return attr


class AutoTestNodes:
    """Các nodes trong LangGraph workflow"""
    
    def __init__(self, db_session, minio_client, llm_settings: LLMSettings = None):
        self.repository = AutoTestRepository(db_session)
        self.minio_client = minio_client
        self.llm_generator = LLMGenerator(llm_settings=llm_settings)
        self.playwright_context = None
        self.browser = None
        self.playwright_wrapper = None
    
    async def _run_sync_page_method(self, method_name, *args, **kwargs):
        """Helper to run sync page methods in thread executor"""
        if not self.playwright_wrapper or not self.playwright_wrapper.page:
            raise Exception("Playwright not initialized")
        
        loop = asyncio.get_event_loop()
        page = self.playwright_wrapper.page
        
        def _call():
            method = getattr(page, method_name)
            return method(*args, **kwargs)
        
        return await loop.run_in_executor(self.playwright_wrapper.executor, _call)
    
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
            filename: Name for the screenshot file (will be used as remote filename)
            bucket_name: MinIO bucket name (default: "testcase-bucket")
            
        Returns:
            Public URL of uploaded screenshot or None if failed
        """
        try:
            # Create folder path
            remote_folder = f"screenshots/{datetime.now().strftime('%Y-%m-%d')}"
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_filename = f"{timestamp}_{filename}"
            remote_file_path = f"{remote_folder}/{safe_filename}"
            
            # Upload directly using bytes (no temp file needed)
            self.minio_client.upload_file(
                bucket_name=bucket_name,
                data=screenshot_bytes,
                remote_file_path=remote_file_path
            )
            
            public_url = self.minio_client.get_file_public_url(bucket_name, remote_file_path)
            print(f"[MINIO] Screenshot uploaded: {public_url}")
            return public_url
                    
        except Exception as e:
            print(f"[MINIO] Failed to upload screenshot: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _is_duplicate_plan(self, new_plan: Dict[str, Any], recent_plans: list, window: int = 3) -> bool:
        """
        Check if new plan is duplicate of recent plans
        
        ENHANCED: Now considers if plan is duplicate AND page state hasn't changed
        Also tracks syntax errors to prevent regenerating broken code
        
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
        syntax_error_count = 0
        
        for plan in recent:
            plan_action = plan.get('action_type', '')
            plan_selector = plan.get('target_element', {}).get('primary_selector', '')
            plan_desc = plan.get('substep_description', '')
            
            # Track if this plan resulted in syntax error
            if plan.get('syntax_error'):
                syntax_error_count += 1
            
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
        
        # If multiple syntax errors in recent history, stop generating more
        if syntax_error_count >= 2:
            print(f"[DUPLICATE_CHECK] Multiple syntax errors detected ({syntax_error_count}), stopping generation")
            return True
        
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
            
            # Initialize Playwright wrapper (async)
            print("[INITIALIZE] Starting Playwright browser...")
            playwright_wrapper = PlaywrightThreadWrapper()
            page = await playwright_wrapper.start()
            print("[INITIALIZE] Playwright started successfully")
            
            # Store wrapper for cleanup
            self.playwright_wrapper = playwright_wrapper
            
            # Update state
            state['test_case'] = self.repository.model_to_dict(test_case)
            state['steps'] = [self.repository.model_to_dict(s) for s in steps]
            state['login_info'] = self.repository.model_to_dict(login_info)
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
    
    async def _execute_click_action(self, page, action_name: str, selector: str, fallback_selectors: list, wait_for_nav: bool = False) -> dict:
        """Generic click action executor with fallback support"""
        try:
            await page.wait_for_selector(selector, timeout=5000, state='visible')
            await page.click(selector)
            print(f"[AUTO_LOGIN] ✓ {action_name}: {selector}")
            
            if wait_for_nav:
                await page.wait_for_load_state('networkidle', timeout=10000)
            else:
                await page.wait_for_load_state('domcontentloaded', timeout=10000)
            await page.wait_for_timeout(2000)
            
            return {'success': True, 'selector': selector, 'method': 'primary'}
        except Exception as e:
            print(f"[AUTO_LOGIN] ✗ Failed {action_name} (primary): {e}")
            
            # Try fallback selectors
            for fallback in fallback_selectors:
                try:
                    await page.wait_for_selector(fallback, timeout=3000, state='visible')
                    await page.click(fallback)
                    print(f"[AUTO_LOGIN] ✓ {action_name} (fallback): {fallback}")
                    
                    if wait_for_nav:
                        await page.wait_for_load_state('networkidle', timeout=10000)
                    else:
                        await page.wait_for_load_state('domcontentloaded', timeout=10000)
                    await page.wait_for_timeout(2000)
                    
                    return {'success': True, 'selector': fallback, 'method': 'fallback'}
                except:
                    continue
            
            return {'success': False, 'error': str(e), 'selector': selector}

    async def _execute_fill_action(self, page, action_name: str, selector: str, value: str, fallback_selectors: list) -> dict:
        """Generic fill action executor with fallback support"""
        try:
            await page.wait_for_selector(selector, timeout=5000, state='visible')
            await page.fill(selector, value)
            print(f"[AUTO_LOGIN] ✓ {action_name}: {selector}")
            await page.wait_for_timeout(1000)
            
            return {'success': True, 'selector': selector, 'method': 'primary'}
        except Exception as e:
            print(f"[AUTO_LOGIN] ✗ Failed {action_name} (primary): {e}")
            
            # Try fallback selectors
            for fallback in fallback_selectors:
                try:
                    await page.wait_for_selector(fallback, timeout=3000, state='visible')
                    await page.fill(fallback, value)
                    print(f"[AUTO_LOGIN] ✓ {action_name} (fallback): {fallback}")
                    await page.wait_for_timeout(1000)
                    
                    return {'success': True, 'selector': fallback, 'method': 'fallback'}
                except:
                    continue
            
            return {'success': False, 'error': str(e), 'selector': selector}

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
            # Check if initialization was successful
            if state.get('overall_status') == 'error':
                print(f"[AUTO_LOGIN] Skipping due to initialization error: {state.get('error_message')}")
                return state
            
            page = state.get('page')
            login_info = state.get('login_info')
            
            # Validate required data
            if not page:
                raise Exception("Browser page not initialized. Initialize node may have failed.")
            if not login_info:
                raise Exception("Login info not found. Initialize node may have failed.")
            
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
                'max_attempts': 15,  # Increased to handle post-submit dialogs like "Stay signed in?"
                'executed_actions': []  # Track all actions for creating substeps
            }
            
            # Login workflow loop - handle multi-step login including post-submit dialogs
            while login_state['attempts'] < login_state['max_attempts']:
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
                
                action_type = login_action['action_type']
                
                # Handle actions that don't need target element
                if action_type == 'wait_for_redirect':
                    print(f"[AUTO_LOGIN] ⏳ Waiting for OAuth/SSO redirect...")
                    await page.wait_for_load_state('networkidle', timeout=15000)
                    await page.wait_for_timeout(2000)
                    login_state['current_url'] = page.url
                    continue
                
                if action_type == 'completed':
                    print(f"[AUTO_LOGIN] ✓ Login detected as completed")
                    login_state['submitted'] = True
                    break
                
                if action_type == 'error':
                    print(f"[AUTO_LOGIN] ✗ Error detected: {login_action['reason']}")
                    raise Exception(login_action['reason'])
                
                # Handle actions that need target element
                target = login_action.get('target')
                if not target:
                    print(f"[AUTO_LOGIN] ⚠ No target provided for action: {action_type}")
                    continue
                
                selector = target.get('primary_selector', '')
                fallback_selectors = target.get('fallback_selectors', [])
                
                # Execute based on action category
                result = None
                description = ""
                
                if action_type in ['click_login_button', 'click_next']:
                    # Click actions
                    action_name = "Clicked login button" if action_type == 'click_login_button' else "Clicked next button"
                    result = await self._execute_click_action(page, action_name, selector, fallback_selectors, wait_for_nav=False)
                    description = action_name
                
                elif action_type == 'click_submit':
                    # Submit click - wait for navigation
                    result = await self._execute_click_action(page, "Clicked submit button", selector, fallback_selectors, wait_for_nav=True)
                    description = "Click login/submit button"
                    if result['success']:
                        login_state['submitted'] = True
                        # Don't break here - continue to handle post-submit dialogs
                
                elif action_type == 'enter_email':
                    # Fill email
                    result = await self._execute_fill_action(page, "Filled email", selector, login_info['email'], fallback_selectors)
                    description = f"Fill email field with '{login_info['email']}'"
                    if result['success']:
                        login_state['email_entered'] = True
                
                elif action_type == 'enter_password':
                    # Fill password
                    result = await self._execute_fill_action(page, "Filled password", selector, login_info['password'], fallback_selectors)
                    description = "Fill password field"
                    if result['success']:
                        login_state['password_entered'] = True
                
                else:
                    print(f"[AUTO_LOGIN] ⚠ Unknown action type: {action_type}")
                    continue
                
                # Track the executed action
                if result:
                    login_state['executed_actions'].append({
                        'action_type': action_type,
                        'selector': result['selector'],
                        'success': result['success'],
                        'description': description,
                        'error': result.get('error')
                    })
                
                # Update current URL
                login_state['current_url'] = page.url
            
            # Verify login success - wait for potential redirects
            print(f"[AUTO_LOGIN] ⏳ Waiting for post-login redirects...")
            await page.wait_for_timeout(3000)
            
            # Wait for network to be idle (all redirects completed)
            try:
                await page.wait_for_load_state('networkidle', timeout=10000)
            except:
                pass  # Continue even if timeout
            
            await page.wait_for_timeout(2000)
            
            # Take screenshot and upload to MinIO
            screenshot_bytes = await page.screenshot(type='png', full_page=False)
            screenshot_url = await self._upload_screenshot_to_minio(
                screenshot_bytes=screenshot_bytes,
                filename='login_success.png'
            )
            
            print(f"[AUTO_LOGIN] Final URL after login: {page.url}")
            
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
                        try:
                            login_substep = self.repository.create_substep(
                                step_id=first_step['step_id'],
                                sub_step_order=substep_order,
                                sub_step_content=action['description'],
                                expected_result=f"Successfully {action['action_type'].replace('_', ' ')}"
                            )
                            if not login_substep:
                                print(f"[AUTO_LOGIN] Warning: Failed to create login substep {substep_order} (DB error)")
                                continue
                            print(f"[AUTO_LOGIN] Created login substep {substep_order}: substep_id={login_substep.sub_step_id}")
                        except Exception as e:
                            print(f"[AUTO_LOGIN] Error creating login substep {substep_order}: {e}")
                            continue
                        
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
                        try:
                            login_script = self.repository.create_generated_script(
                                sub_step_id=login_substep.sub_step_id,
                                script_content=script_content
                            )
                            if login_script:
                                print(f"[AUTO_LOGIN] Created login script {substep_order}: script_id={login_script.generated_script_id}")
                            else:
                                print(f"[AUTO_LOGIN] Warning: Failed to create login script {substep_order} (DB error)")
                        except Exception as e:
                            print(f"[AUTO_LOGIN] Error creating login script {substep_order}: {e}")
                            login_script = None
                        
                        # Save test result for this action
                        try:
                            result_reason = action.get('error') if not action['success'] else action.get('description', 'Action executed successfully')
                            self.repository.create_test_result(
                                object_id=login_substep.sub_step_id,
                                object_type='sub_step',
                                result=action['success'],
                                reason=result_reason or 'Action executed'
                            )
                        except Exception as e:
                            print(f"[AUTO_LOGIN] Error saving test result for substep {substep_order}: {e}")
                        
                        created_substeps.append({
                            'substep': login_substep,
                            'script': login_script,
                            'action': action
                        })
                        
                        substep_order += 1
                    
                    # Create final validation substep
                    try:
                        validation_substep = self.repository.create_substep(
                            step_id=first_step['step_id'],
                            sub_step_order=substep_order,
                            sub_step_content="Verify login successful",
                            expected_result="User is logged in to the application"
                        )
                        if validation_substep:
                            print(f"[AUTO_LOGIN] Created validation substep {substep_order}: substep_id={validation_substep.sub_step_id}")
                        else:
                            print(f"[AUTO_LOGIN] Warning: Failed to create validation substep (DB error)")
                            validation_substep = None
                    except Exception as e:
                        print(f"[AUTO_LOGIN] Error creating validation substep: {e}")
                        validation_substep = None
                    
                    # Create script for validation (only if substep was created)
                    if validation_substep:
                        validation_script_content = f"""# Auto-generated login validation
# Login attempts: {login_state['attempts']}
# Email entered: {login_state['email_entered']}
# Password entered: {login_state['password_entered']}
# Final URL: {page.url}
# Validation result: {validation['is_logged_in']}
# Validation reason: {validation['reason']}
"""
                        try:
                            validation_script = self.repository.create_generated_script(
                                sub_step_id=validation_substep.sub_step_id,
                                script_content=validation_script_content
                            )
                            if validation_script:
                                print(f"[AUTO_LOGIN] Created validation script {substep_order}: script_id={validation_script.generated_script_id}")
                            else:
                                print(f"[AUTO_LOGIN] Warning: Failed to create validation script (DB error)")
                                validation_script = None
                        except Exception as e:
                            print(f"[AUTO_LOGIN] Error creating validation script: {e}")
                            validation_script = None
                        
                        # Save login screenshot to validation substep
                        if screenshot_url and validation_script:
                            try:
                                self.repository.create_screenshot(
                                    generated_script_id=validation_script.generated_script_id,
                                    screenshot_link=screenshot_url
                                )
                                state['login_screenshot_url'] = screenshot_url
                                print(f"[AUTO_LOGIN] Screenshot saved to database: {screenshot_url}")
                            except Exception as e:
                                print(f"[AUTO_LOGIN] Error saving screenshot: {e}")
                        
                        # Save test result for validation
                        try:
                            self.repository.create_test_result(
                                object_id=validation_substep.sub_step_id,
                                object_type='sub_step',
                                result=validation['is_logged_in'],
                                reason=validation['reason']
                            )
                            print(f"[AUTO_LOGIN] Test result saved: {validation['is_logged_in']}")
                        except Exception as e:
                            print(f"[AUTO_LOGIN] Error saving test result: {e}")
                    
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
            
            # Take error screenshot and upload to MinIO (only if page exists)
            error_screenshot_url = None
            page = state.get('page')
            if page:
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
            else:
                print(f"[AUTO_LOGIN] Cannot capture error screenshot: page not initialized")
            
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
                page_stuck=state.get('page_stuck_detected', False),
                previous_plans=state.get('substep_plans', []),
                last_validation=state.get('last_validation')
            )
            
            # NEW: Check for duplicate plans (same action/target as recent substeps)
            # Instead of forcing completion, we just log it. The LLM should have received history and avoided this.
            # If it still generates duplicate, it might be intentional (retry).
            if self._is_duplicate_plan(substep_plan, state.get('substep_plans', [])):
                print(f"[GENERATE_SUBSTEP] Warning: Duplicate plan detected. LLM might be retrying or stuck.")
            
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
            
            # Save substep to database with error handling
            try:
                substep = self.repository.create_substep(
                    step_id=current_step['step_id'],
                    sub_step_order=substep_index + 1,
                    sub_step_content=substep_plan['substep_description'],
                    expected_result=substep_plan['verification'].get('expected', '')
                )
                
                if not substep:
                    raise Exception("Failed to create substep in database (returned None)")
                    
            except Exception as e:
                print(f"[GENERATE_SUBSTEP] Error creating substep in DB: {e}")
                # Continue without DB - create a mock substep for execution
                from types import SimpleNamespace
                substep = SimpleNamespace(
                    sub_step_id=f"mock_{state['current_step_index']}_{substep_index}",
                    sub_step_order=substep_index + 1,
                    sub_step_content=substep_plan['substep_description']
                )
                print(f"[GENERATE_SUBSTEP] Using mock substep ID for execution: {substep.sub_step_id}")
            
            # Add plan to state
            state['substep_plans'].append(substep_plan)
            
            # Generate Playwright script
            script_content = await self.llm_generator.generate_playwright_script(
                substep_plan=substep_plan,
                substep_id=substep.sub_step_id
            )
            
            # Save script to database with error handling
            try:
                generated_script = self.repository.create_generated_script(
                    sub_step_id=substep.sub_step_id,
                    script_content=script_content
                )
                
                if not generated_script:
                    raise Exception("Failed to create script in database (returned None)")
                    
                state['generated_scripts'].append(generated_script.generated_script_id)
                print(f"[GENERATE_SUBSTEP] Created substep_id={substep.sub_step_id}, script_id={generated_script.generated_script_id}")
            except Exception as e:
                print(f"[GENERATE_SUBSTEP] Error creating script in DB: {e}")
                # Create mock script for execution
                from types import SimpleNamespace
                generated_script = SimpleNamespace(
                    generated_script_id=f"mock_script_{substep.sub_step_id}",
                    script_content=script_content
                )
                print(f"[GENERATE_SUBSTEP] Using mock script ID: {generated_script.generated_script_id}")
            
            state['current_substep_id'] = substep.sub_step_id  # Track current substep ID
            
            return state
            
        except Exception as e:
            print(f"[GENERATE_SUBSTEP] Error: {e}")
            state['error_message'] = f"Substep generation failed: {str(e)}"
            state['overall_status'] = 'error'
            return state
    
    async def execute_substep(self, state: AutoTestState) -> AutoTestState:
        """
        Node 5: Execute substep script
        
        ENHANCED: Capture screenshot BEFORE execution for visual validation
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
        
        # CAPTURE SCREENSHOT BEFORE EXECUTION (for visual validation)
        before_screenshot_url = None
        try:
            page = state.get('page')
            if page:
                substep_id = state.get('current_substep_id', 'unknown')
                screenshot_bytes = await page.screenshot(type='png', full_page=False)
                before_screenshot_url = await self._upload_screenshot_to_minio(
                    screenshot_bytes=screenshot_bytes,
                    filename=f'substep_{substep_id}_before.png'
                )
                print(f"[EXECUTE] Before-screenshot captured: {before_screenshot_url}")
                # Store in state for validation
                state['before_screenshot_url'] = before_screenshot_url
        except Exception as e:
            print(f"[EXECUTE] Failed to capture before-screenshot: {e}")
        
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
            
            # VALIDATE: Check for syntax errors before executing
            try:
                compile(script_content, f'<substep_{substep_id}>', 'exec')
            except SyntaxError as se:
                error_msg = f"SyntaxError in generated script at line {se.lineno}: {se.msg}"
                if se.text:
                    error_msg += f"\n  Code: {se.text.strip()}"
                print(f"[EXECUTE] {error_msg}")
                
                # Mark this substep plan as having syntax error
                if state.get('substep_plans'):
                    state['substep_plans'][-1]['syntax_error'] = True
                
                # Take error screenshot
                screenshot_bytes = await page.screenshot(type='png', full_page=False)
                screenshot_url = await self._upload_screenshot_to_minio(
                    screenshot_bytes=screenshot_bytes,
                    filename=f'syntax_error_substep_{substep_id}.png'
                )
                print(f"[EXECUTE] Exception screenshot saved: {screenshot_url}")
                
                raise Exception(error_msg)
            
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
            
            # Get screenshots for visual validation
            before_screenshot_url = state.get('before_screenshot_url')
            after_screenshot_url = None
            if state.get('execution_results'):
                after_screenshot_url = state['execution_results'][-1].get('screenshot_url')
            
            # DEBUG: Log screenshot availability
            if before_screenshot_url and after_screenshot_url:
                print(f"[VALIDATE] Screenshots available:")
                print(f"[VALIDATE] Before: {before_screenshot_url}")
                print(f"[VALIDATE] After: {after_screenshot_url}")
            else:
                print(f"[VALIDATE] Screenshots missing: before={bool(before_screenshot_url)}, after={bool(after_screenshot_url)}")
            
            # Call LLM to validate (will use vision if screenshots available)
            validation_result = await self.llm_generator.validate_step_completion(
                step_action=current_step.get('action', ''),
                expected_result=current_step.get('expected_result', ''),
                page_html=cleaned_html,
                current_url=current_url,
                substep_description=substep_description,
                before_screenshot_url=before_screenshot_url,
                after_screenshot_url=after_screenshot_url
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
            # Stop Playwright wrapper (async)
            if hasattr(self, 'playwright_wrapper') and self.playwright_wrapper:
                await self.playwright_wrapper.stop()
                print(f"[CLEANUP] Playwright wrapper stopped")
            
            # Fallback for async playwright (if used on non-Windows)
            if hasattr(self, 'browser') and self.browser:
                await self.browser.close()
            if hasattr(self, 'playwright_context') and self.playwright_context:
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