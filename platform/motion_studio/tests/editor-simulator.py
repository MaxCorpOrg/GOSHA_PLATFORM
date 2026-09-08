"""Local UI fixture, no serial/network robot adapter. Synthetic key and state only."""
import asyncio
import time
from aiohttp import web
ORIGIN = 'http://127.0.0.1:5180'
PROTOCOL = 'gosha.motion.live.v1'
IDS = ['arm_negative_x','arm_positive_x','leg_negative_x','leg_positive_x','foot_negative_x','foot_positive_x']
LIMITS = [dict(id=i,min=lo,max=hi,max_speed_dps=10) for i,lo,hi in zip(IDS[1:],[-70,-35,-35,-30,-30],[55,35,35,30,30])]
async def ports(request):
    return web.json_response(dict(protocol=PROTOCOL,ports=[dict(port_id='e'*24,label='Симулятор — без робота',vid='303a',pid='1001',busy=False)]),headers={'Access-Control-Allow-Origin':ORIGIN})
async def live(request):
    if request.headers.get('Origin') != ORIGIN: raise web.HTTPForbidden()
    ws=web.WebSocketResponse(); await ws.prepare(request)
    command={i:0 for i in IDS}; position=command.copy(); sid=None; seq=0; last=time.monotonic()
    async def send(op, **fields): await ws.send_json(dict(protocol=PROTOCOL,op=op,**fields))
    async for packet in ws:
        if packet.type != web.WSMsgType.TEXT: break
        data=packet.json(); op=data.get('op'); now=time.monotonic()
        if op=='hello':
            await send('capabilities',request_id=data['request_id'],profile_id='gosha-preview-v1',mode='motion_editor',commissioning=False,calibrated=False,calibration_id='e'*64,motion_allowed=True,right_arm_initialized=True,watchdog_ms=300,max_rate_hz=20,stop_mode='hold_setpoint',auth_required=True,joint_limits=LIMITS,commanded_pose=command,feedback=dict(measured_position=False,imu=False))
        elif op=='arm' and data.get('access_key')=='simulation-access-key-only':
            sid='editor-simulator-session';seq=0;last=now
            await send('armed',request_id=data['request_id'],session_id=sid,calibration_id='e'*64)
        elif sid and data.get('session_id')==sid:
            if op=='stop':
                await send('stopped',session_id=sid,commanded_pose=command,measured_pose=None,tilt=None);sid=None
            elif op in ('pose','keepalive') and data.get('seq',0)>seq and now-last<0.3:
                seq=data['seq']
                if op=='pose':
                    target=data['target'];speed=data['speed_dps']
                    if set(target)!={j['id'] for j in LIMITS} or not 1<=speed<=10 or any(not j['min']<=target[j['id']]<=j['max'] for j in LIMITS):
                        await send('error',session_id=sid,code='invalid_target');sid=None;continue
                    delta=speed*min(now-last,0.1)
                    for j in LIMITS:
                        i=j['id'];position[i]+=max(-delta,min(delta,target[i]-position[i]));command[i]=round(position[i])
                last=now
                await send('ack',session_id=sid,seq=seq,commanded_pose=command,measured_pose=None,tilt=None)
            else:
                await send('error',session_id=sid,code='watchdog_timeout');sid=None
    return ws
app=web.Application();app.router.add_get('/ports',ports);app.router.add_get('/live',live)
web.run_app(app,host='127.0.0.1',port=5181,print=None)
