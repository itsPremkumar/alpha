"""DeepMind Promptbreeder Darwinian Evolution Engine for Self-Improving Prompts.

Evolves both Task Prompts and Mutation Prompts simultaneously in a self-referential
evolutionary cycle with multi-objective fitness scoring against evaluation benchmarks.
"""

from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class PromptGenome:
    id: str
    task_prompt: str
    mutation_prompt: str
    generation: int = 0
    fitness: float = 0.0
    accuracy: float = 0.0
    token_cost: float = 0.0
    latency_ms: float = 0.0
    parent_ids: list[str] = field(default_factory=list)
    mutation_operator_used: str = ""
    evaluated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_prompt": self.task_prompt,
            "mutation_prompt": self.mutation_prompt,
            "generation": self.generation,
            "fitness": round(self.fitness, 4),
            "accuracy": round(self.accuracy, 4),
            "token_cost": round(self.token_cost, 4),
            "latency_ms": round(self.latency_ms, 2),
            "parent_ids": self.parent_ids,
            "mutation_operator_used": self.mutation_operator_used,
        }


@dataclass
class EvolutionGenerationSummary:
    generation: int
    best_fitness: float
    avg_fitness: float
    best_task_prompt: str
    best_mutation_prompt: str
    population_size: int


@dataclass
class EvolutionResult:
    best_task_prompt: str
    best_mutation_prompt: str
    best_fitness: float
    hall_of_fame: list[PromptGenome]
    generation_history: list[EvolutionGenerationSummary]
    total_generations: int
    total_evaluations: int


class PromptbreederEngine:
    """Darwinian self-referential evolutionary engine inspired by DeepMind Promptbreeder."""

    DEFAULT_MUTATION_PROMPTS = [
        "Incorporate strict pre-condition checks and boundary constraints.",
        "Add step-by-step chain-of-thought verification before finalizing the output.",
        "Make the instructions concise, eliminating redundant fluff and boilerplate.",
        "Include negative constraints prohibiting common error patterns and regressions.",
        "Formulate a defense checklist for handling empty, invalid, or malformed inputs.",
        "Emphasize deterministic type annotations and explicit return values.",
    ]

    DEFAULT_HYPER_MUTATION_OPERATORS = [
        "Focus more aggressively on edge cases and failure modes.",
        "Optimize for minimal token usage while preserving semantic accuracy.",
        "Instruct the model to structure the response as a formal verification proof.",
        "Direct attention toward backwards compatibility and test assertion preservation.",
    ]

    def __init__(
        self,
        population_size: int = 10,
        tournament_size: int = 3,
        lambda_cost: float = 0.05,
        lambda_latency: float = 0.001,
        elitism_count: int = 2,
        seed: Optional[int] = None,
    ) -> None:
        self.population_size = population_size
        self.tournament_size = min(tournament_size, population_size)
        self.lambda_cost = lambda_cost
        self.lambda_latency = lambda_latency
        self.elitism_count = elitism_count
        self.rng = random.Random(seed)
        self.population: list[PromptGenome] = []
        self.hall_of_fame: list[PromptGenome] = []

    def initialize_population(
        self,
        seed_task_prompts: list[str],
        seed_mutation_prompts: Optional[list[str]] = None,
    ) -> list[PromptGenome]:
        """Seeds initial dual population of Task and Mutation Prompts."""
        mut_seeds = seed_mutation_prompts or self.DEFAULT_MUTATION_PROMPTS
        self.population = []

        for i in range(self.population_size):
            t_prompt = seed_task_prompts[i % len(seed_task_prompts)]
            m_prompt = mut_seeds[i % len(mut_seeds)]
            genome = PromptGenome(
                id=f"pb-{uuid.uuid4().hex[:8]}",
                task_prompt=t_prompt,
                mutation_prompt=m_prompt,
                generation=0,
            )
            self.population.append(genome)

        return self.population

    # --- Mutation Operators ---

    def zero_order_mutation(self, task_prompt: str) -> str:
        """Direct syntactic/semantic variation without meta-prompt."""
        variations = [
            f"{task_prompt}\nEnsure all edge cases, empty inputs, and boundary values are explicitly verified.",
            f"Think step by step:\n{task_prompt}",
            f"{task_prompt}\nBe concise, direct, and avoid unnecessary explanatory text.",
            f"Requirements:\n- Maintain full backward compatibility\n- {task_prompt}",
        ]
        return self.rng.choice(variations)

    def first_order_mutation(self, task_prompt: str, mutation_prompt: str) -> str:
        """Applies a mutation prompt to mutate a task prompt."""
        return f"{task_prompt}\n[Instruction Optimization: {mutation_prompt}]"

    def hyper_mutation(self, mutation_prompt: str) -> str:
        """Mutates the mutation prompt itself (self-referential)."""
        meta = self.rng.choice(self.DEFAULT_HYPER_MUTATION_OPERATORS)
        return f"{mutation_prompt} Furthermore, {meta.lower()}"

    def darwinian_crossover(self, parent_a: PromptGenome, parent_b: PromptGenome) -> str:
        """Recombines high-performing instructions from two parent genomes."""
        lines_a = [line.strip() for line in parent_a.task_prompt.splitlines() if line.strip()]
        lines_b = [line.strip() for line in parent_b.task_prompt.splitlines() if line.strip()]

        split_a = len(lines_a) // 2 or 1
        split_b = len(lines_b) // 2 or 1

        child_lines = lines_a[:split_a] + lines_b[split_b:]
        if not child_lines:
            child_lines = lines_a or lines_b
        return "\n".join(child_lines)

    # --- Selection & Fitness ---

    def tournament_selection(self) -> PromptGenome:
        """Binary/K-way tournament selection."""
        k = max(1, min(self.tournament_size, len(self.population)))
        candidates = self.rng.sample(self.population, k)
        return max(candidates, key=lambda g: g.fitness)

    def evaluate_fitness(
        self,
        genome: PromptGenome,
        eval_fn: Callable[[str], tuple[float, float, float]],  # prompt -> (accuracy, token_cost, latency_ms)
    ) -> float:
        """Calculates multi-objective fitness score: F = Accuracy - lambda_cost*Cost - lambda_lat*Lat."""
        accuracy, cost, lat = eval_fn(genome.task_prompt)
        genome.accuracy = accuracy
        genome.token_cost = cost
        genome.latency_ms = lat
        
        # Penalties
        fitness = accuracy - (self.lambda_cost * cost) - (self.lambda_latency * lat)
        genome.fitness = fitness
        genome.evaluated = True
        return fitness

    # --- Evolution Loop ---

    def evolve(
        self,
        generations: int,
        eval_fn: Callable[[str], tuple[float, float, float]],
        seed_task_prompts: Optional[list[str]] = None,
    ) -> EvolutionResult:
        """Executes the full Darwinian evolutionary optimization loop over N generations."""
        if not self.population:
            seeds = seed_task_prompts or ["Write robust, clean, tested production code."]
            self.initialize_population(seeds)

        history: list[EvolutionGenerationSummary] = []
        total_evals = 0

        for gen in range(generations):
            # 1. Evaluate current generation
            for genome in self.population:
                if not genome.evaluated:
                    self.evaluate_fitness(genome, eval_fn)
                    total_evals += 1

            # Sort population descending by fitness
            self.population.sort(key=lambda g: g.fitness, reverse=True)

            # Update Hall of Fame (Pareto archive)
            best_curr = self.population[0]
            if not self.hall_of_fame or best_curr.fitness > self.hall_of_fame[0].fitness:
                self.hall_of_fame.insert(0, best_curr)
            if len(self.hall_of_fame) > 5:
                self.hall_of_fame.pop()

            avg_fitness = sum(g.fitness for g in self.population) / len(self.population)
            history.append(
                EvolutionGenerationSummary(
                    generation=gen + 1,
                    best_fitness=best_curr.fitness,
                    avg_fitness=avg_fitness,
                    best_task_prompt=best_curr.task_prompt,
                    best_mutation_prompt=best_curr.mutation_prompt,
                    population_size=len(self.population),
                )
            )

            # If last generation, do not spawn next offspring
            if gen == generations - 1:
                break

            # 2. Elitism: preserve top elite genomes into next generation
            next_generation: list[PromptGenome] = []
            for elite in self.population[: self.elitism_count]:
                cloned = PromptGenome(
                    id=f"pb-{uuid.uuid4().hex[:8]}",
                    task_prompt=elite.task_prompt,
                    mutation_prompt=elite.mutation_prompt,
                    generation=gen + 1,
                    fitness=elite.fitness,
                    accuracy=elite.accuracy,
                    token_cost=elite.token_cost,
                    latency_ms=elite.latency_ms,
                    parent_ids=[elite.id],
                    mutation_operator_used="elitism_preservation",
                    evaluated=True,
                )
                next_generation.append(cloned)

            # 3. Spawn offspring via Promptbreeder mutation operators
            while len(next_generation) < self.population_size:
                parent_a = self.tournament_selection()
                parent_b = self.tournament_selection()

                operator_choice = self.rng.random()
                if operator_choice < 0.35:
                    # First-order mutation (M mutates T)
                    new_task = self.first_order_mutation(parent_a.task_prompt, parent_a.mutation_prompt)
                    new_mut = parent_a.mutation_prompt
                    op_name = "first_order_mutation"
                elif operator_choice < 0.60:
                    # Darwinian crossover
                    new_task = self.darwinian_crossover(parent_a, parent_b)
                    new_mut = self.rng.choice([parent_a.mutation_prompt, parent_b.mutation_prompt])
                    op_name = "darwinian_crossover"
                elif operator_choice < 0.85:
                    # Zero-order direct variation
                    new_task = self.zero_order_mutation(parent_a.task_prompt)
                    new_mut = parent_a.mutation_prompt
                    op_name = "zero_order_variation"
                else:
                    # Self-referential hyper-mutation (mutating M)
                    new_task = parent_a.task_prompt
                    new_mut = self.hyper_mutation(parent_a.mutation_prompt)
                    op_name = "hyper_mutation"

                child = PromptGenome(
                    id=f"pb-{uuid.uuid4().hex[:8]}",
                    task_prompt=new_task,
                    mutation_prompt=new_mut,
                    generation=gen + 1,
                    parent_ids=[parent_a.id, parent_b.id],
                    mutation_operator_used=op_name,
                )
                next_generation.append(child)

            self.population = next_generation

        best_overall = self.hall_of_fame[0] if self.hall_of_fame else self.population[0]
        return EvolutionResult(
            best_task_prompt=best_overall.task_prompt,
            best_mutation_prompt=best_overall.mutation_prompt,
            best_fitness=best_overall.fitness,
            hall_of_fame=self.hall_of_fame,
            generation_history=history,
            total_generations=generations,
            total_evaluations=total_evals,
        )
