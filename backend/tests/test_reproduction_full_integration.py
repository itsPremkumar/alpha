"""End-to-end integration testing: Reproduction Engine, Experience Memory, and Critic Verification."""

from pathlib import Path

from alpha.critic import AgentFinishedCritic
from alpha.learning.experience import ExperienceRecord, ExperienceRetriever, ExperienceStore, OutcomeType
from alpha.reproduction import ReproductionEngine, ReproductionStatus


def test_full_reproduction_and_experience_pipeline(tmp_path):
    # 1. Setup mock codebase with a real bug
    service_file = tmp_path / "pricing.py"
    service_file.write_text(
        "def calculate_discount(price: float, discount_pct: float) -> float:\n"
        "    # BUG: Multiplying price directly by discount_pct instead of applying discount\n"
        "    return price * (discount_pct / 100.0)\n"
    )

    # 2. Consult experience memory before solving
    store = ExperienceStore(load_defaults=True)
    retriever = ExperienceRetriever(store=store)
    lessons_prompt = retriever.render_lessons_prompt("pricing calculation bug")

    # 3. Step 1: Synthesize reproduction script & verify failure pre-fix
    engine = ReproductionEngine()
    test_body = f"""import sys
sys.path.insert(0, r'{tmp_path}')
from pricing import calculate_discount

# For price $100 with 20% discount, final price should be $80
final_price = calculate_discount(100.0, 20.0)
assert final_price == 80.0, f"Expected 80.0, got {{final_price}}"
"""

    prep_report = engine.prepare_reproduction(
        issue_description="calculate_discount returns the discount amount instead of discounted total",
        test_body=test_body,
        workspace_dir=tmp_path,
    )
    # Confirm bug is proven to reproduce before any fix
    assert prep_report.status == ReproductionStatus.PRE_FIX_FAILED
    assert prep_report.pre_fix_result.exit_code == 1

    # 4. Step 2: Apply the code fix
    service_file.write_text(
        "def calculate_discount(price: float, discount_pct: float) -> float:\n"
        "    return price * (1.0 - discount_pct / 100.0)\n"
    )

    # 5. Step 3: Verify post-fix passes
    verify_report = engine.verify_solution(
        reproduction_script_path=Path(prep_report.reproduction_script_path),
        workspace_dir=tmp_path,
        pre_fix_result=prep_report.pre_fix_result,
    )
    assert verify_report.is_verified
    assert verify_report.status == ReproductionStatus.VERIFIED_SOLVED
    assert verify_report.post_fix_result.exit_code == 0

    # 6. Step 4: Validate with OpenHands Critic
    history = [
        {"tool_name": "reproduce_and_verify", "phase": "prepare", "status": "success"},
        {"tool_name": "replace_file_content", "path": "pricing.py", "status": "success"},
        {"tool_name": "reproduce_and_verify", "phase": "verify", "status": "success", "exit_code": 0},
    ]
    critic = AgentFinishedCritic(check_unresolved_errors=True)
    critic_res = critic.evaluate("Fix discount calculation bug", execution_history=history)
    assert critic_res.is_approved

    # 7. Step 5: Save successful solution into Experience Memory
    store.record(
        ExperienceRecord(
            task_goal="Fix pricing calculate_discount logic",
            outcome=OutcomeType.SUCCESS,
            lessons_learned=["Discounts must subtract percentage from 1.0, not return absolute discount amount."],
            modified_files=["pricing.py"],
            tags=["pricing", "math", "discount"],
        )
    )

    # Verify that future queries retrieve this newly minted experience
    updated_matches = retriever.retrieve("pricing discount calculation")
    assert any("pricing" in m.task_goal.lower() for m in updated_matches)
