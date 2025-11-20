#-------------------------#
#    State management     #
#-------------------------#

from typing import TypedDict, Optional, List, Dict, Any
# from datetime import datetime

class PageContext(TypedDict):
    """
    Current state of page
    """
    current_url: str
    page_title: str
    main_heading: Optional[str]
    visible_elements: List[Dict[str, Any]]
    dom_snapshot: Dict[str, Any]
    screenshot_base64: str
    console_logs: List[str]
    previous_results: List[Dict[str, Any]]
    timestamp: str

class SubStepPlan(TypedDict):
    """
    plan for a substep
    """
    substep_description: str
    action_type: str # click | fill | select | verify | wait | navigate
    target_element: Dict[str, Any]
    action_value: Optional[str]
    verification: Dict[str, str]
    is_final_substep: bool

class ExecuionResult(TypedDict):
    """
    Results from executing a step
    """
    success: bool
    screenshot_path: str
    message: str
    error: Optional[str]
    timestamp: str
    llm_validated: Optional[bool]
    validation_reason: Optional[str]

class ValidationResult(TypedDict):
    """
    Validation result from LLM
    """
    is_completed: bool
    confidence: float
    reason: str
    envidence: str

class AutoTestState(TypedDict):
    """
    State for autotest walkthroughs
    """
    # input data
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
    completed_steps: List[int]

    # Page state
    browser: Optional[Any]
    page: Optional[Any]
    page_context: Optional[PageContext]
    page_state_history: List[Dict[str, Any]]    # for tracking changes of page state

    # Execution tracking
    substep_plans: List[SubStepPlan]
    execution_results: List[ExecuionResult]
    generated_scripts: List[int]
    current_substep_id: Optional[int]
    consecutive_failures: int   # for preventing infinite loops
    consecutive_no_change: int  # stuck detection
    last_validation: Optional[ValidationResult]

    # Results
    overall_status: str # running | passed | failed
    error_message: Optional[str]

    # Metadata
    start_time: Optional[str]
    end_time: Optional[str]