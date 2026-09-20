"""Tests for DeepMind Promptbreeder Darwinian Evolution Engine."""

from __future__ import annotations

import pytest
from alpha.evolution.promptbreeder import PromptGenome, PromptbreederEngine


def test_promptbreeder_population_initialization():
    engine = PromptbreederEngine(population_size=6, seed=42)
    seeds = [
        "Implement reliable algorithms with type checking.",
        "Refactor legacy routines into pure deterministic functions.",
    ]
    pop = engine.initialize_population(seed_task_prompts=seeds)

    assert len(pop) == 6
    assert pop[0].task_prompt == seeds[0]
    assert pop[1].task_prompt == seeds[1]
    assert pop[0].mutation_prompt != ""


def test_promptbreeder_mutation_operators():
    engine = PromptbreederEngine(seed=42)

    task_base = "Generate unit tests."
    mut_base = "Add negative tests for invalid inputs."

    # 1. Zero-order variation
    zero_out = engine.zero_order_mutation(task_base)
    assert len(zero_out) > len(task_base)

    # 2. First-order mutation (M mutates T)
    first_out = engine.first_order_mutation(task_base, mut_base)
    assert mut_base in first_out
    assert task_base in first_out

    # 3. Hyper-mutation (Mutating M)
    hyper_out = engine.hyper_mutation(mut_base)
    assert len(hyper_out) > len(mut_base)
    assert "Furthermore" in hyper_out

    # 4. Darwinian Crossover
    parent_1 = PromptGenome(
        id="p1",
        task_prompt="Step 1: Check preconditions.\nStep 2: Initialize state.",
        mutation_prompt="mut1",
    )
    parent_2 = PromptGenome(
        id="p2",
        task_prompt="Step 3: Execute computation.\nStep 4: Assert outputs.",
        mutation_prompt="mut2",
    )
    cross_out = engine.darwinian_crossover(parent_1, parent_2)
    assert "Step" in cross_out


def test_promptbreeder_multi_generation_evolution():
    engine = PromptbreederEngine(
        population_size=6,
        tournament_size=2,
        lambda_cost=0.01,
        lambda_latency=0.001,
        seed=123,
    )

    # Mock evaluation benchmark: awards higher score if prompt mentions 'edge cases' and 'step by step'
    def mock_evaluator(prompt: str) -> tuple[float, float, float]:
        accuracy = 0.5
        if "edge cases" in prompt.lower():
            accuracy += 0.3
        if "step by step" in prompt.lower():
            accuracy += 0.2
        token_cost = len(prompt) / 4.0
        latency_ms = 50.0
        return accuracy, token_cost, latency_ms

    seeds = ["Write python code.", "Generate robust functions."]
    res = engine.evolve(generations=4, eval_fn=mock_evaluator, seed_task_prompts=seeds)

    assert res.total_generations == 4
    assert len(res.generation_history) == 4
    assert res.best_fitness > 0.0
    assert len(res.hall_of_fame) >= 1
    # Check that best prompt evolved higher fitness than naive initial prompt
    initial_acc, initial_cost, initial_lat = mock_evaluator("Write python code.")
    initial_fitness = initial_acc - 0.01 * initial_cost - 0.001 * initial_lat
    assert res.best_fitness >= initial_fitness


def test_promptbreeder_zero_fitness_evaluation_handling():
    eval_call_count = 0

    def zero_fitness_evaluator(prompt: str) -> tuple[float, float, float]:
        nonlocal eval_call_count
        eval_call_count += 1
        return 0.0, 0.0, 0.0  # Exactly 0.0 fitness

    engine = PromptbreederEngine(population_size=4, elitism_count=2, seed=99)
    res = engine.evolve(generations=2, eval_fn=zero_fitness_evaluator, seed_task_prompts=["Test prompt."])

    # Gen 0: 4 evaluations.
    # Gen 1: 2 elites cloned with evaluated=True, 2 offspring evaluated -> total exactly 6 evaluations.
    assert res.total_evaluations == 6
    assert eval_call_count == 6
