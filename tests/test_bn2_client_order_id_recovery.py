from __future__ import annotations
from datetime import datetime
from types import SimpleNamespace
from engine.live.broker.base import Balance, Holding, Order, OrderSide, OrderStatus, OrderType
from engine.live.pending_order_manager import PendingOrderManager, STATE_OPEN, STATE_SUBMITTING, STATE_UNKNOWN_OPEN
from engine.live.position_manager import PositionEntry, PositionManager
from engine.live.runner import Runner, RunnerStats
from engine.live.safety.layer import SafetyDecision
from engine.strategies.rulebook import default_rulebook
class N:
    def __init__(self): self.errors=[]; self.orders=[]; self.blocks=[]
    def send_error(self,m): self.errors.append(m)
    def send_order(self,o): self.orders.append(o)
    def send_safety_block(self,c,m): self.blocks.append((c,m))
    def send(self,*a,**k): pass
class S:
    def check_order(self,*a,**k): return SafetyDecision(True,'ok','')
    def record_order(self,*a,**k): pass
    def record_fill(self,*a,**k): pass
class B:
    mode='alpaca_paper'
    def __init__(self,status=OrderStatus.PENDING,raise_submit=False):
        self.status=status; self.raise_submit=raise_submit; self.orders={}; self.recovered=None; self.buy_calls=0; self.sell_calls=0; self.last_cid=''; self.get_order_calls=0; self.fail_get_order=False
    def get_balance(self): return Balance(1,1,0,self.get_holdings())
    def get_holdings(self): return [Holding('AAA',1,100,94,94,-6,-6)]
    def get_current_price(self,t): return 100.0
    def is_market_open(self,t=None): return True
    def place_buy(self,t,q,order_type=OrderType.MARKET,price=0.0,client_order_id=''):
        self.buy_calls+=1; self.last_cid=client_order_id
        if self.raise_submit: raise RuntimeError('lost')
        o=Order('B1',t,OrderSide.BUY,order_type,q,price,self.status,q if self.status==OrderStatus.FILLED else 0,100 if self.status==OrderStatus.FILLED else 0,client_order_id=client_order_id)
        self.orders[o.order_id]=o; return o
    def place_sell(self,t,q,order_type=OrderType.MARKET,price=0.0,client_order_id=''):
        self.sell_calls+=1; self.last_cid=client_order_id
        o=Order('S1',t,OrderSide.SELL,order_type,q,price,self.status,client_order_id=client_order_id)
        self.orders[o.order_id]=o; return o
    def get_order(self,oid):
        self.get_order_calls+=1
        if self.fail_get_order: return None
        return self.orders.get(oid)
    def get_order_by_client_order_id(self,cid):
        if self.recovered and self.recovered.client_order_id==cid: return self.recovered
        return next((o for o in self.orders.values() if o.client_order_id==cid),None)
class Paper(B):
    mode='paper'
    def place_buy(self,t,q,order_type=OrderType.MARKET,price=0.0):
        self.buy_calls+=1; return Order('P1',t,OrderSide.BUY,order_type,q,price,OrderStatus.FILLED,q,100)
class Kis(Paper): mode='live'; kis_mode='vts'
class RB:
    def __init__(self,atr=1.0): self.atr=atr; self.rb=default_rulebook('AAA',asset_type='us_stock',direction='long')
    def get_last_atr(self,t): return self.atr
    def get_rulebook(self,t): return self.rb
    def get_last_market_context(self,t): return {'score':50,'vix_level':18,'sector_score':50}
def runner(tmp_path,broker):
    r=Runner.__new__(Runner); r.broker=broker; r.safety=S(); r.notifier=N(); r.rulebook=RB()
    store={}
    def reg(t,*a,**k):
        store[t]=SimpleNamespace(ticker=t)
        return store[t]
    r.position_manager=SimpleNamespace(register_entry=reg,add_to_position=lambda *a,**k: SimpleNamespace(ticker='AAA'),get=lambda t: store.get(t))
    r.approval_manager=SimpleNamespace(get_request=lambda rid: None,_save=lambda: None)
    r.pending_order_manager=PendingOrderManager(broker,path=tmp_path/'pending.json'); r.buy_reconciler=r._make_buy_reconciler()
    r.order_shares=1.0; r.order_notional=None; r.stats=RunnerStats(); r.symbols=['AAA']; return r
def test_restart_recovers_submitting_by_client_id(tmp_path):
    b=B(); path=tmp_path/'p.json'; cid=PendingOrderManager.make_client_order_id(ticker='AAA',side='buy',purpose='entry',seed='x')
    m=PendingOrderManager(b,path=path); m.create_submitting_intent(client_order_id=cid,ticker='AAA',side='buy',purpose='entry',requested_shares=1)
    b.orders['B77']=Order('B77','AAA',OrderSide.BUY,OrderType.MARKET,1,0,OrderStatus.PENDING,client_order_id=cid)
    m2=PendingOrderManager(b,path=path); assert m2.all()[0].state==STATE_SUBMITTING; m2.poll_all(); assert m2.get_record('B77').state==STATE_OPEN
def test_missing_client_lookup_keeps_lock(tmp_path):
    b=B(); m=PendingOrderManager(b,path=tmp_path/'p.json'); cid=PendingOrderManager.make_client_order_id(ticker='AAA',side='buy',purpose='entry',seed='missing')
    m.create_submitting_intent(client_order_id=cid,ticker='AAA',side='buy',purpose='entry',requested_shares=1); m.poll_all()
    assert m.get_record_by_client_order_id(cid).state==STATE_UNKNOWN_OPEN; assert m.is_ticker_locked('AAA')
def test_submit_exception_recovers(tmp_path):
    b=B(raise_submit=True); r=runner(tmp_path,b); cid=r.pending_order_manager.make_client_order_id(ticker='AAA',side='buy',purpose='entry',seed='manual')
    b.recovered=Order('B88','AAA',OrderSide.BUY,OrderType.MARKET,1,0,OrderStatus.PENDING,client_order_id=cid)
    o=r._submit_order_with_intent(side='BUY',ticker='AAA',shares=1,purpose='entry',seed='manual')
    assert o.order_id=='B88'; assert r.pending_order_manager.get_record('B88') is not None
def test_deterministic_keys_and_paper_kis_no_intent(tmp_path):
    a=PendingOrderManager.make_client_order_id(ticker='AAA',side='buy',purpose='entry',seed='x')
    assert a==PendingOrderManager.make_client_order_id(ticker='AAA',side='buy',purpose='entry',seed='x')
    assert a!=PendingOrderManager.make_client_order_id(ticker='AAA',side='buy',purpose='entry',seed='y')
    for br in (Paper(),Kis()):
        r=runner(tmp_path,br); r._try_order('BUY','AAA',100,'signal')
        assert br.buy_calls==1; assert r.pending_order_manager.all()==[]
def test_filled_buy_after_intent_can_go_reconciling(tmp_path):
    b=B(status=OrderStatus.FILLED); r=runner(tmp_path,b); o=r._submit_order_with_intent(side='BUY',ticker='AAA',shares=1,purpose='entry',seed='f')
    assert r.pending_order_manager.get_record('B1').state=='RECONCILING'
    r._get_buy_reconciler().track_failure(o,purpose='entry',error='ATR missing')
    assert r.pending_order_manager.all()[0].state=='RECONCILING'
def test_recovered_filled_emits_event_without_get_order_and_keeps_lock_until_finalized(tmp_path):
    b=B(); b.fail_get_order=True; path=tmp_path/'p.json'; cid=PendingOrderManager.make_client_order_id(ticker='AAA',side='buy',purpose='entry',seed='filled')
    m=PendingOrderManager(b,path=path); m.create_submitting_intent(client_order_id=cid,ticker='AAA',side='buy',purpose='entry',requested_shares=1)
    b.recovered=Order('B99','AAA',OrderSide.BUY,OrderType.MARKET,1,0,OrderStatus.FILLED,1,100,client_order_id=cid)
    events=m.poll_all()
    assert len(events)==1; assert events[0][0].state=='RECONCILING'; assert events[0][1].order_id=='B99'
    assert b.get_order_calls==0; assert m.is_ticker_locked('AAA')
    m.mark_finalized('B99')
    assert not m.is_ticker_locked('AAA')
def test_auto_exit_sell_intent_first(tmp_path,monkeypatch):
    monkeypatch.setenv('EXIT_LIVE_POLICY','0'); b=B(); b.get_current_price=lambda t: 94.0; p=PendingOrderManager(b,path=tmp_path/'p.json'); pm=PositionManager.__new__(PositionManager)
    pos=PositionEntry('AAA',datetime.now().isoformat(),100,1,1,95,110,2,95,100,'fixed',99,'long',member_hash='mh')
    pm._positions={'AAA':pos}; pm._save=lambda: None
    assert pm._check_one('AAA',pos,b,notifier=N(),pending_manager=p) is None
    assert b.sell_calls==1; assert p.get_record('S1').client_order_id; assert p.get_record('S1').state==STATE_OPEN
