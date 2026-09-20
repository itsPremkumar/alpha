from alpha.security.enclave.goal_pursuit import (
    AstraGoalHarness,
)
from alpha.security.enclave.spatiotemporal import (
    BoundingBox,
    SpatialObject,
    SpatioTemporalCache,
)


def test_spatiotemporal_cache_and_object_retroactive_query():
    cache = SpatioTemporalCache()

    # Ingest Frame 1 with glasses on table
    obj1 = SpatialObject(
        label="glasses",
        bbox=BoundingBox(x=0.2, y=0.3, width=0.1, height=0.05),
    )
    cache.ingest_frame(
        objects=[obj1],
        screen_context="living room desk",
        timestamp=100.0,
    )

    # Ingest Frame 2 with laptop
    obj2 = SpatialObject(
        label="laptop",
        bbox=BoundingBox(x=0.5, y=0.4, width=0.3, height=0.2),
    )
    cache.ingest_frame(
        objects=[obj2],
        screen_context="coding workspace",
        timestamp=105.0,
    )

    assert cache.total_frames() == 2

    # Query where glasses were seen
    history = cache.find_object_history("glasses")
    assert len(history) == 1
    assert history[0]["screen_context"] == "living room desk"
    assert history[0]["object"]["bbox"]["x"] == 0.2

    # Query most recent location
    recent_loc = cache.get_most_recent_location("laptop")
    assert recent_loc is not None
    assert recent_loc["object"]["label"] == "laptop"

    # Query timeline window
    timeline = cache.get_recent_timeline(window_seconds=10.0)
    assert len(timeline) == 2


def test_astra_goal_pursuit_and_agent_highlighting():
    harness = AstraGoalHarness(goal_statement="Fix payment webhook integration bug")

    # Add milestones
    m1 = harness.add_milestone(
        title="Locate Error",
        description="Find error dialog on screen",
        required_evidence_type="object_located",
    )
    m2 = harness.add_milestone(
        title="Pass Unit Tests",
        description="Run pytest suite and ensure green exit",
        required_evidence_type="test_pass",
    )

    # Initial discrepancy
    initial_eval = harness.evaluate_discrepancy()
    assert initial_eval["discrepancy_score"] == 1.0  # 2/2 pending
    assert initial_eval["progress_percent"] == 0

    # Step 1: Perceive error dialog on screen
    step1_res = harness.pursue_step(
        action_name="screen_perception",
        step_input={"action": "capture_screen"},
        observed_objects=[
            SpatialObject(label="error dialog", bbox=BoundingBox(x=0.1, y=0.1, width=0.4, height=0.3))
        ],
        screen_context="IDE terminal with 500 error",
    )
    assert step1_res["milestone_satisfied"] is True
    assert step1_res["progress_percent"] == 50
    assert len(step1_res["highlights"]) == 1
    assert step1_res["highlights"][0]["label"] == "error dialog"

    # Step 2: Run tests with success result
    step2_res = harness.pursue_step(
        action_name="run_tests",
        step_input={"command": "pytest"},
        tool_result="2 passed in 1.45s - OK",
    )
    assert step2_res["milestone_satisfied"] is True
    assert step2_res["progress_percent"] == 100
    assert step2_res["goal_state"] == "achieved"
