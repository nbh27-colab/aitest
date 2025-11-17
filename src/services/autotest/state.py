"""
State management for LangGraph workflow
"""

from typing import TypedDict, List, Optional, Dict, Any
from datetime import datetime


class PageContext(TypedDict):
    """Context về trạng thái hiện tại của page"""
    current_url: str
    page_title: str
    main_heading: Optional[str]
    visible_elements: List[Dict[str, Any]]
    dom_snapshot: Dict[str, Any]  # NEW: Full HTML structure
    screenshot_base64: str
    console_logs: List[str]
    previous_results: List[Dict[str, Any]]  # UPDATED: Now includes error details
    timestamp: str


class SubStepPlan(TypedDict):
    """Kế hoạch thực thi cho một substep"""
    substep_description: str
    action_type: str  # click|fill|select|verify|wait|navigate
    target_element: Dict[str, Any]
    action_value: Optional[str]
    verification: Dict[str, str]
    is_final_substep: bool


class ExecutionResult(TypedDict):
    """Kết quả thực thi một substep"""
    success: bool
    screenshot_path: str
    message: str
    error: Optional[str]
    timestamp: str
    llm_validated: Optional[bool]  # NEW: Result from LLM validation
    validation_reason: Optional[str]  # NEW: Reason from LLM


class ValidationResult(TypedDict):
    """Kết quả validation từ LLM"""
    is_completed: bool
    confidence: float
    reason: str
    evidence: str


class AutoTestState(TypedDict):
    """State cho toàn bộ autotest workflow"""
    # Input data
    test_case_id: int
    login_info_id: int
    
    # Test case data
    test_case: Optional[Dict[str, Any]]
    login_info: Optional[Dict[str, Any]]
    steps: List[Dict[str, Any]]
    
    # Execution state
    current_step_index: int
    current_substep_index: int
    login_completed: bool
    completed_steps: List[int]  # Track which step indices are completed
    
    # Page state
    browser: Optional[Any]  # Playwright Browser
    page: Optional[Any]  # Playwright Page
    page_context: Optional[PageContext]
    page_state_history: List[Dict[str, Any]]  # NEW: Track page state changes
    
    # Execution tracking
    substep_plans: List[SubStepPlan]
    execution_results: List[ExecutionResult]
    generated_scripts: List[int]  # List of generated_script_ids
    current_substep_id: Optional[int]  # ID of the substep being executed
    consecutive_failures: int  # Count consecutive failures to prevent infinite loop
    consecutive_no_change: int  # NEW: Count no-change validations (stuck detection)
    last_validation: Optional[ValidationResult]  # NEW: Last LLM validation result
    
    # Results
    overall_status: str  # running|passed|failed|error
    error_message: Optional[str]
    
    # Metadata
    start_time: Optional[str]
    end_time: Optional[str]
