"""Dofus THX-01 fine-tuning pipeline.

Production-grade fine-tuning of doofz/THX-01 (322M encoder-only decision model)
for Dofus game data: combat actions, item lookup, map coordinates, NPC dialogue.

THX-01 is NOT a causal LLM. It is a non-autoregressive decision model that takes
a state + typed questions and returns calibrated answers in a single forward pass
(~10ms on one GPU). Fine-tuning here means defining new Dofus decision question
types and training with the RLCD (Reinforcement Learning for Calibrated Decisions)
objective from the thx01 library.
"""