import math
from collections import deque
from dataclasses import dataclass,field
from functools import cached_property,lru_cache
from typing import Any,Dict,FrozenSet,List,Optional,Tuple
from combat.damage_calculator import CharacterStats,DofusDamageCalculator,SpellDamageResult,TargetStats
from data.game_data_extractor import DamageEffect,SpellInfo
NB_CELLS=560
ELEMENT_BY_EFFECT:Dict[int,Tuple[str,str]]={91:('steal','WATER'),92:('steal','EARTH'),93:('steal','AIR'),94:('steal','FIRE'),95:('steal','NEUTRAL'),96:('damage','WATER'),97:('damage','EARTH'),98:('damage','AIR'),99:('damage','FIRE'),100:('damage','NEUTRAL'),108:('heal','HEAL'),143:('heal','HEAL')}
HEAL_EFFECT_IDS=frozenset({81,108,143})
UTILITY_EFFECT_KINDS={4:'teleport',5:'push',6:'pull',8:'swap',50:'carry',84:'ap_steal',101:'ap_loss',102:'ap_use',104:'tackled',111:'ap_gain',120:'ap_gain',128:'mp_gain',132:'cleanse',133:'ap_loss',168:'ap_loss',169:'mp_loss',1041:'pushed',1042:'pulled'}
PLANNED_MECHANIC_KINDS=frozenset({'teleport','push','pull','ap_loss','ap_steal','mp_loss','ap_gain','mp_gain'})
AP_DENIAL_WEIGHT=6.0
MP_DENIAL_WEIGHT=3.0
AP_GAIN_WEIGHT=5.0
MP_GAIN_WEIGHT=1.5
PUSH_WEIGHT=0.75
TELEPORT_POSITIONS=8
_EPS=1e-09
_CP=tuple(((c % 14 - c // 28 + 19,c % 14 - c // 28 + c // 14) for c in range(NB_CELLS)))

def cell_point(cell_id):
    if type(cell_id) is int and 0 <= cell_id < NB_CELLS:
        return _CP[cell_id]
    t=cell_id % 14 - cell_id // 28
    return (t + 19,t + cell_id // 14)
_POINTS:Dict[Tuple[int,int],int]={p:c for c,p in enumerate(_CP)}
_CARDINAL_DIRS=((0,1),(-1,0),(0,-1),(1,0))
_DIAGONAL_DIRS=((1,1),(-1,1),(-1,-1),(1,-1))
_LINE_OFFSETS=((-1,0),(1,0),(0,-1),(0,1))
_DIAG_OFFSETS=((-1,-1),(-1,1),(1,-1),(1,1))
_LINE_DIAG_OFFSETS=_LINE_OFFSETS + _DIAG_OFFSETS

def _neighbour_table(diagonal):
    table={}
    dirs=_CARDINAL_DIRS + (_DIAGONAL_DIRS if diagonal else ())
    get=_POINTS.get
    for cell in range(NB_CELLS):
        x,y=_CP[cell]
        table[cell]=tuple((n for dx,dy in dirs if (n := get((x + dx,y + dy))) is not None))
    return table
_CARDINAL_NEIGHBOURS=_neighbour_table(False)
_DIAGONAL_NEIGHBOURS=_neighbour_table(True)

def _js_round(value):
    return math.floor(value + 0.5)

@lru_cache(maxsize=256)
def _diamond(radius):
    out=[]
    for dx in range(-radius,radius + 1):
        dy=radius - abs(dx)
        out.append((dx,-dy))
        if dy:
            out.append((dx,dy))
    return tuple(out)

@lru_cache(maxsize=16384)
def line_points(x1,y1,x2,y2):
    out=[]
    sx=x1 + 0.5
    sy=y1 + 0.5
    cx=x2 + 0.5
    cy=y2 + 0.5
    adx=abs(sx - cx)
    ady=abs(sy - cy)
    if adx == ady:
        dx=1 if cx > sx else -1
        dy=1 if cy > sy else -1
        for _ in range(int(adx)):
            out.append((math.floor(sx + dx),math.floor(sy + dy)))
            sx += dx
            sy += dy
        return out
    along_x=adx > ady
    if along_x:
        steps=int(adx)
        pa=sx
        ea=cx
        pb=sy
        eb=cy
    else:
        steps=int(ady)
        pa=sy
        ea=cy
        pb=sx
        eb=cx
    sa=1.0 if ea >= pa else -1.0
    sb=abs(pb - eb) / steps if eb > pb else -abs(pb - eb) / steps
    floor=math.floor
    ceil=math.ceil
    for _ in range(steps):
        low=_js_round(10000.0 * pb + 5000.0 * sb) / 10000.0
        high=_js_round(10000.0 * pb + 15000.0 * sb) / 10000.0
        if floor(low) == floor(high):
            v=floor(pb + sb)
            if low == v and high < v:
                v=ceil(pb + sb)
            if high == v and low < v:
                v=ceil(pb + sb)
            cells=(v,)
        elif ceil(low) == ceil(high):
            v=ceil(pb + sb)
            if low == v and high < v:
                v=floor(pb + sb)
            if high == v and low < v:
                v=floor(pb + sb)
            cells=(v,)
        else:
            cells=(floor(low),floor(high))
        q=floor(pa + sa)
        if along_x:
            out.append((q,cells[0]))
            if len(cells) > 1:
                out.append((q,cells[1]))
        else:
            out.append((cells[0],q))
            if len(cells) > 1:
                out.append((cells[1],q))
        pa += sa
        pb += sb
    return out

@lru_cache(maxsize=16384)
def _los_cells(a,b):
    ax,ay=cell_point(a)
    bx,by=cell_point(b)
    get=_POINTS.get
    return tuple((get(p) for p in line_points(ax,ay,bx,by)))

@lru_cache(maxsize=4096)
def _range_ring(limit,origin,min_range,max_range,cast_in_line,cast_in_diagonal):
    if max_range < 0 or min_range > max_range:
        return frozenset()
    x,y=cell_point(origin)
    limit=min(limit,NB_CELLS)
    out=[]
    add=out.append
    get=_POINTS.get
    if min_range == 0:
        cell=get((x,y))
        if cell is not None and cell < limit:
            add(cell)
    start=max(1,min_range)
    if cast_in_line or cast_in_diagonal:
        if cast_in_line and cast_in_diagonal:
            offsets=_LINE_DIAG_OFFSETS
        elif cast_in_line:
            offsets=_LINE_OFFSETS
        else:
            offsets=_DIAG_OFFSETS
        for radius in range(start,max_range + 1):
            for dx,dy in offsets:
                cell=get((x + dx * radius,y + dy * radius))
                if cell is not None and cell < limit:
                    add(cell)
    else:
        for radius in range(start,max_range + 1):
            for dx,dy in _diamond(radius):
                cell=get((x + dx,y + dy))
                if cell is not None and cell < limit:
                    add(cell)
    return frozenset(out)

@dataclass(frozen=True)
class CellMap:
    flags:Tuple[int,...]

    @classmethod
    def from_board(cls,payload):
        raw=payload.get('cells') or []
        if all((type(v) is int for v in raw)):
            return cls(tuple(raw))
        return cls(tuple((int(v) if isinstance(v,(int,float)) else 0 for v in raw)))

    def valid(self,cell):
        return 0 <= cell < len(self.flags) and cell < NB_CELLS

    def walkable(self,cell):
        return self.valid(cell) and self.flags[cell] & 5 == 1

    def sees(self,cell):
        return self.valid(cell) and self.flags[cell] & 2 == 2

    def distance(self,a,b):
        ax,ay=cell_point(a)
        bx,by=cell_point(b)
        return abs(ax - bx) + abs(ay - by)

    def neighbours(self,cell,diagonal=False):
        table=_DIAGONAL_NEIGHBOURS if diagonal else _CARDINAL_NEIGHBOURS
        return [c for c in table.get(cell,()) if self.valid(c)]

    def has_los(self,a,b,occupied=frozenset()):
        if a == b:
            return True
        cells=_los_cells(a,b)
        if not cells:
            return True
        flags=self.flags
        limit=min(len(flags),NB_CELLS)
        last=len(cells) - 1
        for index,cell in enumerate(cells):
            if cell is None or cell >= limit or flags[cell] & 2 != 2:
                return False
            if index < last and cell in occupied:
                return False
        return True

    def slide(self,origin,target,distance,blocked=frozenset(),toward=False):
        if distance <= 0 or origin == target:
            return target
        ox,oy=cell_point(origin)
        tx,ty=cell_point(target)
        dx=0 if tx == ox else 1 if tx > ox else -1
        dy=0 if ty == oy else 1 if ty > oy else -1
        if toward:
            dx=-dx
            dy=-dy
        if dx == 0 and dy == 0:
            return target
        cell=target
        x=tx
        y=ty
        flags=self.flags
        limit=min(len(flags),NB_CELLS)
        get=_POINTS.get
        for _ in range(int(distance)):
            x += dx
            y += dy
            nxt=get((x,y))
            if nxt is None or nxt >= limit or flags[nxt] & 5 != 1:
                break
            if nxt == origin or nxt in blocked:
                break
            cell=nxt
        return cell

    def reachable(self,start,mp,occupied=frozenset()):
        if not self.walkable(start):
            return {}
        flags=self.flags
        limit=min(len(flags),NB_CELLS)
        occ=occupied
        best={start:0.0}
        queue=deque([start])
        pop=queue.popleft
        push=queue.append
        table=_CARDINAL_NEIGHBOURS
        stop=mp + _EPS
        while queue:
            cell=pop()
            cost=best[cell]
            if cost + 1.0 > stop:
                continue
            nxt_cost=cost + 1.0
            for nxt in table.get(cell,()):
                if nxt in best:
                    continue
                if nxt >= limit or flags[nxt] & 5 != 1 or nxt in occ:
                    continue
                best[nxt]=nxt_cost
                push(nxt)
        return best

    def range_cells(self,origin,min_range,max_range,cast_in_line=False,cast_in_diagonal=False):
        return _range_ring(len(self.flags),origin,min_range,max_range,cast_in_line,cast_in_diagonal)

@dataclass(frozen=True)
class LiveEffect:
    effect_id:int
    min_roll:int
    max_roll:int
    target_id:Optional[int]=None
    duration:Optional[int]=None

    @cached_property
    def kind(self):
        if self.effect_id in HEAL_EFFECT_IDS:
            return 'heal'
        entry=ELEMENT_BY_EFFECT.get(self.effect_id)
        if entry:
            return entry[0]
        return UTILITY_EFFECT_KINDS.get(self.effect_id,'other')

    @cached_property
    def element(self):
        if self.effect_id in HEAL_EFFECT_IDS:
            return 'HEAL'
        entry=ELEMENT_BY_EFFECT.get(self.effect_id)
        return entry[1] if entry else None

@dataclass(frozen=True)
class LiveSpell:
    id:int
    name:str
    ap_cost:int
    min_range:int
    max_range:int
    cast_in_line:bool
    cast_in_diagonal:bool
    requires_los:bool
    need_free_cell:bool
    need_taken_cell:bool
    max_cast_per_turn:Optional[int]
    max_cast_per_target:Optional[int]
    min_cast_interval:int
    casts_this_turn:int
    castable:bool
    critical_hit_probability:Optional[float]
    effects:Tuple[LiveEffect,...]
    critical_effects:Tuple[LiveEffect,...]
    is_weapon:bool=False
    zone_size:Optional[int]=None
    zone_shape:Optional[str]=None

    @cached_property
    def damage_effects(self):
        return tuple((e for e in self.effects if e.kind in ('damage','steal')))

    @cached_property
    def heal_effects(self):
        return tuple((e for e in self.effects if e.kind == 'heal'))

    @cached_property
    def mechanic_effects(self):
        return tuple((e for e in self.effects if e.kind in PLANNED_MECHANIC_KINDS))

    @cached_property
    def has_mechanic(self):
        return bool(self.mechanic_effects)

    @cached_property
    def is_teleport(self):
        return any((e.kind == 'teleport' for e in self.effects))

    @cached_property
    def is_steal(self):
        return any((e.kind == 'steal' for e in self.effects))

    @cached_property
    def mechanic_rolls(self):
        ap_gain=mp_gain=ap_loss=mp_loss=push=pull=0
        for effect in self.effects:
            kind=effect.kind
            low=max(0,effect.min_roll)
            high=max(low,effect.max_roll)
            value=int(round((low + high) / 2.0))
            if kind == 'ap_steal':
                ap_gain += value
                ap_loss += value
            elif kind == 'ap_gain':
                ap_gain += value
            elif kind == 'ap_loss':
                ap_loss += value
            elif kind == 'mp_gain':
                mp_gain += value
            elif kind == 'mp_loss':
                mp_loss += value
            elif kind == 'push':
                push += value
            elif kind == 'pull':
                pull += value
        return (ap_gain,mp_gain,ap_loss,mp_loss,push,pull)

    def _damage_info(self,wanted):
        normal=[e for e in self.effects if e.kind in wanted]
        if not normal:
            return None
        critical={}
        for effect in self.critical_effects:
            if effect.kind in wanted:
                critical.setdefault(effect.element or 'HEAL',[]).append(effect)
        damage_effects=[]
        for effect in normal:
            pool=critical.get(effect.element or 'HEAL') or []
            crit=pool.pop(0) if pool else None
            damage_effects.append(DamageEffect(element=effect.element or 'NEUTRAL',min_roll=effect.min_roll,max_roll=effect.max_roll,crit_min_roll=crit.min_roll if crit else 0,crit_max_roll=crit.max_roll if crit else 0))
        return SpellInfo(id=self.id,name_fr=self.name,name_en=self.name,name_es='',breed_id=0,level=1,ap_cost=max(1,self.ap_cost),min_range=self.min_range,max_range=self.max_range,cast_in_line=self.cast_in_line,cast_in_diagonal=self.cast_in_diagonal,cast_test_los=self.requires_los,effects=damage_effects,crit_rate_percent=self.critical_hit_probability or 0.0)

    @cached_property
    def damage_info(self):
        return self._damage_info(('damage','steal'))

    @cached_property
    def heal_info(self):
        return self._damage_info(('heal',))

    @cached_property
    def has_damage(self):
        return bool(self.damage_effects)

    @cached_property
    def has_heal(self):
        return bool(self.heal_effects)

@dataclass(frozen=True)
class Fighter:
    id:int
    name:str
    cell:int
    team:str
    hp:int
    max_hp:int
    ap:int
    mp:int
    shield:int
    level:int
    alive:bool
    by_me:bool
    resist_percent:Tuple[Tuple[str,int],...]=()
    resist_fixed:Tuple[Tuple[str,int],...]=()
    critical_resistance:int=0
    initiative:int=0
    max_ap:int=0
    max_mp:int=0
    states:Tuple[Any,...]=()
    is_boss:bool=False
    is_summon:bool=False
    is_companion:bool=False
    summoner_id:Optional[int]=None
    creature_generic_id:Optional[int]=None
    spells:Tuple[LiveSpell,...]=()

    @cached_property
    def _percent_map(self):
        return dict(self.resist_percent)

    @cached_property
    def _fixed_map(self):
        return dict(self.resist_fixed)

    def _percent(self,element):
        return self._percent_map.get(element.lower(),0)

    def _fixed(self,element):
        return self._fixed_map.get(element.lower(),0)

    def target_stats(self):
        return self._target_stats

    @cached_property
    def _target_stats(self):
        return TargetStats(name=self.name or str(self.id),max_hp=self.max_hp,current_hp=self.hp,level=self.level,res_percent_neutral=self._percent('neutral'),res_percent_earth=self._percent('earth'),res_percent_fire=self._percent('fire'),res_percent_water=self._percent('water'),res_percent_air=self._percent('air'),res_fixed_neutral=self._fixed('neutral'),res_fixed_earth=self._fixed('earth'),res_fixed_fire=self._fixed('fire'),res_fixed_water=self._fixed('water'),res_fixed_air=self._fixed('air'),armor_shield=self.shield,res_fixed_critical=self.critical_resistance,is_player=self.team == 'player')

def enemy_character(fighter):
    return CharacterStats(name=fighter.name or str(fighter.id),level=max(1,int(fighter.level)),action_points=max(0,int(fighter.ap)),movement_points=max(0,int(fighter.mp)))

@dataclass
class Board:
    map_id:Optional[int]
    in_fight:bool
    in_preparation:bool
    is_my_turn:bool
    my_id:int
    my_cell:int
    my_ap:int
    my_mp:int
    fighters:List[Fighter]
    spells:List[LiveSpell]
    my_stats:Dict[str,Any]
    cells:CellMap
    round:Optional[int]=None
    fight_id:Optional[int]=None
    current_fighter_id:Optional[int]=None
    last_actions:Tuple[Dict[str,Any],...]=()
    _character_stats_cache:Optional[CharacterStats]=field(default=None,init=False,repr=False,compare=False)

    @classmethod
    def from_fight_state(cls,payload):
        fighters=[f for f in map(cls._fighter,payload.get('fighters') or []) if f is not None]
        my_id=_int(payload.get('my_id'))
        me=next((f for f in fighters if f.id == my_id),None) if my_id is not None else None
        if me is None:
            me=next((f for f in fighters if f.by_me),None)
        my_cell=me.cell if me is not None else _int(payload.get('my_cell'),0)
        return cls(map_id=_int(payload.get('map_id')),in_fight=bool(payload.get('in_fight')),in_preparation=bool(payload.get('in_preparation')),is_my_turn=bool(payload.get('is_my_turn')),my_id=my_id if my_id is not None else me.id if me else -1,my_cell=my_cell,my_ap=me.ap if me is not None else _int(payload.get('my_ap'),0),my_mp=me.mp if me is not None else _int(payload.get('my_mp'),0),fighters=fighters,spells=[cls._spell(raw) for raw in payload.get('spells') or []],my_stats=dict(payload.get('my_stats') or {}),cells=CellMap.from_board(payload),round=_int(payload.get('round')),fight_id=_int(payload.get('fight_id')),current_fighter_id=_int(payload.get('current_fighter_id')),last_actions=tuple((action for action in payload.get('last_actions') or [] if isinstance(action,dict))))

    @staticmethod
    def _fighter(raw):
        cell=_int(raw.get('cell'))
        if cell is None:
            return None
        team=str(raw.get('team') or 'enemy')
        return Fighter(id=_int(raw.get('id'),-1),name=str(raw.get('name') or ''),cell=cell,team=team,hp=_int(raw.get('hp'),0),max_hp=_int(raw.get('max_hp'),0),ap=_int(raw.get('ap'),0),mp=_int(raw.get('mp'),0),shield=_int(raw.get('shield'),0),level=_int(raw.get('level'),1),alive=bool(raw.get('alive',True)),by_me=bool(raw.get('is_me')),resist_percent=tuple(sorted(((str(k),_int(v)) for k,v in (raw.get('resist_percent') or {}).items()))),resist_fixed=tuple(sorted(((str(k),_int(v)) for k,v in (raw.get('resist_fixed') or {}).items()))),critical_resistance=_int(raw.get('critical_resistance'),0),initiative=_int(raw.get('initiative'),0) or 0,max_ap=_int(raw.get('max_ap'),0) or 0,max_mp=_int(raw.get('max_mp'),0) or 0,states=tuple(raw.get('states') or ()),is_boss=bool(raw.get('is_boss')),is_summon=bool(raw.get('is_summon')),is_companion=bool(raw.get('is_companion')),summoner_id=_int(raw.get('summoner_id')),creature_generic_id=_int(raw.get('creature_generic_id')),spells=tuple((Board._spell(spell) for spell in raw.get('spells') or [] if isinstance(spell,dict))))

    @staticmethod
    def _spell(raw):
        return LiveSpell(id=_int(raw.get('id'),-1),name=str(raw.get('name') or ''),ap_cost=_int(raw.get('ap_cost'),0),min_range=_int(raw.get('min_range'),0),max_range=_int(raw.get('range'),0),cast_in_line=bool(raw.get('cast_in_line')),cast_in_diagonal=bool(raw.get('cast_in_diagonal')),requires_los=bool(raw.get('requires_los',True)),need_free_cell=bool(raw.get('need_free_cell')),need_taken_cell=bool(raw.get('need_taken_cell')),max_cast_per_turn=_limit(raw.get('max_cast_per_turn')),max_cast_per_target=_limit(raw.get('max_cast_per_target')),min_cast_interval=_int(raw.get('min_cast_interval'),0),casts_this_turn=_int(raw.get('casts_this_turn'),0),castable=bool(raw.get('castable',True)),critical_hit_probability=_float(raw.get('critical_hit_probability')),effects=_effects(raw.get('effects')),critical_effects=_effects(raw.get('critical_effects')),is_weapon=bool(raw.get('is_weapon')),zone_size=_int(raw.get('zone_size')) if raw.get('zone_size') is not None else None,zone_shape=raw.get('zone_shape') or None)

    @property
    def me(self):
        return next((f for f in self.fighters if f.id == self.my_id),None)

    @property
    def enemies(self):
        return [f for f in self.fighters if f.alive and f.id != self.my_id and (f.team != 'player')]

    @property
    def allies(self):
        return [f for f in self.fighters if f.alive and f.team == 'player']

    def character_stats(self):
        cached=self._character_stats_cache
        if cached is not None:
            return cached
        stats=self.my_stats or {}
        me=self.me

        def value(key):
            return _int(stats.get(key),0)
        result=CharacterStats(name=me.name if me else 'Player',level=me.level if me else 1,action_points=self.my_ap,movement_points=self.my_mp,strength=value('strength'),intelligence=value('intelligence'),chance=value('chance'),agility=value('agility'),power=value('power'),damage=value('damage'),damage_neutral=value('damage_neutral'),damage_earth=value('damage_earth'),damage_fire=value('damage_fire'),damage_water=value('damage_water'),damage_air=value('damage_air'),critical_hit=value('critical_hit'),damage_critical=value('damage_critical'),heals=value('heals'))
        self._character_stats_cache=result
        return result

def _int(value,default=None):
    if value is None or value == '':
        return default
    try:
        return int(float(value))
    except (TypeError,ValueError):
        return default

def _float(value):
    if value is None or value == '':
        return None
    try:
        return float(value)
    except (TypeError,ValueError):
        return None

def _limit(value):
    parsed=_int(value)
    return None if parsed is None or parsed <= 0 else parsed

def _roll(raw):
    low=_int(raw.get('dice_num'))
    high=_int(raw.get('dice_side'))
    if low is not None and low > 0:
        return (low,high if high else low)
    value=_int(raw.get('value'))
    if value is not None and value > 0:
        return (value,value)
    return None

def _effects(raw_list):
    out=[]
    for raw in raw_list or []:
        effect_id=_int(raw.get('effect_id'))
        if effect_id is None:
            continue
        roll=_roll(raw)
        out.append(LiveEffect(effect_id=effect_id,min_roll=roll[0] if roll else 0,max_roll=roll[1] if roll else 0,target_id=_int(raw.get('target_id')),duration=_int(raw.get('duration'))))
    return tuple(out)

@dataclass(frozen=True,slots=True)
class PlannedAction:
    kind:str
    spell_id:int=0
    spell_name:str=''
    from_cell:int=0
    move_to:Optional[int]=None
    target_id:Optional[int]=None
    target_cell:Optional[int]=None
    mp_cost:float=0.0
    ap_cost:int=0
    damage:float=0.0
    healing:float=0.0
    kills:bool=False
    moved_target:Optional[int]=None
    moved:int=0

    def as_dict(self):
        return {'kind':self.kind,'spell_id':self.spell_id,'spell_name':self.spell_name,'from_cell':self.from_cell,'move_to':self.move_to,'target_id':self.target_id,'target_cell':self.target_cell,'mp_cost':round(self.mp_cost,3),'ap_cost':self.ap_cost,'damage':round(self.damage,2),'healing':round(self.healing,2),'kills':self.kills,'moved_target':self.moved_target,'moved':self.moved}

@dataclass
class TurnPlan:
    actions:List[PlannedAction]
    final_cell:int
    remaining_ap:int
    remaining_mp:float
    expected_damage:float
    expected_healing:float
    kills:List[int]
    score:float
    notes:List[str]=field(default_factory=list)
    final_hp:Dict[int,int]=field(default_factory=dict)

    def as_dict(self):
        return {'actions':[a.as_dict() for a in self.actions],'final_cell':self.final_cell,'remaining_ap':self.remaining_ap,'remaining_mp':round(self.remaining_mp,3),'expected_damage':round(self.expected_damage,2),'expected_healing':round(self.expected_healing,2),'kills':self.kills,'score':round(self.score,2),'notes':self.notes,'final_hp':{str(k):int(v) for k,v in self.final_hp.items()}}

@dataclass(frozen=True,slots=True)
class _CastTemplate:
    spell:LiveSpell
    target_id:int
    target_cell:int
    damage:float
    healing:float
    steal_heal:float
    target_max_hp:int
    damage_int:int
    ap_gain:int=0
    mp_gain:int=0
    ap_loss:int=0
    mp_loss:int=0
    utility:float=0.0
    move_target:Optional[int]=None
    moved:int=0

@dataclass(slots=True)
class _Node:
    cell:int
    ap:int
    mp:float
    hp:Dict[int,int]
    spell_uses:Dict[int,int]
    target_uses:Dict[Tuple[int,int],int]
    healed_by_target:Dict[int,float]
    actions:List[PlannedAction]
    damage:float
    healing:float
    kills:List[int]
    score:float
    moved_targets:FrozenSet[int]=frozenset()
KILL_BONUS=0.35
HEAL_WEIGHT=0.8
CAST_POSITIONS=3
_EMPTY_FROZENSET:FrozenSet[Any]=frozenset()

class BattleSolver:

    def __init__(self,beam_width=16,max_actions=4,max_nodes=20000):
        self.beam_width=max(1,beam_width)
        self.max_actions=max(0,max_actions)
        self.max_nodes=max(1,max_nodes)
        self.last_candidates=[]

    def plan_candidates(self,board,k=5):
        self.plan_turn(board)
        plans=self.last_candidates
        pass_plan=next((plan for plan in plans if plan.actions and plan.actions[0].kind == 'pass'),None)
        if pass_plan is None:
            pass_plan=self._pass_plan(board,[])
        action_plans=[plan for plan in plans if plan.actions and plan.actions[0].kind != 'pass']
        limit=max(1,int(k))
        return (action_plans[:max(0,limit - 1)] + [pass_plan])[:limit]

    def _pass(self,board,notes):
        plan=self._pass_plan(board,notes)
        self.last_candidates=[plan]
        return plan

    def plan_turn(self,board):
        self.last_candidates=[]
        notes=[]
        if not board.in_fight or board.in_preparation or (not board.is_my_turn):
            return self._pass(board,notes + ["not the controlled character's turn"])
        me=board.me
        if me is None:
            return self._pass(board,notes + ['controlled fighter is missing from the board'])
        if not board.cells.valid(me.cell):
            return self._pass(board,notes + ['no cell flags for this map'])
        if board.my_ap <= 0:
            return self._pass(board,notes + ['no AP left'])
        skipped=sorted({s.name or str(s.id) for s in board.spells if not s.has_damage and (not s.has_heal) and (not s.has_mechanic)})
        if skipped:
            notes.append('not modelled yet (buff/utility/area only): ' + ', '.join(skipped))
        blocked=sorted({s.name or str(s.id) for s in board.spells if not s.castable})
        if blocked:
            notes.append('client marks as not castable: ' + ', '.join(blocked))
        occupied=frozenset((f.cell for f in board.fighters if f.alive and f.id != me.id))
        template_cache={}
        reach_cache={}
        los_cache={}
        estimate_cache={}
        slide_cache={}
        character=board.character_stats()
        targets=[f for f in board.fighters if f.alive]
        melee=any((spell.has_damage and spell.max_range <= 1 for spell in board.spells))

        def templates(cell):
            out=template_cache.get(cell)
            if out is None:
                out=self._templates(board,cell,character,occupied,los_cache,estimate_cache,slide_cache,melee,me=me,targets=targets)
                template_cache[cell]=out
            return out

        def reach(cell,mp):
            key=(cell,round(mp,3))
            out=reach_cache.get(key)
            if out is None:
                out=reach_cache[key]=board.cells.reachable(cell,mp,occupied)
            return out
        teleport_spells=[spell for spell in board.spells if spell.is_teleport and spell.castable and (spell.ap_cost > 0) and (not spell.need_taken_cell)]
        cast_ranges=[(spell.min_range,spell.max_range) for spell in board.spells if (spell.has_damage or spell.has_heal) and spell.castable]
        fighter_cells=[f.cell for f in board.fighters if f.alive]
        fighter_spells={spell.id for spell in board.spells if spell.castable and (not spell.is_teleport) and (not spell.need_free_cell) and (spell.has_damage or spell.has_heal or spell.has_mechanic)}
        enemy_ids={f.id for f in board.fighters if f.alive and f.team != 'player' and (f.id != me.id)}
        teleport_cache={}

        def teleports(cell):
            cached=teleport_cache.get(cell)
            if cached is not None:
                return cached
            cells=board.cells
            distance=cells.distance
            options={}
            for spell in teleport_spells:
                for dest in cells.range_cells(cell,spell.min_range,spell.max_range,spell.cast_in_line,spell.cast_in_diagonal):
                    if dest == cell or dest in occupied or (not cells.walkable(dest)):
                        continue
                    if not any((low <= distance(dest,target_cell) <= high for low,high in cast_ranges for target_cell in fighter_cells)):
                        continue
                    if spell.requires_los and (not cells.has_los(cell,dest,occupied)):
                        continue
                    current=options.get(dest)
                    if current is None or spell.ap_cost < current.ap_cost:
                        options[dest]=spell
            if len(options) > TELEPORT_POSITIONS:
                nearest=sorted(options,key=lambda dest:min((distance(dest,target_cell) for target_cell in fighter_cells)))
                options={dest:options[dest] for dest in nearest[:TELEPORT_POSITIONS]}
            result=list(options.items())
            teleport_cache[cell]=result
            return result

        def teleport_ready(spell,node):
            uses=spell.casts_this_turn + node.spell_uses.get(spell.id,0)
            if spell.max_cast_per_turn is not None and uses >= spell.max_cast_per_turn:
                return False
            if spell.min_cast_interval > 0 and uses > 0:
                return False
            return True
        bucket_cache={}
        hp={f.id:f.hp for f in board.fighters if f.alive}
        start=_Node(cell=me.cell,ap=board.my_ap,mp=float(board.my_mp),hp=hp,spell_uses={},target_uses={},healed_by_target={},actions=[],damage=0.0,healing=0.0,kills=[],score=0.0)
        best=start
        frontier=[start]
        seen={}
        expanded=0
        reps_per_cast=max(1,min(CAST_POSITIONS + max(0,self.beam_width - 8) // 2,CAST_POSITIONS + 3))
        for _depth in range(self.max_actions):
            candidates=[]
            for node in frontier:
                if expanded >= self.max_nodes:
                    notes.append('search stopped at the node budget')
                    break
                expanded += 1
                walked=reach(node.cell,node.mp)
                representatives=bucket_cache.get((node.cell,round(node.mp,3)))
                if representatives is None:
                    by_cast={}
                    for index,(cell,mp_cost) in enumerate(walked.items()):
                        for template in templates(cell):
                            key=(template.spell.id,template.target_id)
                            bucket=by_cast.get(key)
                            if bucket is None:
                                by_cast[key]=[(mp_cost,index,cell,template)]
                            elif len(bucket) < reps_per_cast:
                                bucket.append((mp_cost,index,cell,template))
                    representatives=[]
                    extend=representatives.extend
                    for bucket in by_cast.values():
                        extend(((mp_cost,index,cell,template,None) for mp_cost,index,cell,template in bucket))
                    representatives.sort(key=lambda item:item[1])
                    bucket_cache[node.cell,round(node.mp,3)]=representatives
                if teleport_spells and node.ap > 1 and (_depth == 0):
                    walked_targets=set()
                    walked_spells=set()
                    for _mp,_idx,_cell,template,_tele in representatives:
                        walked_targets.add(template.target_id)
                        walked_spells.add(template.spell.id)
                    if not (enemy_ids <= walked_targets and fighter_spells <= walked_spells):
                        covered={(t.spell.id,t.target_id) for _a,_b,_c,t,_d in representatives}
                        extra=[]
                        for dest,tele in teleports(node.cell):
                            if dest in walked:
                                continue
                            if tele.ap_cost >= node.ap or not teleport_ready(tele,node):
                                continue
                            for template in templates(dest):
                                key=(template.spell.id,template.target_id)
                                if key in covered:
                                    continue
                                covered.add(key)
                                extra.append((0.0,1 << 30,dest,template,tele))
                        if extra:
                            representatives=representatives + extra
                for mp_cost,_index,cell,template,tele in representatives:
                    target_id=template.target_id
                    if target_id in node.moved_targets:
                        continue
                    target_hp=node.hp[target_id]
                    if target_hp <= 0:
                        continue
                    spell=template.spell
                    ap_cost=spell.ap_cost + (tele.ap_cost if tele is not None else 0)
                    if ap_cost > node.ap:
                        continue
                    uses_now=spell.casts_this_turn + node.spell_uses.get(spell.id,0)
                    if spell.max_cast_per_turn is not None and uses_now >= spell.max_cast_per_turn:
                        continue
                    if spell.min_cast_interval > 0 and uses_now > 0:
                        continue
                    key_uses=(spell.id,target_id)
                    if spell.max_cast_per_target is not None and node.target_uses.get(key_uses,0) >= spell.max_cast_per_target:
                        continue
                    dealt=min(template.damage,target_hp)
                    kill=template.damage >= target_hp
                    direct_gain=0.0
                    if template.healing != 0.0:
                        direct_gain=min(template.healing,max(0,template.target_max_hp - target_hp - node.healed_by_target.get(target_id,0)))
                    steal_gain=0.0
                    if template.steal_heal > 0:
                        steal_gain=min(template.steal_heal,max(0,me.max_hp - me.hp - node.healed_by_target.get(me.id,0)))
                    gained=direct_gain + steal_gain
                    step_score=dealt + (KILL_BONUS * template.target_max_hp if kill else 0.0)
                    step_score += HEAL_WEIGHT * gained + template.utility
                    if step_score <= 0:
                        continue
                    if template.damage_int > 0:
                        new_hp=dict(node.hp)
                        new_hp[target_id]=max(0,target_hp - template.damage_int)
                    else:
                        new_hp=node.hp
                    if direct_gain > 0 or steal_gain > 0:
                        healed_by_target=dict(node.healed_by_target)
                        if direct_gain > 0:
                            healed_by_target[target_id]=healed_by_target.get(target_id,0) + direct_gain
                        if steal_gain > 0:
                            healed_by_target[me.id]=healed_by_target.get(me.id,0) + steal_gain
                    else:
                        healed_by_target=node.healed_by_target
                    uses=dict(node.spell_uses)
                    uses[spell.id]=uses.get(spell.id,0) + 1
                    if tele is not None:
                        uses[tele.id]=uses.get(tele.id,0) + 1
                    target_uses=dict(node.target_uses)
                    target_uses[key_uses]=target_uses.get(key_uses,0) + 1
                    kills=node.kills if not kill else node.kills + [target_id]
                    action=PlannedAction(kind='cast',spell_id=spell.id,spell_name=spell.name,from_cell=cell,move_to=cell if cell != node.cell and tele is None else None,target_id=target_id,target_cell=template.target_cell,mp_cost=mp_cost,ap_cost=spell.ap_cost,damage=template.damage,healing=gained,kills=kill,moved_target=template.move_target,moved=template.moved)
                    actions=node.actions
                    if tele is not None:
                        actions=actions + [PlannedAction(kind='cast',spell_id=tele.id,spell_name=tele.name,from_cell=node.cell,target_id=me.id,target_cell=cell,ap_cost=tele.ap_cost)]
                    next_ap=node.ap - ap_cost + template.ap_gain
                    if me.max_ap > 0:
                        next_ap=min(next_ap,me.max_ap)
                    next_mp=max(0.0,node.mp - mp_cost + template.mp_gain)
                    if me.max_mp > 0:
                        next_mp=min(next_mp,float(me.max_mp))
                    candidate=_Node(cell=cell,ap=next_ap,mp=next_mp,hp=new_hp,spell_uses=uses,target_uses=target_uses,healed_by_target=healed_by_target,actions=actions + [action],damage=node.damage + dealt,healing=node.healing + gained,kills=kills,score=node.score + step_score,moved_targets=node.moved_targets | {target_id} if template.moved > 0 else node.moved_targets)
                    if _depth:
                        signature=(candidate.cell,candidate.ap,candidate.mp,tuple(candidate.hp.values()),frozenset(candidate.spell_uses.items()),frozenset(candidate.target_uses.items()),frozenset(candidate.healed_by_target.items()) if candidate.healed_by_target else _EMPTY_FROZENSET,candidate.moved_targets if candidate.moved_targets else _EMPTY_FROZENSET)
                        if signature in seen and seen[signature] >= candidate.score - _EPS:
                            continue
                        seen[signature]=candidate.score
                    candidates.append(candidate)
                    if candidate.score > best.score + _EPS:
                        best=candidate
            if not candidates:
                break
            candidates.sort(key=lambda n:(-n.score,n.ap,-n.mp,len(n.actions)))
            frontier=candidates[:self.beam_width]
        if not best.actions:
            return self._pass(board,notes)
        plan=self._to_plan(best,notes)
        nodes=[best] + [node for node in frontier if node.actions]
        plans=[]
        seen_plans=set()
        for node in sorted(nodes,key=lambda item:(-item.score,item.ap,-item.mp)):
            signature=tuple(((action.kind,action.move_to,action.spell_id,action.target_id) for action in node.actions))
            if signature in seen_plans:
                continue
            seen_plans.add(signature)
            plans.append(self._to_plan(node,notes))
        plans.append(self._pass_plan(board,notes))
        self.last_candidates=plans
        return plan

    def _templates(self,board,cell,character,occupied,los_cache=None,estimate_cache=None,slide_cache=None,melee=False,me=None,targets=None):
        me=me if me is not None else board.me
        assert me is not None
        targets=targets if targets is not None else [f for f in board.fighters if f.alive]
        out=[]
        cells=board.cells
        if not cells.walkable(cell):
            return out
        px,py=cell_point(cell)
        distances=[]
        for target in targets:
            tx,ty=cell_point(target.cell)
            distances.append(abs(px - tx) + abs(py - ty))
        my_id=board.my_id
        estimate=self._estimate
        for spell in board.spells:
            if spell.ap_cost <= 0 or spell.ap_cost > board.my_ap:
                continue
            if not spell.castable:
                continue
            if spell.max_cast_per_turn is not None and spell.casts_this_turn >= spell.max_cast_per_turn:
                continue
            if spell.min_cast_interval > 0 and spell.casts_this_turn > 0:
                continue
            has_damage=spell.has_damage
            has_heal=spell.has_heal
            if not has_damage and (not has_heal) and (not spell.has_mechanic):
                continue
            if spell.is_teleport or spell.need_free_cell:
                continue
            ap_gain,mp_gain,ap_loss,mp_loss,push_roll,pull_roll=spell.mechanic_rolls
            if (ap_gain or mp_gain) and (not has_damage) and (not has_heal) and (spell.min_range == 0) and (spell.max_range == 0):
                out.append(_CastTemplate(spell=spell,target_id=me.id,target_cell=cell,damage=0.0,healing=0.0,steal_heal=0.0,target_max_hp=max(1,me.max_hp),damage_int=0,ap_gain=ap_gain,mp_gain=mp_gain,utility=AP_GAIN_WEIGHT * ap_gain + MP_GAIN_WEIGHT * mp_gain))
                continue
            enemy_utility=bool(ap_loss or mp_loss or push_roll or pull_roll)
            is_steal=spell.is_steal
            needs_taken=spell.need_taken_cell
            if not any((distance <= spell.max_range for distance in distances)):
                continue
            in_range=cells.range_cells(cell,spell.min_range,spell.max_range,spell.cast_in_line,spell.cast_in_diagonal)
            if not in_range:
                continue
            for target,distance_to_target in zip(targets,distances):
                target_cell=target.cell
                if target_cell not in in_range:
                    continue
                if spell.requires_los:
                    pair=(cell,target_cell)
                    visible=los_cache.get(pair) if los_cache is not None else None
                    if visible is None:
                        visible=cells.has_los(cell,target_cell,occupied)
                        if los_cache is not None:
                            los_cache[pair]=visible
                    if not visible:
                        continue
                target_id=target.id
                is_enemy=target.team != 'player' and target_id != my_id
                if is_enemy:
                    if not has_damage and (not enemy_utility):
                        continue
                elif not has_heal:
                    continue
                damage=0.0
                healing=0.0
                steal_heal=0.0
                if is_enemy and has_damage:
                    key_damage=(spell.id,target_id,'damage')
                    if estimate_cache is not None and key_damage in estimate_cache:
                        result=estimate_cache[key_damage]
                    else:
                        result=estimate(board,spell,character,target,'damage')
                        if estimate_cache is not None:
                            estimate_cache[key_damage]=result
                    damage=result.expected_value if result else 0.0
                    if is_steal:
                        steal_heal=damage
                elif not is_enemy and has_heal:
                    key_heal=(spell.id,target_id,'heal')
                    if estimate_cache is not None and key_heal in estimate_cache:
                        result=estimate_cache[key_heal]
                    else:
                        result=estimate(board,spell,character,target,'heal')
                        if estimate_cache is not None:
                            estimate_cache[key_heal]=result
                    healing=result.expected_value if result else 0.0
                elif not is_enemy and target_id == me.id and has_damage:
                    continue
                if is_enemy:
                    cap_ap=target.ap if target.ap > 0 else max(0,target.max_ap)
                    cap_mp=target.mp if target.mp > 0 else max(0,target.max_mp)
                    target_ap_loss=min(ap_loss,cap_ap)
                    target_mp_loss=min(mp_loss,cap_mp)
                    target_ap_gain=0
                    target_mp_gain=0
                else:
                    target_ap_loss=0
                    target_mp_loss=0
                    target_ap_gain=ap_gain
                    target_mp_gain=mp_gain
                utility=AP_DENIAL_WEIGHT * target_ap_loss + MP_DENIAL_WEIGHT * target_mp_loss + AP_GAIN_WEIGHT * target_ap_gain + MP_GAIN_WEIGHT * target_mp_gain
                move_target=None
                moved=0
                if is_enemy and (push_roll or pull_roll):
                    key_slide=(spell.id,cell,target_id)
                    cached_slide=slide_cache.get(key_slide) if slide_cache is not None else None
                    if cached_slide is not None:
                        move_target,moved=cached_slide
                    else:
                        blocked=frozenset((f.cell for f in board.fighters if f.alive and f.id not in (me.id,target_id)))
                        before=distance_to_target
                        destination=cells.slide(cell,target_cell,push_roll or pull_roll,blocked,toward=bool(pull_roll))
                        after=cells.distance(cell,destination)
                        move_target=destination if destination != target_cell else None
                        moved=max(0,after - before if push_roll else before - after)
                        if slide_cache is not None:
                            slide_cache[key_slide]=(move_target,moved)
                    if moved:
                        if push_roll:
                            unrecovered=max(0,moved - max(0,target.mp))
                            utility += PUSH_WEIGHT * (moved + 0.5 * unrecovered)
                        elif melee:
                            utility += PUSH_WEIGHT * moved
                if damage <= 0 and healing <= 0 and (utility <= 0):
                    continue
                if needs_taken and target_cell not in occupied:
                    continue
                out.append(_CastTemplate(spell=spell,target_id=target_id,target_cell=target_cell,damage=damage,healing=healing,steal_heal=steal_heal,target_max_hp=max(1,target.max_hp),damage_int=int(round(damage)),ap_gain=target_ap_gain,mp_gain=target_mp_gain,ap_loss=target_ap_loss,mp_loss=target_mp_loss,utility=utility,move_target=move_target,moved=moved))
        return out

    @staticmethod
    def _estimate(board,spell,character,target,mode):
        info=spell.damage_info if mode == 'damage' else spell.heal_info
        if info is None:
            return None
        return DofusDamageCalculator.calculate_spell_damage(info,character,target.target_stats())

    @staticmethod
    def _to_plan(node,notes):
        return TurnPlan(actions=node.actions,final_cell=node.cell,remaining_ap=node.ap,remaining_mp=node.mp,expected_damage=node.damage,expected_healing=node.healing,kills=node.kills,score=node.score,notes=notes,final_hp=dict(node.hp))

    @staticmethod
    def _pass_plan(board,notes):
        me=board.me
        return TurnPlan(actions=[PlannedAction(kind='pass',from_cell=me.cell if me else board.my_cell)],final_cell=me.cell if me else board.my_cell,remaining_ap=board.my_ap,remaining_mp=float(board.my_mp),expected_damage=0.0,expected_healing=0.0,kills=[],score=0.0,notes=notes,final_hp={f.id:f.hp for f in board.fighters if f.alive})

def self_check():
    if len(_POINTS) != NB_CELLS:
        raise RuntimeError('cell point map has %d entries, expected %d' % (len(_POINTS),NB_CELLS))
    if cell_point(0) != (19,0) or cell_point(14) != (19,1):
        raise RuntimeError('cell point formula changed')
    open_map=CellMap(tuple([3] * NB_CELLS))
    if len(open_map.range_cells(300,1,3)) != 24:
        raise RuntimeError('diamond range ring is not 4*radius cells')
    if len(open_map.range_cells(300,1,3,cast_in_line=True)) != 12:
        raise RuntimeError('cross range ring is not 4 cells per radius')
    if len(open_map.range_cells(300,1,3,cast_in_diagonal=True)) != 12:
        raise RuntimeError('diagonal range ring is not 4 cells per radius')
    if len(open_map.reachable(300,1.0)) != 5:
        raise RuntimeError('fight movement must be cardinal, one MP per step')
    if not ELEMENT_BY_EFFECT or 100 not in ELEMENT_BY_EFFECT:
        raise RuntimeError('damage effect table is incomplete')
