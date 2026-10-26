from dataclasses import dataclass,field
from pathlib import Path
import json

REPO_ROOT=Path(__file__).resolve().parents[1]
CAPABILITIES_FILE=REPO_ROOT/"data/capabilities.json"
SPOKEN_LANGUAGES=("en","fr","es","de")

@dataclass
class ToolSpec:
    name:str
    description:str
    params:dict=field(default_factory=dict)
    backend:str="local"
    directive:str|None=None
    returns:str=""

    def required_params(self):
        return [k for k,v in self.params.items() if v.get("required")]

    def as_dict(self):
        return {"name":self.name,"description":self.description,"backend":self.backend,"directive":self.directive,"returns":self.returns,"params":self.params}

_T=ToolSpec

def _p(type_name,doc,required=False,enum=None,default=None):
    s={"type":type_name,"doc":doc,"required":required}
    if enum is not None:s["enum"]=enum
    if default is not None:s["default"]=default
    return s

_BUILTIN_TOOLS=[
_T('knowledge.search','Look up game knowledge (monsters, spells, quests, items, NPCs, achievements, dungeons) in the local DofusDB cache. Use before claiming any game fact.',{'query':_p('str','Free-text name or keyword, any of the 4 languages.',True),'kind':_p('str',"Entity kind to search, or 'any'.",False,['any','monster','spell','quest','item','npc','achievement','dungeon'],'any'),'limit':_p('int','Max results.',default=5)},'local',None,"list of compact records with 'id', 'name' (localised dict) and key fields"),
_T('knowledge.remember',"Record something you discovered that the cache does not know (an NPC's map, a drop, a price, a quest prerequisite). Facts are stored with provenance, so a wrong one can be corrected or forgotten later. Never use this to copy the cache.",{'kind':_p('str','Fact kind.',True,['npc_map','monster_drop','item_source','price','quest_prereq','route','other']),'key':_p('str',"Stable key, e.g. 'npc:447' or 'item:1746'.",True),'value':_p('str',"The fact as text or JSON, e.g. '12345'.",True),'confidence':_p('float','0-1. Use 0.9 for something you saw, 0.5 for an inference.',default=0.5)},'local',None,"{'changed': bool, 'note': str}"),
_T('knowledge.forget','Delete a fact that turned out to be wrong. Use it instead of remembering a contradicting value, so the store stays small.',{'kind':_p('str','Fact kind.',True),'key':_p('str','Key of the fact to remove.',True),'reason':_p('str','Why it is wrong (kept in the log).',default='')},'local',None,"{'removed': bool}"),
_T('nav.goto','Travel to a target: [x,y] coordinates, a zaap name, an NPC, a map id, a cell (a specific spot on this map), or a direction (leave by that edge).',{'x':_p('int','Target X coordinate.'),'y':_p('int','Target Y coordinate.'),'zaap':_p('str','Zaap name, any language.'),'npc':_p('str','NPC name to travel to.'),'map_id':_p('int','Raw map id.'),'cell':_p('int','Walk to this cell on the current map.'),'direction':_p('str','Leave by this edge of the current map.',enum=['left','right','top','bottom']),'npc_id':_p('int',"Walk to this NPC's cell, by template id."),'lang':_p('str','Reply language.',False,['fr','en','es','de'],'fr'),'from_x':_p('int','Current X (default 4).'),'from_y':_p('int','Current Y (default -19).')},'sentinel',None,'navigation path with steps; the client bridge then walks it'),
_T('nav.follow',"Follow a character on the current map and through map changes. Empty name means 'follow the order giver'.",{'name':_p('str','Character name to follow; empty = the order giver.',default='')},'directive','FOLLOW','directive ack'),
_T('quest.list','List quests suitable for a level (and optionally an area), with start position and step objectives.',{'area':_p('str',"Area or subarea name filter, any language (e.g. 'astrub')."),'level_min':_p('int','Player level lower bound.'),'level_max':_p('int','Player level upper bound.'),'dungeon_only':_p('bool','Only dungeon quests.',default=False),'limit':_p('int','Max results.',default=10)},'local',None,'list of quests with id, localised name, level range and step objectives'),
_T('quest.advance','Execute the next actionable step of a quest: navigate, talk to the NPC, harvest, or fight as the step objective requires.',{'quest_id':_p('int','Quest id from quest.list.',True),'npc_id':_p('int','Talk to this NPC, by the template id the objective carries.'),'objective_id':_p('int','Validate this objective. Use it to claim one the game already accepts (objectiveStatus 2).'),'reply':_p('int','Which dialog reply to choose, by index. Defaults to 0, which is what an unanswered NPC dialog needs.'),'reply_text':_p('str','Which dialog reply to choose, by the exact French text reported in dialog.replies. With reply, both must agree.'),'close':_p('bool','Close an open dialog whose reply list is empty.')},'directive','QUEST_ADVANCE','per-step progress report'),
_T('farm.start','Start a farm loop: kill monster groups and/or harvest resources, looting according to the filter, optionally auto-selling.',{'monster':_p('id_or_name','Monster id from the objective, or a name from the corpus. The client needs an id; the harness resolves a name.'),'resource':_p('id_or_name','Resource id from the objective, or a name. The client harvests the nearest interactive element.'),'zone':_p('str','Area/subarea to stay inside, any language.'),'map_id':_p('int','Farm on one specific map.'),'kill_count':_p('int','Stop after this many kills.',default=100),'duration_min':_p('int','Stop after this many minutes.'),'loot_filter':_p('str_list','Item names to keep; others can be dropped.'),'auto_sell':_p('bool','Auto-list kept loot on the market when bags fill.',default=False),'restart':_p('bool','Start a fresh session even if the farm is already running.',default=False)},'directive','FARM_START','farm session id and live counters'),
_T('farm.stop','Stop the active farm loop and return to a safe idle state.',{},'directive','FARM_STOP','final farm counters'),
_T('fight.join','Join a fight: a monster group containing the named monster, an explicit group, or the nearest group.',{'monster':_p('int','Monster id from the objective. The group containing it is chosen; the client knows which group that is.'),'group_id':_p('int','Explicit monster-group id, when the caller has one.')},'directive','AUTO_JOIN_COMBAT','fight state'),
_T('fight.pass','Pass the current combat turn.',{},'directive','PASS_TURN','ack'),
_T('fight.state',"Read the live combat board: turn owner, fighters, cells, HP/AP/MP, team membership, and the controlled character's spell descriptors. The result also carries a versioned observation and a contract naming any fact the client did not expose.",{},'directive','FIGHT_STATE','turn, fighter and spell facts from the live client, plus observation/contract'),
_T('fight.plan',"Read the live board and compute this turn's legal move-and-cast plan: exact cell geometry, line of sight, AP/MP, cooldowns, resistances, damage and healing. top_k > 1 also returns alternative plans (including pass) for a ranker. Read-only; it never casts or moves anything. Every answer carries the live observation and its contract.",{'beam_width':_p('int','How many partial turns to keep while searching.',default=16),'max_actions':_p('int','Maximum casts to consider in the plan.',default=4),'top_k':_p('int','Also return this many candidate plans; 1 = best only.',default=1),'rank_with_value':_p('bool','Compatibility alias for ranker=value: rank candidates with the learned encounter value head.',default=False),'ranker':_p('str',"Which plan ranker to use. 'solver' keeps the solver order; 'value' ranks candidates with the learned value head and refuses when the live observation lacks self stats or enemy spell facts.",False,['solver','value'],'solver')},'local',None,'actions with cells/targets/AP/MP/damage, plus the normalized live observation, its contract, and the ranker decision'),
_T('fight.estimate','Estimate whether the controlled character can win a monster group visible on this map: turns to kill, incoming damage, expected HP loss, and a calibrated win probability. Read-only; it never joins or casts.',{'group_id':_p('int','Monster group id from map.groups.'),'monster':_p('id_or_name','Monster id or name. When several visible groups contain it, the least favourable one is estimated.')},'local',None,'p_win (null until calibrated), raw_score, expected_hp_loss and turns, confidence, missing inputs, allowed and blocked_reason'),
_T('inv.list','List inventory contents, optionally filtered by kind.',{'kind':_p('str','Inventory slice.',False,['all','resources','equipment','consumables','quest_items'],'all')},'directive','INV_LIST','items with id, name, quantity, unit weight'),
_T('inv.sell','Sell inventory items on the market (HDV) at a price rule. Default rule is median market price minus 1 kama.',{'item':_p('str','Item name; omit when all=true.'),'all':_p('bool','Consider the whole inventory.',default=False),'quantity':_p('int','Units to sell; default: all owned (respecting min_stock).'),'min_stock':_p('int','Keep at least this many units.',default=1),'price_rule':_p('str','Pricing rule.',False,['median_minus_1','undercut','fixed'],'median_minus_1'),'price':_p('int','Price per unit when price_rule=fixed.')},'directive','INV_SELL','per-item listing report (price used, quantity, market depth)'),
_T('hdv.scan','Scan the market (HDV) for an item and return price statistics (min/median/max over current listings).',{'item':_p('str','Item name to look up.'),'category':_p('int','Market category id, when browsing rather than searching.'),'type':_p('int','Item type id filter.'),'item_type':_p('int','Alias of type.'),'id':_p('int','Item id filter.'),'gid':_p('int','Item group id filter.')},'directive','HDV_SCAN','price stats and listing count'),
_T('hdv.list','List an item for sale on the market at an explicit or rule-based price.',{'item':_p('str','Item name, any language.',True),'quantity':_p('int','Units to list.',default=1),'price_rule':_p('str','Pricing rule.',False,['median_minus_1','undercut','fixed'],'median_minus_1'),'price':_p('int','Price per unit when price_rule=fixed.')},'directive','HDV_LIST','listing confirmations'),
_T('chat.say','Send a chat message.',{'text':_p('str','Message text.',True),'channel':_p('str','Chat channel.',False,['say','team','guild','whisper'],'say'),'target':_p('str','Recipient for whisper.')},'directive','CHAT_SAY','ack'),
_T('state.snapshot','Request a structured snapshot of current game state (position, map, HP, inventory summary, active fight).',{},'directive','STATE_SNAPSHOT','observation dict recorded into the trajectory log'),
_T('safe.stop','Emergency stop: halt farm/fight loops, stop acting, log out of dangerous situations. Always available.',{'reason':_p('str','Why the stop was triggered.')},'directive','SAFE_STOP','ack'),
]

def _load_capabilities(path=CAPABILITIES_FILE):
    if not path.exists():return None
    try:payload=json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError,OSError):return None
    specs=[]
    for e in payload.get("capabilities") or []:
        n=str(e.get("id") or "").strip()
        if not n:continue
        ps={}
        for k,r in (e.get("params") or {}).items():
            if not isinstance(r,dict):continue
            s={"type":str(r.get("type") or "str"),"doc":str(r.get("doc") or ""),"required":bool(r.get("required"))}
            if r.get("enum"):s["enum"]=list(r["enum"])
            if r.get("default") is not None:s["default"]=r["default"]
            ps[k]=s
        specs.append(ToolSpec(n,str(e.get("description") or ""),ps,str(e.get("backend") or "local"),e.get("directive"),str(e.get("returns") or "")))
    return specs or None

_registry=_load_capabilities()
TOOLS=_registry or _BUILTIN_TOOLS
CAPABILITY_SOURCE="registry" if _registry else "builtin"

def capability_descriptions():
    return [s.description or s.name for s in TOOLS]

def save_capabilities(path=CAPABILITIES_FILE):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({"version":1,"languages":list(SPOKEN_LANGUAGES),"capabilities":[{"id":s.name,"verb":s.name.split(".")[0],"description":s.description,"backend":s.backend,"directive":s.directive,"returns":s.returns,"params":s.params} for s in TOOLS]},ensure_ascii=False,indent=1),encoding="utf-8")

def add_capability(spec,path=CAPABILITIES_FILE):
    global TOOLS,TOOLS_BY_NAME
    if spec.name in TOOLS_BY_NAME:raise ValueError(f"capability {spec.name!r} already exists")
    TOOLS=list(TOOLS)+[spec]
    TOOLS_BY_NAME={s.name:s for s in TOOLS}
    save_capabilities(path)
    return len(TOOLS)-1

TOOLS_BY_NAME={s.name:s for s in TOOLS}

def tool_names():
    return [s.name for s in TOOLS]

def to_json_schema():
    return {"version":1,"languages":list(SPOKEN_LANGUAGES),"tools":[{**s.as_dict(),"json_schema":{"type":"object","properties":s.params,"required":s.required_params()}} for s in TOOLS]}

TOOL_SCHEMA=to_json_schema()

DERIVED_FIELDS=frozenset({"channel","kind","price_rule","lang"})

def _coerce(type_name,value):
    if type_name=="str":return (True,value) if isinstance(value,str) else (False,"expected a string")
    if type_name=="int":
        if isinstance(value,bool):return False,"expected an integer, got a boolean"
        if isinstance(value,int):return True,value
        if isinstance(value,float) and value.is_integer():return True,int(value)
        if isinstance(value,str):
            try:return True,int(value.strip())
            except ValueError:return False,"expected an integer"
        return False,"expected an integer"
    if type_name=="id_or_name":
        if isinstance(value,bool):return False,"expected an id or a name, got a boolean"
        if isinstance(value,int):return True,value
        if isinstance(value,str):
            text=value.strip()
            if not text:return False,"expected an id or a name"
            try:return True,int(text)
            except ValueError:return True,text
        return False,"expected an id or a name"
    if type_name=="float":
        if isinstance(value,bool):return False,"expected a number, got a boolean"
        if isinstance(value,(int,float)):return True,float(value)
        return False,"expected a number"
    if type_name=="bool":
        return (True,value) if isinstance(value,bool) else (False,"expected a boolean")
    if type_name=="str_list":
        if isinstance(value,list) and all(isinstance(v,str) for v in value):return True,value
        if isinstance(value,str):return True,[p.strip() for p in value.split(",") if p.strip()]
        return False,"expected a list of strings"
    return False,f"unknown type '{type_name}'"

def _special_rules(name,a):
    if name=="nav.goto":
        hx="x" in a;hy="y" in a
        if hx!=hy:return "nav.goto needs both x and y, or neither"
        if not (hx and hy or any(k in a for k in ("map_id","zaap","npc","cell","direction","npc_id"))):
            return "nav.goto needs one of: x+y, zaap, npc, map_id, cell, direction, npc_id"
    elif name=="inv.sell":
        if not (a.get("item") or a.get("all")):return "inv.sell needs an item name or all=true"
        if a.get("price_rule")=="fixed" and not isinstance(a.get("price"),int):return "inv.sell with price_rule=fixed needs an integer price"
    elif name=="hdv.list":
        if a.get("price_rule")=="fixed" and not isinstance(a.get("price"),int):return "hdv.list with price_rule=fixed needs an integer price"
    elif name=="chat.say":
        if a.get("channel")=="whisper" and not a.get("target"):return "chat.say on the whisper channel needs a target"

def canonicalize_args(name,args=None):
    spec=TOOLS_BY_NAME.get(name)
    if spec is None:return dict(args or {})
    p=spec.params
    out={}
    for k,v in (args or {}).items():
        q=p.get(k)
        if q is None or q.get("required"):out[k]=v;continue
        d=q.get("default")
        if d is None or v!=d or type(v) is not type(d):out[k]=v
    return out

def validate_call(name,args=None):
    spec=TOOLS_BY_NAME.get(name)
    if spec is None:return False,f"unknown tool '{name}'; known tools: {', '.join(tool_names())}"
    a=args or {}
    p=spec.params
    unknown=[k for k in a if k not in p]
    if unknown:return False,f"unknown argument(s) for {name}: {', '.join(sorted(unknown))}"
    out={}
    c=_coerce
    for k,q in p.items():
        v=a.get(k)
        if v is None:
            if q.get("required"):return False,f"{name} is missing required argument '{k}'"
            d=q.get("default")
            if d is not None:out[k]=d
            continue
        ok,r=c(q["type"],v)
        if not ok:return False,f"{name}.{k}: {r}"
        e=q.get("enum")
        if e is not None and r not in e:return False,f"{name}.{k}: must be one of {', '.join(map(str,e))}"
        out[k]=r
    e=_special_rules(name,out)
    if e:return False,e
    return True,out
