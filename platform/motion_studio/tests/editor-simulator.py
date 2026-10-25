"""Local UI fixture, no serial/network robot adapter. Synthetic key and state only."""
import asyncio
import argparse
import time
from aiohttp import web
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--origin', default='http://127.0.0.1:5180')
parser.add_argument('--port', type=int, default=5181)
parser.add_argument('--joints', type=int, choices=(5, 6), default=5)
parser.add_argument('--speed-limit', type=int, choices=(10, 60, 120), default=10)
args = parser.parse_args()
ORIGIN = args.origin
PROTOCOL = 'gosha.motion.live.v1'
IDS = ['arm_negative_x','arm_positive_x','leg_negative_x','leg_positive_x','foot_negative_x','foot_positive_x']
LIMITS = [dict(id=i,min=lo,max=hi,max_speed_dps=args.speed_limit) for i,lo,hi in zip(IDS[1:],[-70,-35,-35,-30,-30],[55,35,35,30,30])]
if args.joints == 6:
    LIMITS.insert(0, dict(id=IDS[0], min=0, max=15, max_speed_dps=5))
STATE = dict(commanded_pose=dict.fromkeys(IDS, 0), ops=[])
async def ports(request):
    return web.json_response(dict(protocol=PROTOCOL,ports=[dict(port_id='e'*24,label='Симулятор — без робота',vid='303a',pid='1001',busy=False,local_auth=True)]),headers={'Access-Control-Allow-Origin':ORIGIN})
async def state(request):
    return web.json_response(STATE)
async def live(request):
    if request.headers.get('Origin') != ORIGIN: raise web.HTTPForbidden()
    ws=web.WebSocketResponse(); await ws.prepare(request)
    command={i:0 for i in IDS}; position=command.copy(); sid=None; seq=0; last=time.monotonic()
    async def send(op, **fields): await ws.send_json(dict(protocol=PROTOCOL,op=op,**fields))
    async for packet in ws:
        if packet.type != web.WSMsgType.TEXT: break
        data=packet.json(); op=data.get('op'); now=time.monotonic()
        STATE['ops'] = [*STATE['ops'][-99:], op]
        if op=='hello':
            policy = dict(speed_profile='working-v1') if args.speed_limit > 10 else {}
            await send('capabilities',request_id=data['request_id'],profile_id='gosha-preview-v1',mode='motion_editor',commissioning=False,calibrated=False,calibration_id='e'*64,motion_allowed=True,right_arm_initialized=True,watchdog_ms=300,max_rate_hz=20,stop_mode='hold_setpoint',auth_required=True,joint_limits=LIMITS,commanded_pose=command,feedback=dict(measured_position=False,imu=False),**policy)
        elif op=='package_list':
            await send('package_status', request_id=data['request_id'], status='listed', count=0, packages=[])
        elif op=='arm' and (data.get('access_key')=='simulation-access-key-only' or data.get('local_auth') is True):
            sid='editor-simulator-session';seq=0;last=now
            await send('armed',request_id=data['request_id'],session_id=sid,calibration_id='e'*64)
        elif sid and data.get('session_id')==sid:
            if op=='stop':
                await send('stopped',session_id=sid,commanded_pose=command,measured_pose=None,tilt=None);sid=None
            elif op in ('pose','keepalive') and data.get('seq',0)>seq and now-last<0.3:
                seq=data['seq']
                if op=='pose':
                    target=data['target'];speed=data['speed_dps']
                    if set(target)!={j['id'] for j in LIMITS} or not 1<=speed<=args.speed_limit or any(not j['min']<=target[j['id']]<=j['max'] for j in LIMITS):
                        await send('error',session_id=sid,code='invalid_target');sid=None;continue
                    for j in LIMITS:
                        delta=min(speed,j['max_speed_dps'])*min(now-last,0.1)
                        i=j['id'];position[i]+=max(-delta,min(delta,target[i]-position[i]));command[i]=round(position[i])
                    STATE['commanded_pose'] = command.copy()
                    STATE['last_target'] = target.copy()
                    STATE['last_speed_dps'] = speed
                last=now
                await send('ack',session_id=sid,seq=seq,commanded_pose=command,measured_pose=None,tilt=None)
            else:
                await send('error',session_id=sid,code='watchdog_timeout');sid=None
    return ws
app=web.Application();app.router.add_get('/ports',ports);app.router.add_get('/live',live);app.router.add_get('/state',state)
web.run_app(app,host='127.0.0.1',port=args.port,print=None)
