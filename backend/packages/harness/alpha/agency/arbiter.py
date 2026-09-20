from __future__ import annotations


class MotivationArbiter:
    """
    Arbitrates between Curiosity (exploration drive),
    Competence (mastery / exploitation drive), and Extrinsic User Directives.
    """

    def __init__(
        self,
        weight_curiosity: float = 0.25,
        weight_competence: float = 0.35,
        weight_extrinsic: float = 0.40,
    ) -> None:
        self.w_curiosity = weight_curiosity
        self.w_competence = weight_competence
        self.w_extrinsic = weight_extrinsic

    def arbitrate(
        self,
        curiosity_score: float,
        competence_score: float,
        user_priority: float = 1.0,
    ) -> tuple[str, float]:
        """
        Calculates drive intensity and selects dominant operational mode:
          - EXPLORE: high curiosity, novel environment
          - EXPLOIT: high competence, predictable execution
          - DIRECTED: strict user instruction adherence
        """
        c = max(0.0, min(1.0, curiosity_score))
        comp = max(0.0, min(1.0, competence_score))
        u = max(0.0, min(1.0, user_priority))

        explore_drive = c * self.w_curiosity
        exploit_drive = comp * self.w_competence
        directed_drive = u * self.w_extrinsic

        total_motivation = round(explore_drive + exploit_drive + directed_drive, 4)

        if directed_drive >= explore_drive and directed_drive >= exploit_drive:
            dominant_mode = "DIRECTED_EXECUTION"
        elif explore_drive > exploit_drive:
            dominant_mode = "AUTONOMOUS_EXPLORATION"
        else:
            dominant_mode = "EXPLOITATIVE_DELIBERATION"

        return dominant_mode, total_motivation
