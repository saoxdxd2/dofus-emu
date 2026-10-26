from __future__ import annotations
import json,logging,re,sys,threading,time,urllib.parse
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any,List
import torch
import torch.nn as nn
import torch.nn.functional as F
REPO_ROOT=Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:sys.path.insert(0,str(REPO_ROOT))
from networkhealth.inference import MultiInstanceNetworkHealth
from data.game_data_extractor import GameDataRepository,SpellInfo
from combat.damage_calculator import CharacterStats,DofusDamageCalculator,SpellRotationOptimizer,TargetStats
from combat.tactical_engine import TacticalCombatEngine
from combat.episodic_memory import EpisodicSkillMemory
from navigation.world_graph import DofusWorldGraph
from navigation.resource_graph import ResourceKnowledgeGraph
from navigation.quest_graph import QuestKnowledgeGraph
from navigation.nlp_intent_engine import DofusNLPEngine
logging.basicConfig(level=logging.INFO,format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger=logging.getLogger("sentinel.daemon")
CHECKPOINT_PATH=REPO_ROOT/"dofus-network"/"networkhealth_artifacts"/"model.pt"
SUGGESTED_REPLIES=["oui salut 2sec j'farm","oui ?","dsl groupe plein","non dsl je déco bientôt","je craft 2sec j'arrive","ouais dis moi ?"]
_MOD_TAG=re.compile(r"\[(?:Mod|Admin|GM|Ankama)\]",re.I)
_BOT_CHECK=re.compile(r"(?:bot|es[- ]tu l[àa]|t[' ]es l[àa]|r[ée]ponds|r[ée]pondez|ban|signal[ée]|v[ée]rif|afk|macro)",re.I)
_CHAR_FIELDS=frozenset(CharacterStats.__dataclass_fields__)
_TARGET_FIELDS=frozenset(TargetStats.__dataclass_fields__)


class TinyRecursiveReasoner(nn.Module):
    def __init__(self,in_dim=24,hidden_dim=64,max_recursions=4):
        super().__init__()
        self.in_proj=nn.Linear(in_dim,hidden_dim)
        self.ln_in=nn.LayerNorm(hidden_dim)
        self.gru=nn.GRUCell(hidden_dim,hidden_dim)
        self.ln_rec=nn.LayerNorm(hidden_dim)
        self.stance_head=nn.Linear(hidden_dim,4)
        self.halt_head=nn.Linear(hidden_dim,1)
        self.value_head=nn.Linear(hidden_dim,1)
        self.max_recursions=max_recursions
    def forward(self,x,max_steps=None):
        max_s=max_steps or self.max_recursions
        h=self.ln_in(F.gelu(self.in_proj(x)))
        x_proj=h
        all_logits,all_halts=[],[]
        for _ in range(max_s):
            h=self.ln_rec(self.gru(x_proj,h))
            all_logits.append(self.stance_head(h))
            all_halts.append(torch.sigmoid(self.halt_head(h)))
        return all_logits[-1],torch.stack(all_halts,dim=1),self.value_head(h)


class SingletonDecisionAgent:
    _instance=None
    _lock=threading.Lock()
    def __init__(self):
        self.agent=None;self.model=None;self.tokenizer=None;self.id2label={};self.combat_model=None;self.model_name="heuristic";self._load_attempted=False
        self._init_decision_agent()
    @classmethod
    def get_instance(cls):
        with cls._lock:
            if cls._instance is None:cls._instance=cls()
            return cls._instance
    def _init_decision_agent(self):
        if self._load_attempted:return
        self._load_attempted=True
        def _loader():
            try:
                d=Path(__file__).resolve().parent.parent/"models"/"dofus-thx01"
                if d.exists():
                    logger.info(f"Background loading distilled THX-01 ModernBERT Model from {d}...")
                    from transformers import AutoTokenizer,ModernBertForSequenceClassification,ModernBertConfig
                    import safetensors.torch
                    t=AutoTokenizer.from_pretrained(str(d));c=ModernBertConfig.from_pretrained(str(d));m=ModernBertForSequenceClassification(c)
                    m.load_state_dict(safetensors.torch.load_file(str(d/"model.safetensors"),device="cpu"),strict=False);m.eval()
                    with open(d/"labels.json","r",encoding="utf-8") as f:meta=json.load(f)
                    i2l={int(v):k for k,v in meta["label_map"].items()}
                    ci=None;tf=d/"trm_act_combat.pt"
                    if tf.exists():
                        try:
                            r=TinyRecursiveReasoner();r.load_state_dict(torch.load(str(tf),map_location="cpu",weights_only=False));r.eval();ci=r
                            logger.info("TRM-ACT Combat Reasoner neural weights loaded successfully.")
                        except Exception as c_err:
                            logger.warning(f"Could not load TRM-ACT weights: {c_err}")
                    with self._lock:
                        self.tokenizer=t;self.model=m;self.id2label=i2l;self.agent=m;self.combat_model=ci;self.model_name="THX-01-ModernBERT-Distilled"
                    logger.info("Singleton THX-01 model ready in RAM.")
            except Exception as ex:
                logger.warning(f"THX-01 background loading deferred or offline: {ex}. Calibrated heuristics active.")
        threading.Thread(target=_loader,daemon=True,name="DecisionAgentLoader").start()
    def classify_directive(self,text):
        if not self.model or not self.tokenizer or not self.id2label:return("NONE",0.0)
        try:
            inputs=self.tokenizer(text,return_tensors="pt",truncation=True,max_length=128)
            with torch.no_grad():
                probs=torch.softmax(self.model(**inputs).logits,dim=-1)[0]
                pid=int(torch.argmax(probs).item());label=self.id2label.get(pid,"NONE")
                return(label,float(probs[pid].item()))
        except Exception:return("NONE",0.0)
    def evaluate_message(self,sender,text,is_mod_tag=False):
        clean_text=text.strip();sender_clean=sender.strip()
        if is_mod_tag or _MOD_TAG.search(sender_clean):
            return {"is_threat":True,"threat_level":"CRITICAL_MODERATOR","confidence":0.99,"urgency":"CRITICAL","reason":"Official Ankama staff tag in sender name","suggested_replies":["Bonjour, oui je suis là !","Bonjour ?","Oui bonsoir"]}
        is_bot=bool(_BOT_CHECK.search(clean_text))
        if self.agent is not None:
            try:
                state=f"Sender: {sender_clean}\nMessage: {clean_text}"
                q={"is_threat":{"type":"choice","instructions":"Is this message a moderator check, bot suspicion, or player inquiry testing responsiveness?","criteria":{"yes":"moderator or bot verification check","no":"standard player interaction"}},"urgency":{"type":"choice","instructions":"How urgent is this message to avoid an AFK or bot flag?","criteria":{"critical":"immediate action required","normal":"ordinary chat","low":"spam or ignore"}}}
                if hasattr(self.agent,"decide"):pred=self.agent.decide(state,q)
                elif hasattr(self.agent,"predict"):pred=self.agent.predict({"sender":sender_clean,"message":clean_text},q)
                else:pred={}
                c=pred.get("is_threat")
                if isinstance(c,dict):c=c.get("choice")
                th=(c=="yes") or is_bot
                u=pred.get("urgency","normal")
                if isinstance(u,dict):u=u.get("choice","normal")
                urg=str(u).upper()
                return {"is_threat":th,"threat_level":"SUSPECTED_BOT_CHECK" if th else "NORMAL_CHAT","confidence":0.96 if th else 0.88,"urgency":urg if th else "NORMAL","reason":f"{self.model_name} Decision Model classification","suggested_replies":SUGGESTED_REPLIES[:3] if th else []}
            except Exception as e:
                logger.debug(f"Decision agent inference error: {e}, falling back to heuristics")
        if is_bot:
            return {"is_threat":True,"threat_level":"SUSPECTED_BOT_CHECK","confidence":0.88,"urgency":"HIGH","reason":"Message contains direct responsiveness inquiry","suggested_replies":SUGGESTED_REPLIES[:3]}
        return {"is_threat":False,"threat_level":"NORMAL_CHAT","confidence":0.80,"urgency":"LOW","reason":"Standard player interaction or whisper","suggested_replies":[]}


SingletonLayaAgent=SingletonDecisionAgent


class SingletonVisionInspector:
    _instance=None
    _lock=threading.Lock()
    CAPTCHA_KEYWORDS=re.compile(r"(?:v[ée]rification|anti[- ]bot|recopiez|code|captcha|question de s[ée]curit[ée]|attention)",re.I)
    @classmethod
    def get_instance(cls):
        with cls._lock:
            if cls._instance is None:cls._instance=cls()
            return cls._instance
    def evaluate_modal_text(self,text):
        m=self.CAPTCHA_KEYWORDS.search(text)
        if m:return{"has_captcha":True,"matched_keyword":m.group(0),"urgency":"CRITICAL","action_required":"Human captcha input on game screen"}
        return{"has_captcha":False,"matched_keyword":None,"urgency":"NORMAL","action_required":None}


class ThreadedHTTPServer(ThreadingMixIn,HTTPServer):
    daemon_threads=True
    allow_reuse_address=True


def _safe_char_stats(d):
    return CharacterStats(**{k:v for k,v in d.items() if k in _CHAR_FIELDS}) if d else CharacterStats()


def _safe_target_stats(d):
    return TargetStats(**{k:v for k,v in d.items() if k in _TARGET_FIELDS}) if d else TargetStats()


class SentinelHTTPHandler(BaseHTTPRequestHandler):
    laya=SingletonLayaAgent.get_instance()
    vision=SingletonVisionInspector.get_instance()
    network_engine=MultiInstanceNetworkHealth(CHECKPOINT_PATH) if CHECKPOINT_PATH.exists() else None
    repo=GameDataRepository.get_instance()
    memory=EpisodicSkillMemory.get_instance()
    world_graph=DofusWorldGraph()
    resource_graph=ResourceKnowledgeGraph()
    quest_graph=QuestKnowledgeGraph()
    nlp_engine=DofusNLPEngine()
    def log_message(self,format,*args):pass
    def _send_json(self,status,data):
        p=json.dumps(data).encode("utf-8");self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8");self.send_header("Content-Length",str(len(p)));self.end_headers();self.wfile.write(p)
    def do_GET(self):
        u=urllib.parse.urlparse(self.path);path=u.path;g=urllib.parse.parse_qs(u.query).get
        r=self.repo;j=self._send_json
        if path in("/sentinel/status","/status","/health"):
            l=self.laya
            j(200,{"status":"online","service":"dofus-sentinel-ai","singleton_models":{"trm_moe":self.network_engine is not None,"laya":l.agent is not None,"model_name":l.model_name,"thx01":l.model is not None or "THX-01" in l.model_name,"trm_act":l.combat_model is not None,"vision":True,"combat_calculator":True,"total_spells":len(r.spells_by_id),"total_monsters":len(r.monsters_by_id)},"port":8890,"time":time.time()})
        elif path=="/sentinel/combat/mob":
            q=g("name",[""])[0] or g("id",[""])[0];n=int(g("grade",["1"])[0]);lang=g("lang",["fr"])[0]
            mob=r.find_monster(q)
            if not mob:j(404,{"error":"monster_not_found","query":q});return
            gr=mob.get_grade(n)
            j(200,{"status":"ok","id":mob.id,"name":mob.get_name(lang),"is_boss":mob.is_boss,"grade":{"grade":gr.grade,"level":gr.level,"hp":gr.life_points,"ap":gr.action_points,"mp":gr.movement_points,"neutral_res":gr.neutral_res,"earth_res":gr.earth_res,"fire_res":gr.fire_res,"water_res":gr.water_res,"air_res":gr.air_res,"fixed_res":gr.fixed_res}})
        elif path=="/sentinel/combat/all_mobs":
            lang=g("lang",["fr"])[0]
            j(200,{"status":"ok","monsters":[{"id":m.id,"name":m.get_name(lang),"name_fr":m.name_fr,"name_en":m.name_en,"name_es":m.name_es,"grades_count":len(m.grades)} for m in r.monsters_by_id.values()]})
        elif path=="/sentinel/combat/spells":
            bid=int(g("breed",["8"])[0]);lang=g("lang",["fr"])[0]
            j(200,{"status":"ok","breed_id":bid,"spells":[{"id":s.id,"name":s.get_name(lang),"ap_cost":s.ap_cost,"min_range":s.min_range,"max_range":s.max_range,"cast_in_line":s.cast_in_line,"max_cast_per_turn":s.max_cast_per_turn,"effects":[{"element":e.element,"min":e.min_roll,"max":e.max_roll,"crit_min":e.crit_min_roll,"crit_max":e.crit_max_roll} for e in s.effects]} for s in r.get_breed_spells(bid)]})
        elif path=="/sentinel/navigation/zaaps":
            j(200,{"status":"ok","zaaps":self.world_graph.get_all_zaaps()})
        elif path=="/sentinel/resources/all":
            rs=self.resource_graph.resources
            j(200,{"status":"ok","total_resources":len(rs),"resources":[{"id":x.id,"name_fr":x.name_fr,"name_en":x.name_en,"name_es":x.name_es,"profession":x.profession,"level_req":x.level_req} for x in rs]})
        elif path=="/sentinel/quests/all":
            gs=self.quest_graph.guides
            j(200,{"status":"ok","total_quests":len(gs),"quests":[x.to_dict() for x in gs]})
        elif path=="/sentinel/quests/guide":
            q=g("id",[""])[0] or g("q",[""])[0];gs=self.quest_graph.search_guides(q)
            j(200,{"status":"ok","guide":gs[0].to_dict()}) if gs else j(404,{"error":"guide_not_found","query":q})
        else:j(404,{"error":"not_found"})
    def do_POST(self):
        j=self._send_json;r=self.repo;l=self.laya
        n=int(self.headers.get("Content-Length",0));body=self.rfile.read(n) if n>0 else b"{}"
        try:req=json.loads(body.decode("utf-8"))
        except Exception:j(400,{"error":"invalid_json"});return
        p=self.path
        if p=="/sentinel/eval_chat":
            res=l.evaluate_message(req.get("sender","Unknown"),req.get("text",""),bool(req.get("is_mod",False)))
            res["instance_id"]=req.get("instance_id","slot-0");res["timestamp"]=time.time();j(200,res)
        elif p=="/sentinel/eval_modal":
            res=self.vision.evaluate_modal_text(req.get("text",""))
            res["instance_id"]=req.get("instance_id","slot-0");res["timestamp"]=time.time();j(200,res)
        elif p=="/sentinel/combat/calculate_damage":
            sid=req.get("spell");cd=req.get("character",{});td=req.get("target",{});mq=req.get("monster");gn=int(req.get("monster_grade",1))
            spell=r.find_spell(sid)
            if not spell:j(404,{"error":"spell_not_found","spell":sid});return
            ch=_safe_char_stats(cd)
            if mq:
                mob=r.find_monster(mq)
                if not mob:j(404,{"error":"monster_not_found","monster":mq});return
                tg=TargetStats.from_monster_grade(mob,gn)
            elif td:tg=_safe_target_stats(td)
            else:tg=TargetStats()
            res=DofusDamageCalculator.calculate_spell_damage(spell,ch,tg)
            j(200,{"status":"ok","spell_id":res.spell_id,"spell_name":res.spell_name,"ap_cost":res.ap_cost,"damage_min":res.total_min,"damage_max":res.total_max,"damage_avg":res.total_avg,"crit_min":res.crit_total_min,"crit_max":res.crit_total_max,"crit_avg":res.crit_total_avg,"crit_rate":res.crit_rate,"expected_damage":res.expected_value,"ap_efficiency":res.ap_efficiency})
        elif p=="/sentinel/combat/best_combo":
            cd=req.get("character",{});sl=req.get("spells");mq=req.get("monster","Bouftou");gn=int(req.get("monster_grade",1))
            ch=_safe_char_stats(cd)
            mob=r.find_monster(mq)
            if not mob:j(404,{"error":"monster_not_found","monster":mq});return
            tg=TargetStats.from_monster_grade(mob,gn)
            av=[sp for i in sl if (sp:=r.find_spell(i))] if sl else r.get_breed_spells(cd.get("breed_id",8))
            j(200,{"status":"ok",**SpellRotationOptimizer.optimize_turn(ch,av,tg).to_dict()})
        elif p=="/sentinel/combat/tactical_plan":
            cd=req.get("character",{});mq=req.get("monster","Bouftou");gn=int(req.get("monster_grade",1));dist=int(req.get("distance_cells",3));hp=float(req.get("hp_percent",1.0))
            ch=_safe_char_stats(cd)
            mob=r.find_monster(mq)
            if not mob:j(404,{"error":"monster_not_found","monster":mq});return
            tg=TargetStats.from_monster_grade(mob,gn)
            bid=cd.get("breed_id",8) if cd else 8
            av=r.get_breed_spells(bid)
            cs=self.memory.query_skill(bid,mob.name_fr,dist,hp)
            if cs and cs.reward_achieved>=10.0:
                j(200,{"status":"ok","source":"EPISODIC_MEMORY","stance":cs.stance,"target_name":mob.name_fr,"target_hp":tg.current_hp,"estimated_damage":0.0,"multi_turn_projected_damage":0.0,"action_sequence":[{"spell_name":s,"type":"EPISODIC_SKILL"} for s in cs.proven_sequence],"explanation":f"Retrieved proven tactical skill from Episodic Memory (reward: {cs.reward_achieved}).","value_score":0.99});return
            plan=TacticalCombatEngine.plan_tactical_turn(ch,tg,av,distance_cells=dist,char_hp_percent=hp)
            if plan.value_score>=0.85:
                self.memory.record_outcome(breed_id=bid,target_name=mob.name_fr,distance_cells=dist,char_hp_percent=hp,stance=plan.stance.value,action_sequence=[a.get("spell_name","") for a in plan.action_sequence if a.get("spell_name")],reward=plan.value_score*15.0)
            j(200,{"status":"ok","source":"TACTICAL_WORLD_MODEL","stance":plan.stance.value,"target_name":plan.target_name,"target_hp":plan.target_hp,"estimated_damage":plan.estimated_damage,"multi_turn_projected_damage":plan.multi_turn_projected_damage,"action_sequence":plan.action_sequence,"explanation":plan.explanation,"value_score":plan.value_score})
        elif p=="/sentinel/navigation/path":
            a=req.get("from",[4,-19]);b=req.get("to",[4,-18]);az=bool(req.get("allow_zaap",True));lang=req.get("lang","fr")
            j(200,{"status":"ok","path":self.world_graph.find_path(tuple(a),tuple(b),allow_zaap=az,lang=lang).to_dict()})
        elif p=="/sentinel/resources/search":
            q=req.get("query","fer");near=req.get("near");nr=tuple(near) if near else None;mr=int(req.get("max_results",8))
            spots=self.resource_graph.search_spots(q,near_pos=nr,max_results=mr)
            j(200,{"status":"ok","query":q,"total_spots":len(spots),"spots":[s.to_dict() for s in spots]})
        elif p=="/sentinel/resources/farm_loop":
            q=req.get("resource","blé");st=tuple(req.get("start",[4,-19]));mm=int(req.get("max_maps",6))
            loop=self.resource_graph.generate_farm_loop(q,start_coord=st,max_maps=mm)
            j(200,{"status":"ok","farm_loop":loop.to_dict()}) if loop else j(404,{"error":"no_spots_found_for_resource","resource":q})
        elif p=="/sentinel/nlp/query":
            j(200,{"status":"ok","result":self.nlp_engine.process_query(req.get("text",""),current_pos=tuple(req.get("current_pos",[4,-19]))).to_dict()})
        elif p=="/sentinel/parse_command":
            ct=req.get("command","").strip();target_slot=req.get("target_slot","all");cd=req.get("character");curr_pos=tuple(req.get("current_pos",[4,-19]))
            directive=None;confidence=0.85;explanation="Intent parsing";metadata={}
            pred_label,thx_conf=l.classify_directive(ct)
            if pred_label!="NONE" and thx_conf>=0.70:
                directive=pred_label;confidence=thx_conf;explanation=f"THX-01 Classified Directive: {pred_label}";metadata={"source":"THX-01-ModernBERT","neural_confidence":thx_conf}
            cmd_lower=ct.lower()
            if any(w in cmd_lower for w in ("combo","attack","attaque","ataca","kill","tuer","matar","rotation","degats","damage")):
                target_mob=None
                for m in r.monsters_by_id.values():
                    for nm in (m.name_fr,m.name_en,m.name_es):
                        if nm.lower() in cmd_lower:target_mob=m;break
                    if target_mob:break
                if target_mob:
                    ch=_safe_char_stats(cd) if cd else CharacterStats(action_points=10,strength=300,power=40)
                    av=r.get_breed_spells(cd.get("breed_id",8) if cd else 8)
                    plan=TacticalCombatEngine.plan_tactical_turn(ch,TargetStats.from_monster_grade(target_mob,1),av)
                    directive="EXECUTE_TACTICAL_PLAN";confidence=0.98;explanation=f"Tactical Stance: {plan.stance.value} against {target_mob.name_fr} - {plan.explanation}"
                    metadata={"stance":plan.stance.value,"target_name":plan.target_name,"target_hp":plan.target_hp,"rotation":plan.action_sequence,"multi_turn_projected_damage":plan.multi_turn_projected_damage,"value_score":plan.value_score}
            if not directive:
                nlp_res=self.nlp_engine.process_query(ct,current_pos=curr_pos)
                if nlp_res.intent.value!="UNKNOWN":
                    directive=nlp_res.execution_payload.get("action") or nlp_res.intent.value;confidence=nlp_res.confidence;explanation=nlp_res.message_reply
                    metadata={"nlp_reply":nlp_res.message_reply,"detected_lang":nlp_res.detected_lang,**nlp_res.execution_payload}
            if not directive:
                if l.agent is not None and hasattr(l.agent,"decide"):
                    try:
                        pred=l.agent.decide(ct,{"action":{"type":"choice","instructions":"Which game bot directive does the user want executed?","criteria":{"FOLLOW":"follow leader or other player","AUTO_JOIN_COMBAT":"join combat or click team fight sword","PASS_TURN":"pass turn in combat or skip turn","AUTO_FARM":"farm monsters, harvest wheat, mine ores or collect resources","STOP":"stop or pause automation"}}})
                        act=pred.get("action")
                        if isinstance(act,dict):act=act.get("choice")
                        if act in ("FOLLOW","AUTO_JOIN_COMBAT","PASS_TURN","AUTO_FARM","STOP"):directive=act;confidence=0.98;explanation=f"THX-01 calibrated decision: {act}"
                    except Exception:pass
            if not directive:
                if any(w in cmd_lower for w in ("follow","suis","suivre","lead")):directive="FOLLOW";confidence=0.95;explanation="Set instance to follow leader"
                elif any(w in cmd_lower for w in ("join","combat","fight","rejoins","epee")):directive="AUTO_JOIN_COMBAT";confidence=0.96;explanation="Auto-join team fights on map"
                elif any(w in cmd_lower for w in ("pass","passe","tour","skip")):directive="PASS_TURN";confidence=0.98;explanation="Auto pass turn in combat after natural human delay"
                elif any(w in cmd_lower for w in ("stop","pause","arret","arrete")):directive="STOP";confidence=0.99;explanation="Stop all active automated co-op directives"
                elif any(w in cmd_lower for w in ("farm","mob","recolte","harvest","craft")):directive="AUTO_FARM";confidence=0.90;explanation="Highlight and engage target farm resources"
                else:directive="CUSTOM_ACTION";confidence=0.70;explanation=f"Custom action: {ct}"
            resp_data={"status":"ok","directive":directive,"confidence":confidence,"explanation":explanation,"target_slot":target_slot,"timestamp":time.time()}
            if metadata:resp_data["metadata"]=metadata
            j(200,resp_data)
        else:j(404,{"error":"not_found"})


def run_sentinel_service(host="127.0.0.1",port=8890):
    logger.info(f"Starting Singleton AI Sentinel Daemon on http://{host}:{port}...")
    server=ThreadedHTTPServer((host,port),SentinelHTTPHandler)
    logger.info("Serving Singleton AI Sentinel for all 4 instances with ZERO RAM DUPLICATION.")
    try:server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down Sentinel Daemon...");server.shutdown()


if __name__=="__main__":run_sentinel_service()
