"""Vectorised Dofus encounters for TPU self-play.

The live estimator needs one thing the hand-written rules cannot give it: a
probability that a given group is beatable under *play*, not under a one-turn
damage heuristic. This package models the encounter as a small, fully batched
Markov state, so thousands of fights can be rolled out on a TPU and a shared
policy/value head can be trained on the outcomes.

The model is deliberately coarse where the live client has detail: actors share
a one-dimensional distance to the opposing line, spells are expected damage
rather than rolls, and every actor in a team acts on that team's turn. It keeps
the parts a win probability depends on — HP, AP/MP budgets, ranges, resists,
team size, initiative, healing — and is honest about the rest in the dataset
metadata.
"""
