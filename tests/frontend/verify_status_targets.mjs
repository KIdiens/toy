/* 设备状态控制器必须把真实目标交给四轴组件，停止帧必须清掉旧值。 */
import assert from 'node:assert/strict';

class Element {
  constructor() { this.attrs = new Map(); this.events = new Map(); this.classList = { add() {}, remove() {} }; }
  setAttribute(k,v) { this.attrs.set(k,String(v)); }
  getAttribute(k) { return this.attrs.get(k) ?? null; }
  hasAttribute(k) { return this.attrs.has(k); }
  removeAttribute(k) { this.attrs.delete(k); }
  addEventListener(k,fn) { this.events.set(k,fn); }
  querySelector() { return null; }
  push() {}
}
globalThis.HTMLElement = Element;
globalThis.customElements = { define() {}, get() {} };
let reloads=0;
globalThis.location = { protocol:'http:', host:'localhost', reload(){reloads++;} };
const listeners = new Map();
globalThis.window = { addEventListener(name,fn) { listeners.set(name,fn); } };
let socket;
globalThis.WebSocket = class { constructor() { socket=this; } close() {} };
const axes = Array.from({length:4},(_,i)=>{const el=new Element();el.setAttribute('axis',i+1);return el;});
const nodes = new Map();
globalThis.document = {
  querySelector(id) { if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id); },
  querySelectorAll(query) { return query==='axis-limit-bar' ? axes : []; },
  createElement() { return new Element(); },
};
const limits = {role_id:1,config_version:'1.1.0',min_us:1400,max_us:1600,neutral_us:1500};
const target = {role_id:1,config_version:'1.1.0',source:'sent_frame',seq:5,progress_ms:100,
  action_id:'wave',target_us:[1500,1540,1520,1500],valid_for_ms:1000};
const status = {state:'PERFORMING',role_id:1,session_generation:1,target,axis_limits:limits};
let statusGate = null;
globalThis.fetch = async url=>{
  if(url.endsWith('/status') && statusGate) await statusGate;
  return {ok:true,status:200,text:async()=>JSON.stringify({code:0,data:
    url.endsWith('/status') ? status : url.endsWith('/roles') ? [{role_id:1,name:'小星',config_version:'1.1.0'}] : {provider:'stub'}})};
};
const {default:boot}=await import('../../host/web/static/pages/status.js');
await boot();
assert.deepEqual(axes.map(el=>el.frame),axes.map(()=>target.target_us),'状态页没有给四轴注入目标帧');
socket.onmessage({data:JSON.stringify({...status,kind:'STATUS',state:'IDLE',target:null,action:null})});
assert.ok(axes.every(el=>el.frame===null),'停止后仍残留旧指令目标');
const emit = data => socket.onmessage({data:JSON.stringify({kind:'STATUS',...data})});
emit(status);
assert.ok(axes.every(el=>el.frame!==null));
socket.onerror();
assert.ok(axes.every(el=>el.frame===null),'事件流断线后仍显示旧目标');
socket.onopen();
emit({...status,session_generation:0});
assert.ok(axes.every(el=>el.frame!==null),'服务重启后代数降低被误判为旧会话');
emit({...status,session_generation:-1,state:'IDLE',target:null});
assert.ok(axes.every(el=>el.frame!==null),'同一连接的旧代数覆盖了较新目标');
let release;
statusGate=new Promise(resolve=>{release=resolve;});
nodes.get('#banner').events.get('banner-retry')();
emit({...status,state:'IDLE',target:null,action:null});
release();
await new Promise(resolve=>setImmediate(resolve));
assert.ok(axes.every(el=>el.frame===null),'迟到 HTTP 恢复了已经停止的旧目标');
statusGate=null;
emit(status);
(listeners.get('pagehide')||listeners.get('beforeunload'))?.();
assert.ok(axes.every(el=>el.frame===null));
emit(status);
assert.ok(axes.every(el=>el.frame===null),'页面销毁后仍接收目标');
listeners.get('pageshow')({persisted:false});
assert.equal(reloads,0);
listeners.get('pageshow')({persisted:true});
assert.equal(reloads,1,'历史缓存恢复后没有重新建立已关闭的事件流');

// 使用可控时间验证到期与非法数据，无需真实延时。
const {createTargetDisplay}=await import('../../host/web/static/pages/status-targets.js');
let clock=0, timerId=0;
const timers=new Map();
const display=createTargetDisplay(axes,{
  now:()=>clock, schedule:(fn,ms)=>{const id=++timerId;timers.set(id,{fn,at:clock+ms});return id;},
  cancel:id=>timers.delete(id),
});
const tick=ms=>{clock+=ms;for(const [id,t]of timers)if(t.at<=clock){timers.delete(id);t.fn();}};
display.update(status);
tick(999);
assert.ok(axes.every(el=>el.frame!==null));
tick(1);
assert.ok(axes.every(el=>el.frame===null),'目标未在剩余有效期结束时清空');
for(const data of [
  {...status,state:'IDLE'}, {...status,fault:{code:'INTERLOCK'}},
  {...status,role_id:2}, {...status,target:{...target,config_version:'old'}},
  {...status,target:{...target,source:'actual'}},
  {...status,target:{...target,target_us:[NaN,1500,1500,1500]}},
  {...status,target:{...target,target_us:[1700,1500,1500,1500]}},
  {...status,target:{...target,valid_for_ms:0}},
]){display.update(status);display.update(data);assert.ok(axes.every(el=>el.frame===null));}
display.update(status, clock-1001);
assert.ok(axes.every(el=>el.frame===null),'HTTP 等待耗时未计入有效期');
display.update({...status,role_id:2,axis_limits:{...limits,role_id:2,min_us:1420,max_us:1580},
  target:{...target,role_id:2}});
assert.ok(axes.every(el=>el.getAttribute('min-us')==='1420' && el.getAttribute('max-us')==='1580'));
display.clear();
assert.equal(timers.size,0);
console.log('PASS 状态页目标、有效期、角色限幅、断线清空与异步竞争');
