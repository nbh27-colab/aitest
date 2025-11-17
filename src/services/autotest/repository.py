"""
Database operations cho autotest workflow
"""

from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select

from src.models.test_case import TestCase
from src.models.step import Step
from src.models.sub_step import SubStep
from src.models.login_info import LoginInfo
from src.models.generated_script import GeneratedScript
from src.models.screenshot import Screenshot
from src.models.test_result import TestResult


class AutoTestRepository:
    """Database operations for autotest"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_test_case(self, test_case_id: int) -> Optional[TestCase]:
        """Lấy test case by ID"""
        return self.db.query(TestCase).filter(TestCase.test_case_id == test_case_id).first()
    
    def get_steps(self, test_case_id: int) -> List[Step]:
        """Lấy tất cả steps của test case, sorted by step_order"""
        return self.db.query(Step)\
            .filter(Step.test_case_id == test_case_id)\
            .order_by(Step.step_order)\
            .all()
    
    def get_substeps(self, step_id: int) -> List[SubStep]:
        """Lấy tất cả substeps của một step, sorted by sub_step_order"""
        return self.db.query(SubStep)\
            .filter(SubStep.step_id == step_id)\
            .order_by(SubStep.sub_step_order)\
            .all()
    
    def get_login_info(self, login_info_id: int) -> Optional[LoginInfo]:
        """Lấy login info by ID"""
        return self.db.query(LoginInfo).filter(LoginInfo.login_info_id == login_info_id).first()
    
    def create_substep(
        self, 
        step_id: int, 
        sub_step_order: int,
        sub_step_content: str,
        expected_result: str
    ) -> SubStep:
        """Tạo substep mới"""
        substep = SubStep(
            step_id=step_id,
            sub_step_order=sub_step_order,
            sub_step_content=sub_step_content,
            expected_result=expected_result
        )
        self.db.add(substep)
        self.db.commit()
        self.db.refresh(substep)
        return substep
    
    def create_generated_script(
        self,
        sub_step_id: int,
        script_content: str
    ) -> GeneratedScript:
        """Lưu generated Playwright script"""
        # Check if script already exists for this substep
        existing = self.db.query(GeneratedScript)\
            .filter(GeneratedScript.sub_step_id == sub_step_id)\
            .first()
        
        if existing:
            # Update existing script
            existing.script_content = script_content
            self.db.commit()
            self.db.refresh(existing)
            return existing
        else:
            # Create new script
            script = GeneratedScript(
                sub_step_id=sub_step_id,
                script_content=script_content
            )
            self.db.add(script)
            self.db.commit()
            self.db.refresh(script)
            return script
    
    def create_screenshot(
        self,
        generated_script_id: int,
        screenshot_link: str
    ) -> Screenshot:
        """Lưu screenshot"""
        screenshot = Screenshot(
            generated_script_id=generated_script_id,
            screenshot_link=screenshot_link
        )
        self.db.add(screenshot)
        self.db.commit()
        self.db.refresh(screenshot)
        return screenshot
    
    def create_test_result(
        self,
        object_id: int,
        object_type: str,  # 'step' or 'sub_step'
        result: bool,
        reason: str
    ) -> TestResult:
        """Lưu test result"""
        test_result = TestResult(
            object_id=object_id,
            object_type=object_type,
            result=result,
            reason=reason
        )
        self.db.add(test_result)
        self.db.commit()
        self.db.refresh(test_result)
        return test_result
    
    def get_generated_script(self, sub_step_id: int) -> Optional[GeneratedScript]:
        """Lấy generated script cho substep"""
        return self.db.query(GeneratedScript)\
            .filter(GeneratedScript.sub_step_id == sub_step_id)\
            .first()
    
    def model_to_dict(self, model) -> Dict[str, Any]:
        """Convert SQLAlchemy model to dict"""
        if model is None:
            return None
        
        result = {}
        for column in model.__table__.columns:
            value = getattr(model, column.name)
            # Convert datetime to string
            if hasattr(value, 'isoformat'):
                value = value.isoformat()
            result[column.name] = value
        return result
