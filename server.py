# -*- coding: utf-8 -*-
"""========================================================
   سيرفر صراع النجاة 3D — Python Edition (online multiplayer)
   التشغيل:
       pip install python-socketio aiohttp
       python server.py
   ========================================================
"""
import asyncio
import random
import time
import socketio
from aiohttp import web

WORLD = 220
import os
BASE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get('PORT', 3000))
COLORS = [0xc62828, 0xad1457, 0x6a1b9a, 0x4527a0, 0x1565c0,
          0x00838f, 0x2e7d32, 0xef6c00, 0xd84315, 0x00695c]

players = {}          # sid -> player dict
game_state = 'lobby'  # lobby | playing | ended
zone = {'cx': 0, 'cz': 0, 'r': WORLD * 0.9, 'targetR': WORLD * 0.9, 'speed': 0, 'active': False}
zone_tasks = []
reset_task = None

sio = socketio.AsyncServer(cors_allowed_origins='*', async_mode='aiohttp')
app = web.Application()
sio.attach(app)
async def index(request):
    return web.FileResponse(os.path.join(BASE, 'public', 'index.html'))

app.router.add_get('/', index)
app.router.add_static('/', path=os.path.join(BASE, 'public'), name='static')


def spawn_point():
    for _ in range(60):
        x = random.uniform(-(WORLD - 25), WORLD - 25)
        z = random.uniform(-(WORLD - 25), WORLD - 25)
        ok = True
        for p in players.values():
            if p['alive'] and ((p['x'] - x) ** 2 + (p['z'] - z) ** 2) ** 0.5 < 20:
                ok = False
                break
        if ok:
            return x, z
    return 0.0, 0.0


def alive_count():
    return len([p for p in players.values() if p['alive']])


def players_payload():
    return [{'id': p['id'], 'name': p['name'], 'x': p['x'], 'y': p['y'], 'z': p['z'],
             'yaw': p['yaw'], 'pitch': p['pitch'], 'hp': p['hp'], 'kills': p['kills'],
             'alive': p['alive'], 'color': p['color'], 'weapon': p['weapon'],
             'crouch': p['crouch'], 'moving': p['moving']} for p in players.values()]


async def broadcast_players():
    await sio.emit('players', players_payload())


async def zone_tick():
    """تقليص المنطقة كل 50ms"""
    global zone
    while True:
        await asyncio.sleep(0.05)
        if game_state == 'playing' and zone['active'] and zone['r'] > zone['targetR']:
            zone['r'] = max(zone['targetR'], zone['r'] - zone['speed'] * 0.05)
            await sio.emit('zone', zone)


async def zone_damage_tick():
    """ضرر المنطقة كل ثانية"""
    while True:
        await asyncio.sleep(1)
        if game_state != 'playing':
            continue
        changed = False
        for sid, p in list(players.items()):
            if not p['alive']:
                continue
            d = ((p['x'] - zone['cx']) ** 2 + (p['z'] - zone['cz']) ** 2) ** 0.5
            if d > zone['r']:
                p['hp'] -= 6
                changed = True
                await sio.emit('damaged', {'hp': p['hp'], 'from': 'المنطقة'}, room=sid)
                if p['hp'] <= 0:
                    p['hp'] = 0
                    p['alive'] = False
                    await sio.emit('kill', {'victimId': sid, 'victimName': p['name'],
                                            'killerId': None, 'killerName': 'المنطقة'})
                    await check_win()
        if changed:
            await broadcast_players()


def schedule_zone():
    global zone
    zone = {'cx': 0, 'cz': 0, 'r': WORLD * 0.9, 'targetR': WORLD * 0.9, 'speed': 0, 'active': False}

    async def phase(target_r, speed, msg, delay):
        await asyncio.sleep(delay)
        if game_state != 'playing':
            return
        zone['active'] = True
        zone['speed'] = speed
        zone['targetR'] = target_r
        await sio.emit('zone', zone)
        await sio.emit('announce', msg)

    zone_tasks.clear()
    zone_tasks.append(asyncio.create_task(phase(55, 2.2, '⚠️ المنطقة الآمنة بدأت تتقلص!', 40)))
    zone_tasks.append(asyncio.create_task(phase(28, 2.6, '⚠️ المنطقة تتقلص أكثر!', 85)))
    zone_tasks.append(asyncio.create_task(phase(10, 3.0, '⚠️ المراحل الأخيرة!', 135)))


async def start_game():
    global game_state
    if game_state == 'playing':
        return
    game_state = 'playing'
    for p in players.values():
        x, z = spawn_point()
        p.update(x=x, y=0, z=z, hp=100, kills=0, alive=True,
                 yaw=random.uniform(0, 6.28), pitch=0)
    schedule_zone()
    await broadcast_players()
    await sio.emit('start')
    await sio.emit('announce', f'⚔️ {alive_count()} مقاتل انزلوا في الجزيرة — انج بنفسك!')
    print('Game started with', alive_count(), 'players')


async def end_game(winner_id):
    global game_state, reset_task
    game_state = 'ended'
    winner = players.get(winner_id)
    await sio.emit('gameover', {'winnerId': winner_id,
                                'winnerName': winner['name'] if winner else '???'})
    print('Winner:', winner['name'] if winner else 'none')

    async def reset():
        global game_state
        await asyncio.sleep(9)
        game_state = 'lobby'
        await sio.emit('backtolobby')

    if reset_task:
        reset_task.cancel()
    reset_task = asyncio.create_task(reset())


async def check_win():
    if game_state != 'playing':
        return
    alive = [p for p in players.values() if p['alive']]
    if len(players) > 1 and len(alive) == 1:
        await end_game(alive[0]['id'])


@sio.event
async def connect(sid, environ):
    print('Connected:', sid)


@sio.event
async def join(sid, name):
    global game_state
    name = str(name or 'مقاتل')[:14]
    x, z = spawn_point()
    players[sid] = {
        'id': sid, 'name': name, 'x': x, 'y': 0, 'z': z,
        'yaw': 0, 'pitch': 0, 'hp': 100, 'kills': 0, 'alive': True,
        'color': COLORS[len(players) % len(COLORS)],
        'weapon': 0, 'crouch': False, 'moving': False,
        'last_hit': 0.0
    }
    await sio.emit('init', {'id': sid, 'world': WORLD, 'players': players_payload(),
                            'zone': zone, 'gameState': game_state}, room=sid)
    await sio.emit('announce', f'🎮 انضم {name} للمعركة!', skip_sid=sid)
    await broadcast_players()

    if game_state == 'lobby':
        if len(players) >= 2:
            await start_game()
        else:
            await sio.emit('announce', '⏳ انتظر لاعبين آخرين للانضمام...', room=sid)
    elif game_state == 'playing':
        players[sid]['alive'] = False
        players[sid]['hp'] = 0
        await sio.emit('spectate', room=sid)
        await broadcast_players()


@sio.event
async def state(sid, s):
    p = players.get(sid)
    if not p or not s:
        return
    p['x'] = max(-WORLD, min(WORLD, float(s.get('x') or 0)))
    p['y'] = max(0, min(30, float(s.get('y') or 0)))
    p['z'] = max(-WORLD, min(WORLD, float(s.get('z') or 0)))
    p['yaw'] = float(s.get('yaw') or 0)
    p['pitch'] = float(s.get('pitch') or 0)
    p['crouch'] = bool(s.get('crouch'))
    p['moving'] = bool(s.get('moving'))
    p['weapon'] = int(s.get('weapon') or 0)
    payload = {'id': sid, 'x': p['x'], 'y': p['y'], 'z': p['z'],
               'yaw': p['yaw'], 'pitch': p['pitch'], 'crouch': p['crouch'],
               'moving': p['moving'], 'weapon': p['weapon']}
    await sio.emit('st', payload, skip_sid=sid)


@sio.event
async def shoot(sid, d):
    p = players.get(sid)
    if not p or not p['alive'] or not d:
        return
    await sio.emit('shoot', {'id': sid, 'ox': d.get('ox'), 'oy': d.get('oy'), 'oz': d.get('oz'),
                             'dx': d.get('dx'), 'dy': d.get('dy'), 'dz': d.get('dz')}, skip_sid=sid)


@sio.event
async def hit(sid, d):
    p = players.get(sid)
    t = players.get(d.get('target')) if d else None
    if not p or not t or not p['alive'] or not t['alive'] or game_state != 'playing':
        return
    now = time.time()
    if now - p['last_hit'] < 0.09:  # منع الغش السريع
        return
    p['last_hit'] = now
    dmg = max(1, min(100, float(d.get('dmg') or 10)))
    t['hp'] -= dmg
    await sio.emit('damaged', {'hp': max(0, t['hp']), 'from': p['name']}, room=t['id'])
    if t['hp'] <= 0:
        t['hp'] = 0
        t['alive'] = False
        p['kills'] += 1
        await sio.emit('kill', {'victimId': t['id'], 'victimName': t['name'],
                                'killerId': p['id'], 'killerName': p['name']})
        await broadcast_players()
        await check_win()
    else:
        await broadcast_players()


@sio.event
async def restart(sid):
    if game_state == 'lobby':
        await start_game()


@sio.event
async def disconnect(sid):
    p = players.pop(sid, None)
    if p:
        await sio.emit('announce', f'👋 خرج {p["name"]}')
        await sio.emit('leave', sid)
        await broadcast_players()
        await check_win()
    print('Disconnected:', sid)


async def on_startup(app):
    asyncio.create_task(zone_tick())
    asyncio.create_task(zone_damage_tick())

app.on_startup.append(on_startup)

if __name__ == '__main__':
    print('🎮 صراع النجاة 3D (بايثون) يشتغل على: http://localhost:%d' % PORT)
    web.run_app(app, host='0.0.0.0', port=PORT)
