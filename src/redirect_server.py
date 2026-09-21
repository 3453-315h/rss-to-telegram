#  RSS to Telegram Bot
#  Copyright (C) 2022-2024  Rongrong <i@rong.moe>
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU Affero General Public License as
#  published by the Free Software Foundation, either version 3 of the
#  License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU Affero General Public License for more details.
#
#  You should have received a copy of the GNU Affero General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.

from __future__ import annotations
from typing import Optional

from aiohttp import web

from . import log

logger = log.getLogger('RSStT.redirect_server')


async def redirect(_):
    return web.HTTPFound('https://github.com/Rongronggg9/RSS-to-Telegram-Bot')


async def healthz(_: web.Request) -> web.Response:
    """Liveness probe returning HTTP 200."""
    return web.json_response({'status': 'ok'})


async def readyz(_: web.Request) -> web.Response:
    """Readiness probe checking database connectivity and Telegram client state."""
    checks = {}
    is_ready = True

    # Database connectivity check
    try:
        from tortoise import Tortoise
        conn = Tortoise.get_connection('default')
        await conn.execute_query('SELECT 1')
        checks['database'] = 'ok'
    except Exception as e:
        checks['database'] = f'error: {e}'
        is_ready = False

    # Telegram client connectivity check
    try:
        from . import env
        if getattr(env, 'bot', None) is not None:
            connected = env.bot.is_connected()
            checks['telegram'] = 'ok' if connected else 'disconnected'
            if not connected:
                is_ready = False
        else:
            checks['telegram'] = 'uninitialized'
    except Exception as e:
        checks['telegram'] = f'error: {e}'

    status_code = 200 if is_ready else 503
    return web.json_response({'status': 'ready' if is_ready else 'unready', 'checks': checks}, status=status_code)


app = web.Application()
app.add_routes([
    web.get('/healthz', healthz),
    web.get('/livez', healthz),
    web.get('/readyz', readyz),
    web.route('*', '/{tail:.*}', redirect),
])


runner: Optional[web.AppRunner] = None


async def run(port: Optional[int] = 5000):
    global runner
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, port=port)
    await site.start()
    logger.info(f'Redirect server listening on port {port}')


async def cleanup():
    global runner
    if runner:
        try:
            await runner.cleanup()
        except Exception as e:
            logger.warning('Failed to cleanup redirect server:', exc_info=e)
        finally:
            runner = None


if __name__ == '__main__':
    web.run_app(app)
