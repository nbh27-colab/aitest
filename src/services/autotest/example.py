"""
Example script để test autotest workflow
"""

import asyncio
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.services.autotest.workflow import AutoTestWorkflow
from config.settings import Settings


async def test_autotest_workflow():
    """
    Test autotest workflow với một test case
    
    Prerequisites:
    1. Database có test_case_id=1
    2. Database có login_info_id=1
    3. OPENAI_API_KEY được set trong environment
    """
    
    # Setup database
    settings = Settings()
    engine = create_engine(settings.DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    
    try:
        # Get OpenAI API key
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            print("Error: OPENAI_API_KEY not found in environment")
            print("Please set it: export OPENAI_API_KEY='sk-...'")
            return
        
        print("=== AutoTest Example ===")
        print(f"Using OpenAI API key: {api_key[:10]}...")
        
        # Create workflow
        workflow = AutoTestWorkflow(
            db_session=db,
            minio_client=None,  # Not using MinIO for now
            openai_api_key=api_key
        )
        
        # Run autotest
        # TODO: Replace with actual test_case_id and login_info_id
        test_case_id = 1
        login_info_id = 1
        
        print(f"\nRunning autotest for test_case_id={test_case_id}, login_info_id={login_info_id}")
        
        result = await workflow.run(
            test_case_id=test_case_id,
            login_info_id=login_info_id
        )
        
        # Print results
        print("\n=== Results ===")
        print(f"Status: {result['status']}")
        print(f"Total Steps: {result['total_steps']}")
        print(f"Total SubSteps: {result['total_substeps']}")
        print(f"Passed: {result['passed_substeps']}")
        print(f"Failed: {result['failed_substeps']}")
        
        if result.get('error_message'):
            print(f"\nError: {result['error_message']}")
        
        print(f"\nGenerated Scripts: {result['generated_scripts']}")
        
        # Print execution results
        print("\n=== Execution Details ===")
        for i, exec_result in enumerate(result.get('execution_results', []), 1):
            status = "✓" if exec_result.get('success') else "✗"
            print(f"{status} SubStep {i}: {exec_result.get('message')}")
            if exec_result.get('error'):
                print(f"  Error: {exec_result.get('error')}")
        
    except Exception as e:
        print(f"\nError running autotest: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        db.close()


if __name__ == "__main__":
    # Run the test
    asyncio.run(test_autotest_workflow())
