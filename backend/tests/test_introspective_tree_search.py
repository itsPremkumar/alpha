"""Unit tests for Introspective Language Agent Tree Search (I-MCTS / CodeTree)."""

import ast
import pytest

from agent_workspace.reasoning.introspective_tree_search import (
    CompositeRewardEvaluator,
    IntrospectiveTreeSearchEngine,
    MCTSAction,
    MCTSNode,
    ValidationFeedback,
    run_introspective_tree_search,
)


def test_composite_reward_evaluator():
    evaluator = CompositeRewardEvaluator()

    # Valid green state
    green_feedback = ValidationFeedback(
        syntax_valid=True,
        compilation_exit_code=0,
        tests_passed=10,
        tests_total=10,
        diff_size=40,
    )
    score_green = evaluator.compute_reward(green_feedback)
    assert score_green > 0.95

    # Syntax error yields zero
    syntax_err_feedback = ValidationFeedback(
        syntax_valid=False,
        compilation_exit_code=1,
        tests_passed=0,
        tests_total=10,
        error_message="Invalid syntax",
    )
    assert evaluator.compute_reward(syntax_err_feedback) == 0.0

    # Partial test pass rate
    partial_feedback = ValidationFeedback(
        syntax_valid=True,
        compilation_exit_code=0,
        tests_passed=5,
        tests_total=10,
        diff_size=100,
    )
    score_partial = evaluator.compute_reward(partial_feedback)
    assert 0.5 < score_partial < score_green


def test_mcts_node_and_uct():
    root = MCTSNode(node_id="root", depth=0)
    root.visits = 10
    root.total_value = 8.0

    child = MCTSNode(node_id="child_1", parent=root, depth=1)
    child.visits = 2
    child.total_value = 1.6

    root.children.append(child)

    # UCT formula: value + c * sqrt(ln(parent.visits) / visits)
    uct = child.compute_uct(exploration_constant=1.414)
    assert uct > child.value

    # Pruned node has negative infinite UCT
    child.is_pruned = True
    assert child.compute_uct() == -float("inf")


def test_introspective_failure_learning():
    engine = IntrospectiveTreeSearchEngine()
    parent = MCTSNode(node_id="p")

    # Add a failed sibling with syntax error
    failed_sibling = MCTSNode(node_id="s1", parent=parent)
    failed_sibling.validation_feedback = ValidationFeedback(
        syntax_valid=False,
        error_message="unexpected EOF while parsing",
    )
    parent.children.append(failed_sibling)

    # Add a sibling with test failure
    test_fail_sibling = MCTSNode(node_id="s2", parent=parent)
    test_fail_sibling.validation_feedback = ValidationFeedback(
        syntax_valid=True,
        tests_passed=1,
        tests_total=3,
        error_message="AssertionError: 2 != 3",
    )
    parent.children.append(test_fail_sibling)

    lessons = engine.introspect_failures(parent)
    assert len(lessons) == 2
    assert any("unexpected EOF" in l for l in lessons)
    assert any("AssertionError" in l for l in lessons)


def test_tree_search_early_termination_on_green():
    engine = IntrospectiveTreeSearchEngine()

    initial_state = {
        "problem_statement": "Fix arithmetic function add(a, b)",
        "file_contents": {"calc.py": "def add(a, b):\n    return a - b\n"},
    }

    # Action that yields green state
    green_action = MCTSAction(
        action_type="code_edit",
        target_file="calc.py",
        edit_content="def add(a, b):\n    return a + b\n",
        hypothesis="Change subtraction to addition",
    )

    def custom_eval(node: MCTSNode) -> ValidationFeedback:
        content = node.state_snapshot.get("file_contents", {}).get("calc.py", "")
        if "return a + b" in content:
            return ValidationFeedback(
                syntax_valid=True,
                compilation_exit_code=0,
                tests_passed=5,
                tests_total=5,
                diff_size=20,
            )
        return ValidationFeedback(
            syntax_valid=True,
            compilation_exit_code=0,
            tests_passed=0,
            tests_total=5,
            diff_size=20,
        )

    result = engine.search(
        root_state=initial_state,
        candidate_actions_generator=lambda node: [green_action] if node.depth == 0 else [],
        eval_fn=custom_eval,
        max_iterations=5,
    )

    assert result["success"] is True
    assert result["data"]["is_green"] is True
    assert result["data"]["best_reward"] >= 0.95
    assert len(result["data"]["path"]) >= 1


def test_tree_search_prunes_syntax_error():
    engine = IntrospectiveTreeSearchEngine()

    initial_state = {"file_contents": {"module.py": "x = 1\n"}}
    syntax_error_action = MCTSAction(
        action_type="code_edit",
        target_file="module.py",
        edit_content="def broken(\n",
    )

    result = engine.search(
        root_state=initial_state,
        candidate_actions_generator=lambda node: [syntax_error_action] if node.depth == 0 else [],
        max_iterations=2,
    )

    assert result["success"] is True
    # The child with syntax error should be pruned
    tree = result["data"]["tree"]
    assert "children" in tree
    if tree["children"]:
        child = tree["children"][0]
        assert child["is_pruned"] is True
        assert "Syntax error" in child["prune_reason"]


def test_run_introspective_tree_search_tool():
    res = run_introspective_tree_search.invoke({
        "problem_statement": "ZeroDivisionError in compute_average()",
        "files_to_modify": ["stats.py"],
        "max_iterations": 3,
        "candidate_actions": [
            {
                "action_type": "code_edit",
                "target_file": "stats.py",
                "edit_content": "def compute_average(nums):\n    return sum(nums) / len(nums) if nums else 0.0\n",
                "hypothesis": "Add empty sequence guard to prevent division by zero",
            }
        ],
    })

    assert isinstance(res, dict)
    assert res["success"] is True
    assert "best_node" in res["data"]
    assert "best_reward" in res["data"]
    assert res["data"]["best_reward"] > 0.0
    assert res["data"]["iterations_executed"] >= 1


def test_expand_filters_repeating_failed_hypothesis():
    engine = IntrospectiveTreeSearchEngine()
    parent = MCTSNode(node_id="root", depth=0)

    # Failed sibling with hypothesis
    failed_sibling = MCTSNode(node_id="child_1", parent=parent, depth=1)
    failed_sibling.action = MCTSAction(hypothesis="Divide by constant 10")
    failed_sibling.validation_feedback = ValidationFeedback(
        syntax_valid=False,
        error_message="ZeroDivisionError",
    )
    failed_sibling.is_pruned = True
    failed_sibling.prune_reason = "ZeroDivisionError in constant 10"
    parent.children.append(failed_sibling)

    # Expanding another node under same parent should avoid repeating the failed hypothesis
    new_node = MCTSNode(node_id="child_2", parent=parent, depth=1)
    candidates = [
        MCTSAction(hypothesis="Divide by constant 10"),
        MCTSAction(hypothesis="Check divisor before division"),
    ]
    expanded = engine.expand(new_node, candidates)

    assert len(expanded) == 1
    assert expanded[0].action.hypothesis == "Check divisor before division"

