"""
LangGraph workflow nodes cho autotest
"""

import asyncio
import os
from typing import Dict, Any
from datetime import datetime
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
    
    def _is_duplicate_plan(self, new_plan: Dict[str, Any], recent_plans: list, window: int = 3) -> bool:
        """
        Check if new plan is duplicate of recent plans
        
        Args:
            new_plan: The newly generated substep plan
            recent_plans: List of recent substep plans
            window: Number of recent plans to check (default: 3)
        
        Returns:
            True if duplicate detected, False otherwise
        """
        if len(recent_plans) < window:
            return False
        
        # Get last N plans
        recent = recent_plans[-window:]
        
        new_action = new_plan.get('action_type', '')
        new_selector = new_plan.get('target', {}).get('primary_selector', '')
        new_desc = new_plan.get('substep_description', '')
        
        for plan in recent:
            plan_action = plan.get('action_type', '')
            plan_selector = plan.get('target', {}).get('primary_selector', '')
            plan_desc = plan.get('substep_description', '')
            
            # Check if action + selector match
            if (plan_action == new_action and plan_selector == new_selector):
                print(f"[DUPLICATE_CHECK] Found duplicate: {new_action} on {new_selector}")
                return True
            
            # Also check if description is very similar (fuzzy match)
            if new_desc and plan_desc:
                # Simple similarity: check if 80% of words overlap
                new_words = set(new_desc.lower().split())
                plan_words = set(plan_desc.lower().split())
                if len(new_words) > 0:
                    overlap = len(new_words & plan_words) / len(new_words)
                    if overlap > 0.8:
                        print(f"[DUPLICATE_CHECK] Found similar description: {overlap:.0%} overlap")
                        return True
        
        return False
    
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
                viewport={'width': 1920, 'height': 1080}
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
        Node 2: Tự động đăng nhập
        - Mở web_url
        - Generate và execute login script
        """
        print(f"[AUTO_LOGIN] Starting auto login")
        
        try:
            page = state['page']
            login_info = state['login_info']
            
            # Navigate to web URL
            print(f"[AUTO_LOGIN] Navigating to {login_info['web_url']}")
            await page.goto(login_info['web_url'], wait_until='domcontentloaded')
            await page.wait_for_timeout(2000)
            
            # Simple login script generation
            # TODO: Make this more intelligent with LLM
            print(f"[AUTO_LOGIN] Attempting to fill login form")
            
            # Try common login selectors
            email_selectors = [
                'input[name="email"]',
                'input[type="email"]',
                'input[name="username"]',
                'input[name="accessKey"]',  # MinIO uses accessKey
                '#email',
                '#username',
                '#accessKey'
            ]
            
            password_selectors = [
                'input[name="password"]',
                'input[type="password"]',
                'input[name="secretKey"]',  # MinIO uses secretKey
                '#password',
                '#secretKey'
            ]
            
            submit_selectors = [
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("login")',
                'button:has-text("sign in")',
                '.login-button',
                '#login-button'
            ]
            
            # Fill email
            for selector in email_selectors:
                try:
                    await page.wait_for_selector(selector, timeout=2000, state='visible')
                    await page.fill(selector, login_info['email'])
                    print(f"[AUTO_LOGIN] Filled email with selector: {selector}")
                    break
                except:
                    continue
            
            # Fill password
            for selector in password_selectors:
                try:
                    await page.wait_for_selector(selector, timeout=2000, state='visible')
                    await page.fill(selector, login_info['password'])
                    print(f"[AUTO_LOGIN] Filled password with selector: {selector}")
                    break
                except:
                    continue
            
            # Click submit
            for selector in submit_selectors:
                try:
                    await page.wait_for_selector(selector, timeout=2000, state='visible')
                    await page.click(selector)
                    print(f"[AUTO_LOGIN] Clicked submit with selector: {selector}")
                    break
                except:
                    continue
            
            # Wait for navigation
            await page.wait_for_load_state('networkidle', timeout=10000)
            await page.wait_for_timeout(2000)
            
            # Take screenshot
            screenshot_path = 'login_success.png'
            await page.screenshot(path=screenshot_path)
            
            # Upload to MinIO
            # TODO: Implement MinIO upload
            
            state['login_completed'] = True
            
            # Check if Step 1 is "Login to Minio" or similar
            # If so, mark it as completed to skip it
            if state['steps']:
                first_step = state['steps'][0]
                step_action = first_step.get('action', '').strip().lower()
                step_order = first_step.get('step_order', 1)
                
                print(f"[AUTO_LOGIN] Checking Step {step_order} action: '{step_action}'")
                
                # Check if step is about login (or if it's step 1 with empty action)
                # Step 1 is often the login step even if action field is empty
                is_login_step = (
                    any(keyword in step_action for keyword in ['login', 'đăng nhập', 'sign in', 'log in', 'minio']) or
                    (step_order == 1 and not step_action)  # Empty step 1 is likely login
                )
                
                if is_login_step:
                    print(f"[AUTO_LOGIN] Step {step_order} detected as login step, marking as completed")
                    if 0 not in state['completed_steps']:
                        state['completed_steps'].append(0)
                    state['current_step_index'] = 1  # Skip to step 2 (index 1)
                    print(f"[AUTO_LOGIN] Skipping to Step 2, completed_steps: {state['completed_steps']}")
                else:
                    print(f"[AUTO_LOGIN] Step {step_order} is not a login step, will execute normally")
            
            print(f"[AUTO_LOGIN] Login completed successfully")
            
            return state
            
        except Exception as e:
            print(f"[AUTO_LOGIN] Error: {e}")
            state['error_message'] = f"Login failed: {str(e)}"
            # Continue anyway, maybe login is not required
            state['login_completed'] = True
            return state
    
    async def get_current_context(self, state: AutoTestState) -> AutoTestState:
        """
        Node 3: Lấy context của page hiện tại
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
        
        try:
            # Generate substep plan với LLM
            substep_plan = await self.llm_generator.generate_substep_plan(
                step=current_step,
                context=state['page_context'],
                substep_index=substep_index
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
            if not result['success'] and state['substep_plans']:
                # Wait and re-verify
                await page.wait_for_timeout(1000)
                
                # Get current substep plan
                current_plan = state['substep_plans'][-1]
                verification = current_plan.get('verification', {})
                
                print(f"[POST-VERIFY] Re-checking verification after apparent failure")
                
                # Re-check verification conditions
                if verification.get('type') == 'url_contains':
                    expected_url = verification.get('expected', '')
                    current_url = page.url
                    if expected_url and expected_url in current_url:
                        print(f"[POST-VERIFY] Action succeeded despite initial failure (URL matches)")
                        result['success'] = True
                        result['message'] += " (verified post-action)"
                
                elif verification.get('type') == 'element_visible':
                    selector = verification.get('selector', '')
                    try:
                        await page.wait_for_selector(selector, timeout=2000, state='visible')
                        print(f"[POST-VERIFY] Action succeeded despite initial failure (element visible)")
                        result['success'] = True
                        result['message'] += " (verified post-action)"
                    except:
                        print(f"[POST-VERIFY] Element still not visible")
                
                elif verification.get('type') == 'element_not_visible':
                    selector = verification.get('selector', '')
                    try:
                        await page.wait_for_selector(selector, timeout=2000, state='hidden')
                        print(f"[POST-VERIFY] Action succeeded despite initial failure (element hidden)")
                        result['success'] = True
                        result['message'] += " (verified post-action)"
                    except:
                        print(f"[POST-VERIFY] Element still visible")
            
            # Update consecutive failures counter
            if result['success']:
                state['consecutive_failures'] = 0
            else:
                state['consecutive_failures'] = state.get('consecutive_failures', 0) + 1
            
            # Store current page URL in result for comparison
            result['page_url'] = page.url
            
            # Save screenshot to MinIO
            # TODO: Upload screenshot to MinIO and save link
            screenshot_link = result.get('screenshot_path', '')
            
            if screenshot_link:
                self.repository.create_screenshot(
                    generated_script_id=generated_script.generated_script_id,
                    screenshot_link=screenshot_link
                )
            
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
            
            # Record failure
            result = {
                'success': False,
                'screenshot_path': None,
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
