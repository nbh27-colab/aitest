"""
LangGraph workflow cho autotest
Sequential execution với context awareness
"""

from typing import Dict, Any, Literal
from langgraph.graph import StateGraph, END
from sqlalchemy.orm import Session
from config.settings import LLMSettings

from .states import AutoTestState
from .nodes import AutoTestNodes


class AutoTestWorkflow:
    """
    Main workflow để execute autotest với LangGraph
    Sequential execution với context awareness
    """
    
    def __init__(self, db_session: Session, minio_client, llm_settings: LLMSettings):
        self.db_session = db_session
        self.nodes = AutoTestNodes(db_session, minio_client, llm_settings)
        self.graph = self._build_graph()
    
    def _move_to_next_step(self, state: AutoTestState) -> AutoTestState:
        """
        Helper node: Move to next step and update state
        This is separated from decision logic to avoid state mutation in conditional edges
        """
        current_step_idx = state['current_step_index']
        
        print(f"[MOVE_TO_NEXT_STEP] Current step: {current_step_idx}, Completed: {state.get('completed_steps', [])}")
        
        # Mark current step as completed
        if current_step_idx not in state['completed_steps']:
            state['completed_steps'].append(current_step_idx)
            print(f"[MOVE_TO_NEXT_STEP] Marked step {current_step_idx} as completed")
        
        # Move to next step and reset substep state
        state['current_step_index'] = current_step_idx + 1
        state['current_substep_index'] = 0
        state['substep_plans'] = []
        state['consecutive_failures'] = 0
        state['current_substep_id'] = None  # CRITICAL: Reset substep ID
        
    
        # Skip any already completed steps
        while (state['current_step_index'] < len(state['steps']) and 
               state['current_step_index'] in state['completed_steps']):
            print(f"[MOVE_TO_NEXT_STEP] Skipping already completed step {state['current_step_index']}")
            state['current_step_index'] += 1
        
        # Check if we have more steps
        if state['current_step_index'] >= len(state['steps']):
            print(f"[MOVE_TO_NEXT_STEP] All steps completed")
            state['overall_status'] = 'completed'
        else:
            print(f"[MOVE_TO_NEXT_STEP] Moving to step {state['current_step_index']} ({state['current_step_index'] + 1}/{len(state['steps'])})")
        
        return state
    
    def _continue_substeps(self, state: AutoTestState) -> AutoTestState:
        """
        Helper node: Increment substep index
        """
        # Safety check: don't increment if workflow is done
        if state.get('overall_status') in ['completed', 'error']:
            print(f"[CONTINUE_SUBSTEPS] Workflow finished, not incrementing substep")
            return state
        
        if state['current_step_index'] >= len(state['steps']):
            print(f"[CONTINUE_SUBSTEPS] All steps completed, not incrementing substep")
            state['overall_status'] = 'completed'
            return state
        
        state['current_substep_index'] += 1
        print(f"[CONTINUE_SUBSTEPS] Moving to substep {state['current_substep_index']}")
        return state
    
    def _build_graph(self) -> StateGraph:
        """Xây dựng LangGraph workflow"""
        
        # Create graph
        workflow = StateGraph(AutoTestState)
        
        # Add nodes
        workflow.add_node("initialize", self.nodes.initialize)
        workflow.add_node("auto_login", self.nodes.auto_login)
        workflow.add_node("move_to_next_step", self._move_to_next_step)
        workflow.add_node("continue_substeps", self._continue_substeps)
        workflow.add_node("get_context", self.nodes.get_current_context)
        workflow.add_node("generate_substep", self.nodes.generate_next_substep)
        workflow.add_node("execute_substep", self.nodes.execute_substep)
        workflow.add_node("validate_step", self.nodes.validate_step)
        workflow.add_node("cleanup", self.nodes.cleanup)
        
        # Set entry point
        workflow.set_entry_point("initialize")
        
        # Add edges
        workflow.add_edge("initialize", "auto_login")
        workflow.add_edge("auto_login", "get_context")
        workflow.add_edge("get_context", "generate_substep")
        workflow.add_edge("generate_substep", "execute_substep")
        workflow.add_edge("execute_substep", "validate_step")
        
        # Conditional edge after validate_step
        workflow.add_conditional_edges(
            "validate_step",
            self._decide_next_action,
            {
                "continue_substeps": "continue_substeps",
                "next_step": "move_to_next_step",
                "finish": "cleanup"
            }
        )
        
        # After moving to next step or continuing substeps, go back to get_context
        workflow.add_edge("move_to_next_step", "get_context")
        workflow.add_edge("continue_substeps", "get_context")
        
        # End after cleanup
        workflow.add_edge("cleanup", END)
        
        # Compile with higher recursion limit
        return workflow.compile(
            checkpointer=None,
            interrupt_before=None,
            interrupt_after=None,
            debug=False
        )
    
    def _decide_next_action(self, state: AutoTestState) -> Literal["continue_substeps", "next_step", "finish"]:
        """
        Quyết định action tiếp theo sau khi validate substep
        Sử dụng kết quả từ LLM validation để quyết định
        
        NOTE: This function should NOT mutate state directly!
        State updates should happen in nodes, not in conditional edges.
        """
        current_step_idx = state['current_step_index']
        
        print(f"[DECISION] Current state: step_idx={current_step_idx}, substep_idx={state['current_substep_index']}, completed={state.get('completed_steps', [])}")
        
        # CRITICAL: Check if all steps completed or error state
        if state.get('overall_status') in ['completed', 'error']:
            print(f"[DECISION] Workflow finished with status: {state['overall_status']}")
            return "finish"
        
        # Check if we've run out of steps
        if current_step_idx >= len(state['steps']):
            print(f"[DECISION] All steps completed (step_idx {current_step_idx} >= {len(state['steps'])})")
            return "finish"
        
        # NEW: Check for max substeps per step (prevent infinite loops)
        MAX_SUBSTEPS_PER_STEP = 10
        if state['current_substep_index'] >= MAX_SUBSTEPS_PER_STEP:
            print(f"[DECISION] Max substeps per step ({MAX_SUBSTEPS_PER_STEP}) reached, forcing next step")
            return "next_step"
        
        # Check for too many failures
        if state.get('consecutive_failures', 0) >= 5:
            print(f"[DECISION] Too many consecutive failures (5+)")
            return "finish"
        
        # CRITICAL: Check if current step is already completed (should never happen)
        if current_step_idx in state.get('completed_steps', []):
            print(f"[DECISION] WARNING: Already on completed step {current_step_idx}!")
            return "finish"
        
        # NEW: Use LLM validation result if available
        validation_result = state.get('last_validation')
        if validation_result and validation_result.get('confidence', 0) >= 0.7:
            is_step_completed = validation_result.get('is_completed', False)
            
            print(f"[DECISION] LLM Validation: completed={is_step_completed}, confidence={validation_result.get('confidence')}")
            print(f"[DECISION] Reason: {validation_result.get('reason', 'N/A')}")
            
            if is_step_completed:
                print(f"[DECISION] Step validated as complete by LLM, moving to next step")
                return "next_step"
            else:
                # Step not completed according to LLM
                # NEW: Check for stuck state (no page changes)
                if state.get('consecutive_no_change', 0) >= 3:
                    print(f"[DECISION] Page stuck (3 no-change), forcing next step")
                    return "next_step"
                
                # Check if we should retry or give up
                if state.get('consecutive_failures', 0) >= 3:
                    print(f"[DECISION] Too many failures ({state['consecutive_failures']}), moving to next step anyway")
                    return "next_step"
                
                # NEW: Low confidence validation + some failures → skip
                if validation_result.get('confidence', 0) < 0.6 and state.get('consecutive_failures', 0) >= 2:
                    print(f"[DECISION] Low confidence ({validation_result.get('confidence')}) + failures, skipping")
                    return "next_step"
                
                print(f"[DECISION] Step not completed, will try next substep")
                return "continue_substeps"
        
        # FALLBACK: Use execution result if LLM validation not available
        last_result = None
        last_plan = None
        
        if state['execution_results']:
            last_result = state['execution_results'][-1]
        
        if state['substep_plans']:
            last_plan = state['substep_plans'][-1]
        
        if last_result:
            execution_success = last_result.get('success', False)
            
            # Case 1: Execution failed
            if not execution_success:
                # Check if we should stop due to too many failures
                if state.get('consecutive_failures', 0) >= 3:
                    print(f"[DECISION] Too many failures ({state['consecutive_failures']}), will move to next step")
                    return "next_step"
                
                # If substep was marked as final but failed, retry instead of moving on
                if last_plan and last_plan.get('is_final_substep', False):
                    print(f"[DECISION] Final substep failed, will retry (attempt {state.get('consecutive_failures', 0) + 1}/3)")
                    return "continue_substeps"
                
                # Regular failure, continue with next substep
                print(f"[DECISION] Substep failed, will try next substep")
                return "continue_substeps"
            
            # Case 2: Execution succeeded
            else:
                # If substep was marked as final AND succeeded, move to next step
                if last_plan and last_plan.get('is_final_substep', False):
                    print(f"[DECISION] Final substep succeeded, will move to next step")
                    return "next_step"
                
                # Success but not final, continue with next substep
                print(f"[DECISION] Substep succeeded, will continue with next substep")
                return "continue_substeps"
        
        # Fallback: continue with next substep
        print(f"[DECISION] No validation/execution result, continuing with next substep")
        return "continue_substeps"
    
    async def run(self, test_case_id: int, login_info_id: int) -> Dict[str, Any]:
        """
        Execute autotest workflow
        
        Args:
            test_case_id: ID của test case cần test
            login_info_id: ID của login info
        
        Returns:
            Dict chứa kết quả execution
        """
        print(f"=== Starting AutoTest Workflow ===")
        print(f"Test Case ID: {test_case_id}")
        print(f"Login Info ID: {login_info_id}")
        
        # Initialize state
        initial_state: AutoTestState = {
            "test_case_id": test_case_id,
            "login_info_id": login_info_id,
            "test_case": None,
            "login_info": None,
            "steps": [],
            "current_step_index": 0,
            "current_substep_index": 0,
            "login_completed": False,
            "completed_steps": [],
            "browser": None,
            "page": None,
            "page_context": None,
            "page_state_history": [],  # NEW: Track page state
            "substep_plans": [],
            "execution_results": [],
            "generated_scripts": [],
            "current_substep_id": None,
            "consecutive_failures": 0,
            "consecutive_no_change": 0,  # NEW: Track stuck state
            "last_validation": None,
            "overall_status": "running",
            "error_message": None,
            "start_time": None,
            "end_time": None
        }
        
        try:
            # Run workflow with higher recursion limit
            config = {"recursion_limit": 100}  # Increase from default 25 to 100
            final_state = await self.graph.ainvoke(initial_state, config)
            
            # Prepare result
            result = {
                "test_case_id": test_case_id,
                "status": final_state['overall_status'],
                "start_time": final_state.get('start_time'),
                "end_time": final_state.get('end_time'),
                "total_steps": len(final_state['steps']),
                "total_substeps": len(final_state['execution_results']),
                "passed_substeps": sum(1 for r in final_state['execution_results'] if r.get('success', False)),
                "failed_substeps": sum(1 for r in final_state['execution_results'] if not r.get('success', False)),
                "generated_scripts": final_state['generated_scripts'],
                "error_message": final_state.get('error_message'),
                "execution_results": final_state['execution_results']
            }
            
            print(f"=== AutoTest Workflow Completed ===")
            print(f"Status: {result['status']}")
            print(f"Total SubSteps: {result['total_substeps']}")
            print(f"Passed: {result['passed_substeps']}, Failed: {result['failed_substeps']}")
            
            return result
            
        except Exception as e:
            print(f"=== AutoTest Workflow Error ===")
            print(f"Error: {e}")
            
            # Cleanup on error
            try:
                await self.nodes.cleanup(initial_state)
            except:
                pass
            
            return {
                "test_case_id": test_case_id,
                "status": "error",
                "error_message": str(e),
                "total_steps": 0,
                "total_substeps": 0,
                "passed_substeps": 0,
                "failed_substeps": 0
            }
