fp = 'c:/Users/sao/Documents/dofus-emu/dofus-network/agent/planner.py'
with open(fp, 'r') as f:
    c = f.read()

# Replace _map_interactives
old = '''    @staticmethod
    def _map_interactives(obs: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The map's interactive elements, from the nested block or the flat key.

        A free-form objective is performed through one of these; the element list is
        what the client's own renderer reports (``isoEngine.mapRenderer.interactiveElements``),
        not a table the planner invents.
        """
        if not isinstance(obs, dict):
            return []
        block = obs.get("map") if isinstance(obs.get("map"), dict) else {}
        raw = obs.get("interactives")
        if not isinstance(raw, list):
            raw = block.get("interactives")
        out: List[Dict[str, Any]] = []
        for entry in raw or []:
            if not isinstance(entry, dict):
                continue
            elem_id = entry.get("id", entry.get("element_id", entry.get("elemId")))
            try:
                elem_id = int(elem_id)
            except (TypeError, ValueError):
                continue
            row = dict(entry)
            row["elem_id"] = elem_id
            out.append(row)
        return out'''

new = '''    @staticmethod
    def _map_interactives(obs: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The map's interactive elements, from the nested block or the flat key."""
        from agent.map_utils import map_interactives as _map_interactives_impl
        return _map_interactives_impl(obs)'''

if old in c:
    c = c.replace(old, new, 1)
    with open(fp, 'w') as f:
        f.write(c)
    print("_map_interactives replaced")
else:
    print("_map_interactives NOT found")

# Replace _tutorial
old = '''    def _tutorial(obs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        block = obs.get("tutorial")
        if isinstance(block, dict) and block.get("active"):
            return block
        return None'''

new = '''    @staticmethod
    def _tutorial(obs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Tutorial block from observation, if active."""
        from agent.equipment_utils import _tutorial as _tutorial_impl
        return _tutorial_impl(obs)'''

if old in c:
    c = c.replace(old, new, 1)
    with open(fp, 'w') as f:
        f.write(c)
    print("_tutorial replaced")
else:
    print("_tutorial NOT found")

# Replace _tutorial_quest
old = '''    @staticmethod
    def _tutorial_quest(obs: Dict[str, Any],
                        tutorial: Dict[str, Any]) -> Optional[int]:
        """The quest the scenario is driving, without guessing from the list order.

        The scenario knows its own quest id; the tracker is the fallback. Taking
        `quests_active[0]` is what let a stall retire the only live quest while the
        start loop blamed a different one.
        """
        tracker = obs.get("current_objective") or {}
        for raw in (tutorial.get("quest_id"), tracker.get("quest_id")):
            try:
                qid = int(raw)
            except (TypeError, ValueError):
                continue'''

new = '''    @staticmethod
    def _tutorial_quest(obs: Dict[str, Any],
                        tutorial: Dict[str, Any]) -> Optional[int]:
        """The quest the scenario is driving, without guessing from the list order."""
        from agent.equipment_utils import _tutorial_quest as _tutorial_quest_impl
        return _tutorial_quest_impl(obs, tutorial)'''

if old in c:
    c = c.replace(old, new, 1)
    with open(fp, 'w') as f:
        f.write(c)
    print("_tutorial_quest replaced")
else:
    print("_tutorial_quest NOT found")

# Replace _tutorial (the first one)
old_tutorial = '''    def _tutorial(obs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        block = obs.get("tutorial")
        if isinstance(block, dict) and block.get("active"):
            return block
        return None'''

new_tutorial = '''    @staticmethod
    def _tutorial(obs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Tutorial block from observation, if active."""
        from agent.equipment_utils import _tutorial as _tutorial_impl
        return _tutorial_impl(obs)'''

if old_tutorial in c:
    c = c.replace(old_tutorial, new_tutorial, 1)
    with open(fp, 'w') as f:
        f.write(c)
    print("_tutorial replaced")
else:
    print("_tutorial NOT found")