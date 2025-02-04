import time
import json
import asyncio
import dataclasses

from typing import Optional, Union
from dataclasses import dataclass
from amiyabot.network.httpRequests import http_requests, ResponseException
from amiyabot.builtin.message import Message
from amiyabot.builtin.messageChain import Chain
from amiyabot.adapters import BotAdapterProtocol, ManualCloseException, HANDLER_TYPE

from .package import package_kook_message, RolePermissionCache
from .builder import build_message_send, KOOKMessageCallback, log


class KOOKBotInstance(BotAdapterProtocol):
    def __init__(self, appid: str, token: str):
        super().__init__(appid, token)

        self.ws_url = ''
        self.base_url = 'https://www.kookapp.cn/api/v3'
        self.headers = {'Authorization': f'Bot {token}'}
        self.connection = None

        self.pong = 0
        self.last_sn = 0

        self.bot_name = 'kook_bot'

    def __str__(self):
        return 'KOOK'

    @property
    def __still_alive(self):
        return self.keep_run and self.connection

    async def connect(self, private: bool, handler: HANDLER_TYPE):
        me_req = await self.get_request('/user/me')
        if me_req:
            self.appid = me_req['data']['id']
            self.bot_name = me_req['data']['username']

        while self.keep_run:
            await self.__connect(handler)
            await asyncio.sleep(10)

    async def __connect(self, handler: HANDLER_TYPE):
        try:
            if not self.ws_url:
                log.info(f'requesting appid {self.appid} gateway')

                resp = await self.get_request('/gateway/index', {'compress': 0})
                if not resp:
                    raise ManualCloseException

                self.ws_url = resp['data']['url']

            log.info(f'connecting({self.appid})...')

            async with self.get_websocket_connection(self.appid, self.ws_url) as websocket:
                self.connection = websocket

                while self.__still_alive:
                    await asyncio.sleep(0)

                    recv = await websocket.recv()
                    payload = WSPayload(**json.loads(recv))

                    if payload.sn is not None:
                        self.last_sn = payload.sn

                    if payload.s == 0:
                        asyncio.create_task(handler('event', payload.d))

                    if payload.s == 1:
                        if payload.d['code'] != 0:
                            self.ws_url = ''
                            self.last_sn = 0
                            raise ManualCloseException

                        log.info(f'connected({self.appid}): {self.bot_name}')

                        if self.last_sn:
                            log.info(f'resuming({self.appid})...')
                            await self.connection.send(WSPayload(4, sn=self.last_sn).to_json())

                        self.session = payload.d['session_id']
                        asyncio.create_task(self.heartbeat_interval())

                    if payload.s == 3:
                        self.pong = 1

                    if payload.s == 5:
                        self.ws_url = ''
                        self.last_sn = 0
                        await self.close_connection()

                    if payload.s == 6:
                        log.info(f'resume({self.appid}) done.')

                    if payload.sn:
                        self.last_sn = payload.sn

        finally:
            await self.close_connection()

    async def heartbeat_interval(self):
        sec = 0
        while self.__still_alive:
            await asyncio.sleep(1)
            sec += 1
            if sec >= 30:
                sec = 0
                await self.connection.send(WSPayload(2, sn=self.last_sn).to_json())

                asyncio.create_task(self.wait_heartbeat())

    async def wait_heartbeat(self):
        sec = 0
        while self.pong == 0 and self.__still_alive:
            await asyncio.sleep(1)
            sec += 1
            if sec >= 30:
                await self.close_connection()
        self.pong = 0

    async def close_connection(self):
        if self.connection:
            await self.connection.close()
        self.connection = None

    async def record_role_list(self, guild_id: str):
        if guild_id in RolePermissionCache.cache_create_time:
            if time.time() - RolePermissionCache.cache_create_time[guild_id] < 10:
                return

        res = await self.get_request('/guild-role/list', {'guild_id': guild_id}, ignore_error=True)
        if res:
            roles = {}
            for item in res['data']['items']:
                roles[item['role_id']] = item['permissions']

            RolePermissionCache.guild_role[guild_id] = roles

        elif guild_id in RolePermissionCache.guild_role:
            del RolePermissionCache.guild_role[guild_id]

        RolePermissionCache.cache_create_time[guild_id] = time.time()

    async def close(self):
        log.info(f'closing {self}(appid {self.appid})...')
        self.keep_run = False
        await self.close_connection()

    async def package_message(self, event: str, message: dict):
        if message['type'] != 255:
            guild_id = message['extra'].get('guild_id', '')
            if guild_id:
                await self.record_role_list(guild_id)

        return await package_kook_message(self, message)

    async def send_chain_message(self, chain: Chain, is_sync: bool = False):
        message = await build_message_send(self, chain)
        callback = []

        url = '/direct-message/create' if chain.data.is_direct else '/message/create'

        for item in [message]:
            payload = {
                'target_id': chain.data.user_id if chain.data.is_direct else chain.data.channel_id,
                **item,
            }
            if chain.reference:
                payload['quote'] = chain.data.message_id

            callback.append(KOOKMessageCallback(self, await self.post_request(url, payload)))

        return callback

    async def build_active_message_chain(self, chain: Chain, user_id: str, channel_id: str, direct_src_guild_id: str):
        data = Message(self)

        data.user_id = user_id
        data.channel_id = channel_id

        if not channel_id and not user_id:
            raise TypeError('send_message() missing argument: "channel_id" or "user_id"')

        if not channel_id and user_id:
            data.is_direct = True

        message = Chain(data)
        message.chain = chain.chain
        message.builder = chain.builder

        return message

    async def recall_message(self, message_id: Union[str, int], target_id: Union[str, int] = None):
        await self.post_request('/message/delete', {'msg_id': message_id})

    async def get_request(self, url: str, params: Optional[dict] = None, **kwargs):
        return self.__check_response(
            await http_requests.get(self.base_url + url, params, headers=self.headers, **kwargs)
        )

    async def post_request(self, url: str, payload: Optional[dict] = None, **kwargs):
        return self.__check_response(
            await http_requests.post(self.base_url + url, payload, headers=self.headers, **kwargs)
        )

    @staticmethod
    def __check_response(response_text: Optional[str]) -> Optional[dict]:
        if response_text is None:
            return None

        try:
            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            raise ResponseException(-1, repr(e)) from e

        if 'code' in data and data['code'] != 0:
            raise ResponseException(**data)

        return data


@dataclass
class WSPayload:
    s: int
    d: Optional[dict] = None
    sn: Optional[int] = None
    extra: Optional[dict] = None

    def to_json(self):
        return json.dumps(dataclasses.asdict(self), ensure_ascii=False)
